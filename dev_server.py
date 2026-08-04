"""DEV test harness — a multi-user dashboard that speaks the REAL webhook path.

One page shows several WhatsApp-style widgets, each an independent user (its own
phone). Each widget POSTs Meta Cloud API-shaped payloads to `/webhook` — exactly
like real WhatsApp. Ingress = app.api.whatsapp.parse_webhook; routing =
app.router.route. Only the `run_agent` runtime here is harness-specific
(Kimi K2 on Bedrock via boto3).

- Tools bound: Flows 1-3 (register, find kitchens, view menu, create/add/view cart).
- DB: local SQLite poc.db  (seed first:  python dev_seed.py)

Run:  python dev_seed.py
      uvicorn dev_server:app --port 8000     # open http://127.0.0.1:8000
"""

import os
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./poc.db")

import asyncio
import re

import boto3
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

import app.tools.customer_tools  # noqa: F401  (registers the finish_registration resume handler)
from app.agents.prompts import CUSTOMER_PROMPT
from app.api.whatsapp import normalize_phone, parse_webhook, verify_challenge
from app.router import route
from app.tools.customer_tools import (
    add_item_to_order,
    create_order,
    find_nearby_kitchens,
    get_customer_profile,
    register_customer,
    view_cart,
    view_chef_menu,
)
from app.tools.pause import Pause, clear_pending, get_pending

KIMI = "moonshotai.kimi-k2.5"   # non-thinking Kimi — ~5x faster than kimi-k2-thinking, same tool support
WEBHOOK_VERIFY_TOKEN = "homatri_verify"
_br = boto3.Session(profile_name="homatri-bedrock").client("bedrock-runtime", region_name="us-east-1")

TOOLS = {t.name: t for t in [
    get_customer_profile, register_customer, find_nearby_kitchens, view_chef_menu,
    create_order, add_item_to_order, view_cart,
]}
CONVOS: dict[str, list] = {}  # phone -> [{role, text}]  (text-only history per phone)


def _toolconfig() -> dict:
    """Full JSON schema per tool (from the Pydantic args_schema) so nested/list
    args like create_order.items=[{item_id, quantity}] reach Kimi intact."""
    specs = []
    for name, t in TOOLS.items():
        schema = t.args_schema.model_json_schema()
        specs.append({"toolSpec": {"name": name, "description": t.description,
            "inputSchema": {"json": schema}}})
    return {"tools": specs}


def _clean(t: str) -> str:
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.DOTALL)
    return t.replace("<think>", "").replace("</think>", "").strip()


def _window_messages(hist: list, n: int = 4) -> list:
    """Context window: last `n` USER + last `n` ASSISTANT turns (chronological).

    Returned as valid alternating Bedrock messages — starts with a user turn,
    consecutive same-role turns merged. (In-memory stand-in for the real
    DB-backed Context Assembler over conversation_messages.)
    """
    user_idx = [i for i, h in enumerate(hist) if h["role"] == "user"][-n:]
    bot_idx = [i for i, h in enumerate(hist) if h["role"] == "assistant"][-n:]
    windowed = [hist[i] for i in sorted(set(user_idx) | set(bot_idx))]
    while windowed and windowed[0]["role"] != "user":
        windowed.pop(0)
    msgs: list = []
    for h in windowed:
        if msgs and msgs[-1]["role"] == h["role"]:
            msgs[-1]["content"][0]["text"] += "\n" + h["text"]
        else:
            msgs.append({"role": h["role"], "content": [{"text": h["text"]}]})
    return msgs


async def run_agent(phone: str, user_text: str) -> dict:
    """The (harness) agent runtime: Kimi K2 + the bound tools + text-only history."""
    hist = CONVOS.setdefault(phone, [])
    hist.append({"role": "user", "text": user_text})
    system = [{"text": CUSTOMER_PROMPT + (
        f"\n\nThe current customer's phone number is {phone}. Always pass this exact phone to any tool "
        f"needing customer_phone. At the start of a conversation call get_customer_profile to check if they "
        f"are registered. If NOT_FOUND or INCOMPLETE, ask for their name and full delivery address, then call "
        f"register_customer(customer_phone, name, delivery_address). If get_customer_profile says they are "
        f"already registered, do NOT call register_customer again."
        f"\n\nGROUNDING — this is critical:"
        f"\n- You have ZERO knowledge of any kitchen, dish, price, or menu. Every fact you state MUST come from a "
        f"tool result you received THIS turn. NEVER write a menu, dish name, or price from your own memory."
        f"\n- NEVER invent or guess IDs, phone numbers, or item codes. The tools take plain NAMES — pass them."
        f"\n\nFlow — browse & order (refer to kitchens and dishes by NAME):"
        f"\n- To find kitchens: find_nearby_kitchens(latitude, longitude). For a registered customer, use the "
        f"saved location from get_customer_profile — don't ask for a pin again."
        f"\n- When the customer picks a kitchen, call view_chef_menu(kitchen=<the kitchen name they said>). "
        f"If they say 'the 3rd one', map it to that kitchen's name from the list you just showed."
        f"\n- To place an order: create_order(customer_phone, kitchen=<name>, items=[{{'dish_name': <name>, "
        f"'quantity': N}}]). To change the cart: add_item_to_order(customer_phone, items=[{{'dish_name': <name>, "
        f"'quantity': N}}]) — quantity is the FINAL desired count, not how many to add. Show the cart with "
        f"view_cart(customer_phone)."
        f"\n- If a tool says a name matches nothing or is ambiguous, re-show the menu/list and ask — do not guess."
        f"\n- Payment (request_payment) is not built yet — after an order is created, tell the customer their "
        f"cart is ready and that payment is coming soon. Stop there.")}]
    messages = _window_messages(hist, n=4)   # last 4 user + last 4 agent turns
    tc = _toolconfig()
    for _ in range(6):
        r = await asyncio.to_thread(_br.converse, modelId=KIMI, system=system, messages=messages,
                                    toolConfig=tc, inferenceConfig={"maxTokens": 1500, "temperature": 0.3})
        out = r["output"]["message"]; messages.append(out)
        if r["stopReason"] == "tool_use":
            results = []
            for b in out["content"]:
                if "toolUse" in b:
                    tu = b["toolUse"]
                    print(f"[{phone}] TOOL {tu['name']}({tu['input']})", flush=True)
                    try:
                        res = await TOOLS[tu["name"]].ainvoke(tu["input"])
                    except Pause as p:
                        print(f"[{phone}]   -> PAUSE: {p.message[:80]}", flush=True)
                        hist.append({"role": "assistant", "text": p.message})
                        return {"reply": p.message, "await_location": True}
                    print(f"[{phone}]   -> {str(res)[:120]}", flush=True)
                    results.append({"toolResult": {"toolUseId": tu["toolUseId"], "content": [{"text": str(res)}]}})
            messages.append({"role": "user", "content": results})
            continue
        final = _clean("".join(b.get("text", "") for b in out["content"] if "text" in b))
        hist.append({"role": "assistant", "text": final})
        return {"reply": final, "await_location": get_pending(phone) is not None}
    return {"reply": "(the agent stopped without a reply)", "await_location": False}


app = FastAPI(title="Homaatri dev harness")


@app.get("/webhook")
def webhook_verify(req: Request):
    """Meta webhook verification handshake (GET)."""
    p = req.query_params
    ch = verify_challenge(p.get("hub.mode", ""), p.get("hub.verify_token", ""),
                          p.get("hub.challenge", ""), WEBHOOK_VERIFY_TOKEN)
    if ch is not None:
        return PlainTextResponse(ch)
    return JSONResponse({"error": "verification failed"}, status_code=403)


@app.post("/webhook")
async def webhook(req: Request):
    """Inbound message (real WhatsApp shape) → parse → check-first router → reply."""
    msg = parse_webhook(await req.json())
    if msg is None:
        return JSONResponse({"status": "ignored"})   # status callback etc.
    return JSONResponse(await route(msg, run_agent))


@app.post("/reset")
async def reset(req: Request):
    body = await req.json()
    phone = normalize_phone(body.get("phone", ""))
    clear_pending(phone); CONVOS.pop(phone, None)
    return JSONResponse({"ok": True})


@app.get("/")
def index() -> HTMLResponse:
    return HTMLResponse(PAGE)


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Homaatri — multi-user tester</title>
<style>
 *{box-sizing:border-box}
 body{margin:0;font-family:'Segoe UI',sans-serif;height:100vh;display:flex;flex-direction:column;background:#0b141a}
 #bar{background:#075e54;color:#fff;padding:8px 14px;display:flex;align-items:center;gap:10px;flex:0 0 auto}
 #bar b{font-size:16px} #bar span{opacity:.85;font-size:12px}
 #bar input{padding:5px 8px;border:0;border-radius:6px;font-size:13px;width:150px;margin-left:auto}
 #bar button{padding:6px 12px;border:0;border-radius:6px;background:#0b7d6e;color:#fff;cursor:pointer}
 #board{flex:1;display:flex;gap:10px;padding:10px;overflow-x:auto;align-items:stretch}
 .widget{flex:0 0 330px;display:flex;flex-direction:column;background:#efeae2;border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.35)}
 .wh{background:#128c7e;color:#fff;padding:8px 10px;display:flex;align-items:center;gap:6px}
 .wh .ph{font-weight:600;flex:1;font-size:14px}
 .wh button{padding:3px 8px;border:0;border-radius:5px;background:#0b6b60;color:#fff;cursor:pointer;font-size:11px}
 .log{flex:1;overflow-y:auto;padding:10px;display:flex;flex-direction:column;gap:6px;background:#e5ddd5}
 .msg{max-width:86%;padding:7px 10px;border-radius:8px;white-space:pre-wrap;line-height:1.3;font-size:13px}
 .me{align-self:flex-end;background:#dcf8c6} .bot{align-self:flex-start;background:#fff}
 .sys{align-self:center;color:#556;font-size:11px;text-align:center}
 .f{display:flex;gap:6px;padding:8px;background:#f0f0f0;align-items:center}
 .f .m{flex:1;padding:8px;border:1px solid #ccc;border-radius:18px;outline:none;font-size:13px}
 .f button{padding:8px 12px;border:0;border-radius:18px;background:#075e54;color:#fff;cursor:pointer}
 .f button:disabled{opacity:.5}
 .loc.hot{background:#e8a020;animation:pulse 1s infinite}
 @keyframes pulse{50%{opacity:.6}}
</style></head><body>
 <div id="bar"><b>Homaatri</b><span>multi-user tester — each widget is an independent user</span>
   <input id="newphone" placeholder="new phone…"><button id="add">+ Add user</button></div>
 <div id="board"></div>
<script>
 const board=document.getElementById('board');
 function createWidget(phone){
   const w=document.createElement('div'); w.className='widget';
   w.innerHTML=`<div class="wh"><span class="ph">📱 ${phone}</span>
       <button class="rst">reset</button><button class="cls">✕</button></div>
     <div class="log"><div class="msg sys">User ${phone}. Try "hi" (to register a new user) or "get me nearby kitchens".</div></div>
     <form class="f"><button type="button" class="loc" title="share location">📍</button>
       <input class="m" autocomplete="off" placeholder="Message…"><button class="snd">Send</button></form>`;
   const log=w.querySelector('.log'),f=w.querySelector('.f'),m=w.querySelector('.m'),
         snd=w.querySelector('.snd'),loc=w.querySelector('.loc');
   const add=(cls,txt)=>{const d=document.createElement('div');d.className='msg '+cls;d.textContent=txt;log.appendChild(d);log.scrollTop=log.scrollHeight;return d;};
   async function send(waMsg,label){add('me',label);snd.disabled=true;loc.disabled=true;
     const t=add('bot','…thinking (Kimi K2)…');
     const payload={entry:[{changes:[{value:{messages:[waMsg]}}]}]};
     try{const r=await fetch('/webhook',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
         const j=await r.json();t.textContent=j.reply||'(no reply)';loc.classList.toggle('hot',!!j.await_location);}
     catch(e){t.textContent='Error: '+e;}
     snd.disabled=false;loc.disabled=false;m.focus();}
   f.onsubmit=(e)=>{e.preventDefault();const text=m.value.trim();if(!text)return;m.value='';
     send({from:phone,type:'text',text:{body:text}},text);};
   loc.onclick=()=>{const FIXED={from:phone,type:'location',location:{latitude:19.1235,longitude:73.0012}};
     if(!navigator.geolocation){return send(FIXED,'📍 shared location (test coords)');}
     loc.disabled=true;
     navigator.geolocation.getCurrentPosition(
       p=>{loc.disabled=false;const la=p.coords.latitude,lo=p.coords.longitude;
         send({from:phone,type:'location',location:{latitude:la,longitude:lo}},`📍 my real location (${la.toFixed(5)}, ${lo.toFixed(5)})`);},
       e=>{loc.disabled=false;send(FIXED,'📍 test coords (geolocation blocked: '+e.message+')');},
       {enableHighAccuracy:true,timeout:10000});};
   w.querySelector('.rst').onclick=async()=>{await fetch('/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone})});
     log.innerHTML='';add('sys','reset');loc.classList.remove('hot');};
   w.querySelector('.cls').onclick=()=>w.remove();
   board.appendChild(w);
 }
 ['7416767453','7000000000','9111111111'].forEach(createWidget);
 document.getElementById('add').onclick=()=>{const el=document.getElementById('newphone');const v=el.value.trim();if(v){createWidget(v);el.value='';}};
</script></body></html>"""
