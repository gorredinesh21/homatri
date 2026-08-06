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

import sys
# Windows consoles default to cp1252, which can't encode ₹/emoji in our debug prints —
# force UTF-8 so a logging print never crashes a request (errors='replace' as a backstop).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import asyncio
import re

import boto3
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

import app.tools.customer_tools  # noqa: F401  (registers the finish_registration + confirm_payment resume handlers)
import app.tools.topup  # noqa: F401  (registers the resolve_topup_counter + confirm_topup_payment resume handlers)
from sqlalchemy import delete, select

from app.agents.agents import chef_agent, customer_agent, driver_agent
from app.agents.prompts import CHEF_PROMPT, CUSTOMER_PROMPT, DRIVER_PROMPT
from app.api.whatsapp import normalize_phone, parse_webhook, verify_challenge
from app.db.session import SessionFactory, transaction
from app.executors.master import execute_conversation_message_insert
from app.models.chef import ChefProfile
from app.models.customer import CustomerOrder
from app.models.driver import DriverProfile
from app.models.shared import ConversationMessage
from app.models.system import SystemHitlSession, SystemOutboundQueue
from app.router import route
from app.tools.cancellation import clear_cancellation
from app.tools.common import resolve_time_pool
from app.tools.dietary import clear_negotiation
from app.tools.driver_tools import clear_driver_query
from app.tools.topup import clear_topup
from app.tools.master_tools import _run_cutoff_batch
from app.tools.pause import RESUME_HANDLERS, Pause, clear_pending, get_pending
from dev_batch import CHEFS as BATCH_CHEFS, DRIVERS as BATCH_DRIVERS, build_roster, seed_batch

KIMI = "moonshotai.kimi-k2.5"   # non-thinking Kimi — ~5x faster than kimi-k2-thinking, same tool support
WEBHOOK_VERIFY_TOKEN = "homatri_verify"
_br = boto3.Session(profile_name="homatri-bedrock").client("bedrock-runtime", region_name="us-east-1")

# Each agent's bound tools ARE its harness toolset (single source of truth).
CUSTOMER_TOOLS = customer_agent.tool_map
CHEF_TOOLS = chef_agent.tool_map
DRIVER_TOOLS = driver_agent.tool_map
CONVOS: dict[str, list] = {}  # phone -> [{role, text}]  (text-only history per phone)


def _toolconfig(tools: dict) -> dict:
    """Full JSON schema per tool (from the Pydantic args_schema) so nested/list
    args like create_order.items=[{dish_name, quantity}] reach Kimi intact."""
    specs = []
    for name, t in tools.items():
        schema = t.args_schema.model_json_schema()
        specs.append({"toolSpec": {"name": name, "description": t.description,
            "inputSchema": {"json": schema}}})
    return {"tools": specs}


async def _role_for(phone: str) -> str:
    """Seeded chef phone -> CHEF, seeded driver phone -> DRIVER, else CUSTOMER."""
    async with SessionFactory() as session:
        if await session.get(ChefProfile, phone) is not None:
            return "CHEF"
        if await session.get(DriverProfile, phone) is not None:
            return "DRIVER"
    return "CUSTOMER"


def _driver_extra(phone: str) -> str:
    return (
        f"\n\nThe current driver's phone number is {phone}. Always pass this exact phone to any tool "
        f"needing driver_phone. On an inbound message call get_driver_profile. To go on/off shift use "
        f"update_duty_status. To see the route call get_driver_route — it shows ONLY the next stop. "
        f"When the driver says they've picked up at the kitchen, call confirm_pickup(driver_phone). When "
        f"they've delivered, call confirm_delivery(driver_phone) — if they name a specific apartment/area "
        f"(out of order) pass it as `location`, and pass any undelivered order ids as `undelivered_ids`. "
        f"If they ask whether the food is ready, call ask_chef_status(driver_phone). "
        f"If they CAN'T FIND an address, call report_address_issue(driver_phone) — it asks the customer for a "
        f"fresh location pin and sends it to the driver when they share. "
        f"LAST RESORT if stuck: escalate_to_admin(source_role='DRIVER', escalation_type='STUCK', summary=..., order_id=...). "
        f"These tools RETURN the next stop's details — just relay that to the driver. Keep replies short."
    )


def _chef_extra(phone: str) -> str:
    return (
        f"\n\nThe current chef's phone number is {phone}. Always pass this exact phone to any tool "
        f"needing chef_phone. On an inbound message call get_chef_profile to identify the kitchen. "
        f"To show what to cook, call get_chef_batch(chef_phone) — it lists each order + items and a cook "
        f"summary. To mark a dish out of/back in stock: toggle_dish_stock(chef_phone, dish, is_available). "
        f"To set how many portions you can make: set_daily_capacity(...). When an order is packed, call "
        f"mark_order_ready(chef_phone, order_id) — it moves the order to PACKED and notifies the driver. "
        f"If a customer DIETARY REQUEST arrives (e.g. 'no garlic'), decide with "
        f"respond_to_dietary_request(chef_phone, decision='accept'|'reject'|'counter', counter_note=<if counter>). "
        f"If a CANCELLATION request arrives for an order you're cooking, decide with "
        f"respond_to_cancellation(chef_phone, decision='approve'|'deny'). "
        f"If an ADD-ON request arrives (customer wants to ADD extra dishes to a paid order), decide with "
        f"respond_to_topup_request(chef_phone, decision='accept'|'reject'|'counter'). If you can't do the full "
        f"quantity, use decision='counter' with counter_note=<your message to the customer, e.g. 'only 1 paneer "
        f"left'> AND counter_items=[{{'dish_name': <name>, 'quantity': N}}] (the amount you CAN add). "
        f"If a DRIVER is waiting and asks how long, reply with respond_to_driver_query(chef_phone, reply) "
        f"(e.g. reply='5 more minutes' or 'ready now'). "
        f"LAST RESORT if stuck: escalate_to_admin(source_role='CHEF', escalation_type='STUCK', summary=..., order_id=...). "
        f"Refer to dishes by name and orders by their ord_ id from get_chef_batch. Keep replies short."
    )


def _customer_extra(phone: str) -> str:
    return (
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
        f"\n- LOCATION: once a customer has shared a location pin they are REGISTERED — NEVER ask for a pin "
        f"again. Only find_nearby_kitchens needs coordinates; if you need them, get them from "
        f"get_customer_profile (it returns the saved latitude/longitude). Never ask the user for a pin to show "
        f"a menu or take an order."
        f"\n- To show a specific kitchen's menu, ALWAYS call view_chef_menu(kitchen=<the name>). It needs ONLY "
        f"the kitchen name — NOT a location. Do NOT call find_nearby_kitchens just to open a named kitchen's menu."
        f"\n- If they say 'the 3rd one', map it to that kitchen's name from the list you last showed."
        f"\n- To place an order: create_order(customer_phone, kitchen=<name>, items=[{{'dish_name': <name>, "
        f"'quantity': N}}]) — this needs ONLY the kitchen + dish names, NEVER a location. To change the cart: "
        f"add_item_to_order(customer_phone, items=[{{'dish_name': <name>, "
        f"'quantity': N}}]) — quantity is the FINAL desired count, not how many to add. Show the cart with "
        f"view_cart(customer_phone)."
        f"\n- If a tool says a name matches nothing or is ambiguous, re-show the menu/list and ask — do not guess."
        f"\n- To take payment, call request_payment(customer_phone). It sends a payment link and waits for the "
        f"customer to pay. NEVER tell the customer their payment succeeded or their order is confirmed — you do "
        f"NOT know that; the system sends the confirmation automatically once payment completes. If they ask "
        f"'did it go through?', say you're still waiting for the payment and ask them to use the link."
        f"\n- To check an EXISTING (placed) order — 'where's my order?', 'order status', 'my current orders' — "
        f"call get_order_status(customer_phone). That's different from view_cart (the pre-payment cart)."
        f"\n- If the customer asks for ANY custom/dietary change to a placed order ('no garlic', 'less spicy', "
        f"'no onion', 'extra spicy'), you MUST call request_dietary_change(customer_phone, note) — that tool is "
        f"the ONLY way to reach the kitchen. NEVER say you've sent or asked the kitchen unless you ACTUALLY "
        f"called this tool this turn. After it returns, relay its message. Do not promise the change is done."
        f"\n- If the customer wants to ADD EXTRA DISHES to an order they ALREADY PLACED and PAID for ('add 2 more "
        f"paneer', 'can I also get a lassi'), call request_order_topup(customer_phone, items=[{{'dish_name': "
        f"<name>, 'quantity': N}}]). The kitchen must approve and the customer pays the extra amount before the "
        f"items are added — relay the tool's message; NEVER say the items were added yourself. (This is only for a "
        f"PAID order; for a cart that hasn't been paid yet, use add_item_to_order instead.)"
        f"\n- To CANCEL an order, call cancel_order(customer_phone, reason). If it's already cooking, the kitchen "
        f"is asked to approve — tell the customer you're checking. Relay the tool's message; don't decide yourself."
        f"\n- To leave FEEDBACK on a delivered order (ratings/comment), call "
        f"submit_order_review(customer_phone, chef_rating, driver_rating, comment)."
        f"\n- LAST RESORT: if you're genuinely stuck and can't help after trying, call "
        f"escalate_to_admin(source_role='CUSTOMER', escalation_type='STUCK', summary=<what's wrong>, order_id=<if any>)."
    )


def _clean(t: str) -> str:
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.DOTALL)
    return t.replace("<think>", "").replace("</think>", "").strip()


def _window_messages(hist: list, n: int = 6) -> list:
    """Context window from the FULL chat log: last `n` human-side + last `n` assistant turns.

    A hist entry's role is one of: 'user' (what the person typed), 'assistant' (the agent's
    own reply), or 'received' (a message PUSHED to this person — a cross-domain notification
    from the outbound queue, folded in by `_fold_received`). 'user' and 'received' are both the
    human/incoming side, so a bare "yes/accept" sits right under the request it answers.

    Returned as valid alternating Bedrock messages — first turn is 'user', consecutive
    same-role turns merged. (In-memory stand-in for the real DB-backed Context Assembler.)
    """
    assistant_idx = [i for i, h in enumerate(hist) if h["role"] == "assistant"][-n:]
    human_idx = [i for i, h in enumerate(hist) if h["role"] in ("user", "received")][-n:]
    windowed = [hist[i] for i in sorted(set(assistant_idx) | set(human_idx))]
    msgs: list = []
    for h in windowed:
        role = "assistant" if h["role"] == "assistant" else "user"
        text = h["text"] if h["role"] != "received" else f"📨 (message you received): {h['text']}"
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"][0]["text"] += "\n" + text
        else:
            msgs.append({"role": role, "content": [{"text": text}]})
    if msgs and msgs[0]["role"] != "user":       # Bedrock requires the first turn to be 'user'
        msgs.insert(0, {"role": "user", "content": [{"text": "(continuing our chat)"}]})
    return msgs


# Shared rule so every agent treats pushed notifications as answerable context.
CONTEXT_RULE = (
    "\n\nCONTEXT: The messages you receive are the recent chat — prior turns plus the current one. "
    "A line starting with '📨 (message you received)' is an incoming notification or request that was "
    "PUSHED to this user (e.g. a dietary / cancellation / add-on request, or a driver's question). When "
    "the user then replies with a short answer like 'yes', 'accept', 'reject', 'approve', 'deny', 'no', or "
    "'ok', they are answering that most recent 📨 request — call the matching responder tool (it locates the "
    "pending request by phone). Do NOT ask them to clarify what they're responding to when a recent 📨 "
    "request already makes it clear."
)

# Which outbound-queue messages have already been folded into a phone's in-memory log
# (by message_id, so it survives a DB reseed that mints fresh ids).
_folded_ids: dict[str, set] = {}


async def _fold_received(phone: str, hist: list) -> None:
    """Append any not-yet-seen pushed messages (system_outbound_queue) to this phone's log as
    'received' turns, in creation order — so the recipient's context includes what was pushed to
    them. Called right BEFORE the new inbound message so a reply lands under the request it answers.
    """
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                select(SystemOutboundQueue).where(SystemOutboundQueue.recipient_phone == phone)
                .order_by(SystemOutboundQueue.created_at, SystemOutboundQueue.message_id)
            )
        ).scalars().all()
    seen = _folded_ids.setdefault(phone, set())
    for r in rows:
        if r.message_id not in seen:
            hist.append({"role": "received", "text": r.message_text})
            seen.add(r.message_id)


async def _forget_received(phone: str) -> None:
    """Clean slate for a phone: mark every current pushed message as already-seen so a /reset
    doesn't resurrect old notifications into the fresh conversation."""
    async with SessionFactory() as session:
        ids = (
            await session.execute(
                select(SystemOutboundQueue.message_id).where(SystemOutboundQueue.recipient_phone == phone)
            )
        ).scalars().all()
    _folded_ids[phone] = set(ids)


async def _log_message(phone: str, *, actor_role: str, direction: str, source: str, text: str,
                       message_type: str = "TEXT", related_order_id: str | None = None) -> None:
    """Append one row to the unified chat log (conversation_messages)."""
    async with transaction() as session:
        await execute_conversation_message_insert(
            session, phone=phone, actor_role=actor_role, direction=direction, source=source,
            message_text=text, message_type=message_type, related_order_id=related_order_id)


async def _assemble_context(phone: str, n: int = 6) -> list:
    """Build the LLM context from conversation_messages: the last `n` INBOUND (what the user
    sent) + last `n` OUTBOUND (what the user received — agent replies AND pushes), in order.

    Mapped to Bedrock turns: INBOUND -> user; OUTBOUND+AGENT -> assistant; OUTBOUND+SYSTEM
    (a push) -> user, marked '📨 (message you received)' so a bare 'yes/accept' has its referent.
    """
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                select(ConversationMessage).where(ConversationMessage.phone == phone)
                .order_by(ConversationMessage.created_at, ConversationMessage.message_id)
            )
        ).scalars().all()
    idx_in = [i for i, r in enumerate(rows) if r.direction == "INBOUND"][-n:]
    idx_out = [i for i, r in enumerate(rows) if r.direction == "OUTBOUND"][-n:]
    keep = sorted(set(idx_in) | set(idx_out))
    msgs: list = []
    for i in keep:
        r = rows[i]
        if r.direction == "INBOUND":
            role, text = "user", (r.message_text or "")
        elif r.source == "SYSTEM":
            role, text = "user", f"📨 (message you received): {r.message_text or ''}"
        else:                                   # OUTBOUND agent reply
            role, text = "assistant", (r.message_text or "")
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"][0]["text"] += "\n" + text
        else:
            msgs.append({"role": role, "content": [{"text": text}]})
    if msgs and msgs[0]["role"] != "user":      # Bedrock requires the first turn to be 'user'
        msgs.insert(0, {"role": "user", "content": [{"text": "(continuing our chat)"}]})
    return msgs


async def run_agent(phone: str, user_text: str) -> dict:
    """The (harness) agent runtime: Kimi K2 + the sender's role-appropriate tools + DB-backed history."""
    role = await _role_for(phone)
    if role == "CHEF":
        tools, system = CHEF_TOOLS, [{"text": CHEF_PROMPT + _chef_extra(phone) + CONTEXT_RULE}]
    elif role == "DRIVER":
        tools, system = DRIVER_TOOLS, [{"text": DRIVER_PROMPT + _driver_extra(phone) + CONTEXT_RULE}]
    else:
        tools, system = CUSTOMER_TOOLS, [{"text": CUSTOMER_PROMPT + _customer_extra(phone) + CONTEXT_RULE}]

    messages = await _assemble_context(phone)   # last n sent + last n received, from conversation_messages
    tc = _toolconfig(tools)
    for _ in range(6):
        r = await asyncio.to_thread(_br.converse, modelId=KIMI, system=system, messages=messages,
                                    toolConfig=tc, inferenceConfig={"maxTokens": 1500, "temperature": 0.3})
        out = r["output"]["message"]; messages.append(out)
        if r["stopReason"] == "tool_use":
            results = []
            for b in out["content"]:
                if "toolUse" in b:
                    tu = b["toolUse"]
                    print(f"[{phone}:{role}] TOOL {tu['name']}({tu['input']})", flush=True)
                    try:
                        res = await tools[tu["name"]].ainvoke(tu["input"])
                    except Pause as p:
                        print(f"[{phone}]   -> PAUSE({p.await_type}): {p.message[:80]}", flush=True)
                        return {"reply": p.message,
                                "await_location": p.await_type == "LOCATION_PIN",
                                "await_payment": p.await_type == "PAYMENT_CONFIRM"}
                    print(f"[{phone}]   -> {str(res)[:120]}", flush=True)
                    results.append({"toolResult": {"toolUseId": tu["toolUseId"], "content": [{"text": str(res)}]}})
            messages.append({"role": "user", "content": results})
            continue
        final = _clean("".join(b.get("text", "") for b in out["content"] if "text" in b))
        note = get_pending(phone)
        at = note["await_type"] if note else None
        return {"reply": final,
                "await_location": at == "LOCATION_PIN",
                "await_payment": at == "PAYMENT_CONFIRM"}
    return {"reply": "(the agent stopped without a reply)", "await_location": False, "await_payment": False}


app = FastAPI(title="Homaatri dev harness")
app.mount("/static", StaticFiles(directory="app/static"), name="static")   # serves the mock payment page


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
    phone = msg["phone"]
    role = await _role_for(phone)
    # BOUNDARY 1 — inbound: log what the user sent.
    intext = msg.get("text") or f"(shared their location: {msg.get('location')})"
    await _log_message(phone, actor_role=role, direction="INBOUND", source="WHATSAPP", text=intext,
                       message_type="LOCATION" if msg.get("type") == "location" else "TEXT")
    result = await route(msg, run_agent)
    # BOUNDARY 2 — outbound: log the agent's reply (covers normal, paused, and resumed replies).
    reply = result.get("reply")
    if reply:
        await _log_message(phone, actor_role=role, direction="OUTBOUND", source="AGENT", text=reply)
    return JSONResponse(result)


@app.post("/pay")
async def pay(req: Request):
    """Mock gateway callback: the widget's '💳 Pay' button fires this (stands in for the
    Razorpay webhook). Resumes the paused customer thread -> payment PAID -> order CONFIRMED."""
    body = await req.json()
    phone = normalize_phone(body.get("phone", ""))
    note = get_pending(phone)
    if not note or note["await_type"] != "PAYMENT_CONFIRM":
        return JSONResponse({"reply": "No payment is pending.", "await_payment": False})
    handler = RESUME_HANDLERS[note["resume"]]
    reply = await handler(phone, {"transaction_id": f"txn_mock_{phone}"}, note["ctx"])
    clear_pending(phone)
    # Outbound: the payment-confirmation message the customer receives.
    await _log_message(phone, actor_role=await _role_for(phone), direction="OUTBOUND", source="AGENT", text=reply)
    return JSONResponse({"reply": reply, "await_payment": False})


@app.post("/cutoff")
async def cutoff(req: Request):
    """Manual cutoff trigger (stands in for the scheduler). Batches EVERY (window, date)
    that has CONFIRMED orders — so it works whatever window you tested in (lunch/dinner)."""
    async with SessionFactory() as session:
        pending = (
            await session.execute(
                select(CustomerOrder.meal_window, CustomerOrder.service_date)
                .where(CustomerOrder.status == "CONFIRMED").distinct()
            )
        ).all()
    if not pending:
        return JSONResponse({"status": "NO_ORDERS", "runs": [],
                             "message": "No confirmed orders waiting to be batched."})
    runs = []
    for window, service_date in pending:
        async with transaction() as session:
            res = await _run_cutoff_batch(session, window=window, service_date=service_date)
        runs.append({"window": window, "service_date": str(service_date), **res})
    msg = " | ".join(f"{r['window'].lower()} {r['service_date']}: {r['message']}" for r in runs)
    return JSONResponse({"status": "BATCHED", "runs": runs, "message": msg})


@app.get("/admin/queue")
async def admin_queue(req: Request):
    """The admin escalation queue — open HITL sessions waiting on ADMIN (from escalate_to_admin)."""
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                select(SystemHitlSession)
                .where(SystemHitlSession.waiting_on_role == "ADMIN", SystemHitlSession.status == "WAITING")
                .order_by(SystemHitlSession.created_at.desc())
            )
        ).scalars().all()
    return JSONResponse({"items": [
        {"id": r.session_id, "type": (r.payload or {}).get("type"), "from": (r.payload or {}).get("source_role"),
         "summary": (r.payload or {}).get("summary"), "order_id": r.order_id,
         "at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]})


@app.get("/admin/summary")
async def admin_summary(req: Request):
    """Admin oversight: order pipeline counts + kitchen availability."""
    from app.tools.master_tools import _get_kitchen_availability_summary, _get_order_pipeline_summary
    async with SessionFactory() as session:
        pipeline = await _get_order_pipeline_summary(session)
        kitchens = await _get_kitchen_availability_summary(session)
    return JSONResponse({"pipeline": pipeline["counts"], "total": pipeline["total"],
                         "kitchens_active": sum(1 for k in kitchens["kitchens"] if k["active"]),
                         "kitchens_total": len(kitchens["kitchens"])})


@app.get("/outbox")
async def outbox(req: Request):
    """The dispatched-message inbox for a phone (chef/driver) — reads system_outbound_queue."""
    phone = normalize_phone(req.query_params.get("phone", ""))
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                select(SystemOutboundQueue)
                .where(SystemOutboundQueue.recipient_phone == phone)
                .order_by(SystemOutboundQueue.created_at)
            )
        ).scalars().all()
    return JSONResponse({"messages": [
        {"role": r.recipient_role, "text": r.message_text,
         "at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]})


@app.post("/reset")
async def reset(req: Request):
    body = await req.json()
    phone = normalize_phone(body.get("phone", ""))
    clear_pending(phone); clear_negotiation(phone); clear_cancellation(phone)
    clear_driver_query(phone); clear_topup(phone); CONVOS.pop(phone, None)
    await _forget_received(phone)   # don't resurrect old pushes into the fresh chat
    async with transaction() as session:   # clear this phone's chat log so the widget starts fresh
        await session.execute(delete(ConversationMessage).where(ConversationMessage.phone == phone))
    return JSONResponse({"ok": True})


@app.get("/")
def index() -> HTMLResponse:
    return HTMLResponse(PAGE)


# ---------------------------------------------------------------------------
# Batch / load-test orchestrator
# ---------------------------------------------------------------------------
@app.get("/batch/roster")
def batch_roster() -> JSONResponse:
    """The pre-scripted roster the orchestrator drives (customers not seeded — they register via buttons)."""
    return JSONResponse({
        "customers": build_roster(),
        "chefs": [{"phone": c["phone"], "name": c["kitchen"]} for c in BATCH_CHEFS],
        "drivers": [{"phone": d["phone"], "name": d["name"]} for d in BATCH_DRIVERS],
    })


@app.post("/batch/reset")
async def batch_reset() -> JSONResponse:
    """Wipe + reseed 10 chefs + 10 drivers (no customers). Clears in-memory state too."""
    await seed_batch()
    CONVOS.clear()
    _folded_ids.clear()   # DB was wiped — drop all fold high-water marks
    return JSONResponse({"ok": True})


@app.get("/batch")
def batch_page() -> HTMLResponse:
    return HTMLResponse(BATCH_PAGE)


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
 <div id="bar"><b>Homaatri</b><span>multi-user tester</span>
   <button id="cutoff" title="run the meal-window cutoff now" style="background:#e8a020">⏰ Run cutoff</button>
   <input id="newphone" placeholder="new customer phone…"><button id="add">+ Add customer</button>
   <input id="newinbox" placeholder="chef phone…"><button id="addinbox">+ Add chef</button>
   <input id="newdriver" placeholder="driver phone…"><button id="adddriver">+ Add driver</button></div>
 <div id="board"></div>
<script>
 const board=document.getElementById('board');
 function esc(s){return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
 function linkify(s){return esc(s).replace(/(https?:\\/\\/[^\\s<]+)/g,'<a href="$1" target="_blank" rel="noopener">$1</a>');}
 function createWidget(phone, role){
   role = role || 'customer';
   const staff = (role === 'chef' || role === 'driver');
   const icon = role==='chef'?'🍳':(role==='driver'?'🛵':'📱');
   const tag = role==='chef'?' · chef':(role==='driver'?' · driver':'');
   const w=document.createElement('div'); w.className='widget';
   const hint = role==='chef' ? 'Chef '+phone+'. Try "show my batch", "mark order ord_… ready", "paneer is out of stock".'
              : role==='driver' ? 'Driver '+phone+'. Try "show my route", "picked up", "delivered".'
              : 'User '+phone+'. Try "hi" (to register) or "get me nearby kitchens".';
   const hide = staff ? ' style="display:none"' : '';
   w.innerHTML=`<div class="wh"><span class="ph">${icon} ${phone}${tag}</span>
       <button class="rst">reset</button><button class="cls">✕</button></div>
     <div class="log"><div class="msg sys">${hint}</div></div>
     <form class="f"><button type="button" class="loc" title="share location"${hide}>📍</button>
       <button type="button" class="pay" title="pay now" style="display:none">💳 Pay</button>
       <input class="m" autocomplete="off" placeholder="Message…"><button class="snd">Send</button></form>`;
   const log=w.querySelector('.log'),f=w.querySelector('.f'),m=w.querySelector('.m'),
         snd=w.querySelector('.snd'),loc=w.querySelector('.loc'),pay=w.querySelector('.pay');
   const add=(cls,txt)=>{const d=document.createElement('div');d.className='msg '+cls;d.innerHTML=linkify(txt);log.appendChild(d);log.scrollTop=log.scrollHeight;return d;};
   function applyAwait(j){loc.classList.toggle('hot',!!j.await_location);pay.style.display=j.await_payment?'':'none';}
   async function send(waMsg,label){add('me',label);snd.disabled=true;loc.disabled=true;
     const t=add('bot','…thinking (Kimi K2)…');
     const payload={entry:[{changes:[{value:{messages:[waMsg]}}]}]};
     try{const r=await fetch('/webhook',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
         const j=await r.json();t.innerHTML=linkify(j.reply||'(no reply)');applyAwait(j);}
     catch(e){t.textContent='Error: '+e;}
     snd.disabled=false;loc.disabled=false;m.focus();}
   pay.onclick=async()=>{add('me','💳 Paid (mock)');pay.disabled=true;snd.disabled=true;
     const t=add('bot','…processing payment…');
     try{const r=await fetch('/pay',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone})});
         const j=await r.json();t.innerHTML=linkify(j.reply||'(no reply)');pay.style.display=j.await_payment?'':'none';}
     catch(e){t.textContent='Error: '+e;}
     pay.disabled=false;snd.disabled=false;};
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
   let timer=null;
   w.querySelector('.rst').onclick=async()=>{await fetch('/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone})});
     log.innerHTML='';add('sys','reset');loc.classList.remove('hot');pay.style.display='none';};
   w.querySelector('.cls').onclick=()=>{if(timer)clearInterval(timer);w.remove();};
   // All widgets surface pushed messages: chefs get cook checklists / dietary requests;
   // customers get async dietary answers; drivers get route dispatches / ready pings.
   {let seen=0;
     async function pollOutbox(){try{const r=await fetch('/outbox?phone='+encodeURIComponent(phone));const j=await r.json();
       const msgs=j.messages||[];for(let i=seen;i<msgs.length;i++){add('bot','📨 '+msgs[i].text);}seen=msgs.length;}catch(e){}}
     timer=setInterval(pollOutbox,4000); pollOutbox();}
   board.appendChild(w);
 }
 document.getElementById('cutoff').onclick=async()=>{
   const b=document.getElementById('cutoff'); b.disabled=true; const old=b.textContent; b.textContent='⏰ running…';
   try{const r=await fetch('/cutoff',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
     const j=await r.json(); alert('Cutoff — '+(j.message||JSON.stringify(j)));}
   catch(e){alert('Cutoff error: '+e);}
   b.disabled=false; b.textContent=old;
 };
 ['7416767453','7000000000','9111111111'].forEach(p=>createWidget(p,'customer'));
 ['9876543210','9876543211','9876543212','9876543213'].forEach(p=>createWidget(p,'chef'));  // the 4 seeded chefs
 ['9500000001'].forEach(p=>createWidget(p,'driver'));  // the seeded driver (Ravi Kumar)
 document.getElementById('add').onclick=()=>{const el=document.getElementById('newphone');const v=el.value.trim();if(v){createWidget(v,'customer');el.value='';}};
 document.getElementById('addinbox').onclick=()=>{const el=document.getElementById('newinbox');const v=el.value.trim();if(v){createWidget(v,'chef');el.value='';}};
 document.getElementById('adddriver').onclick=()=>{const el=document.getElementById('newdriver');const v=el.value.trim();if(v){createWidget(v,'driver');el.value='';}};
</script></body></html>"""


BATCH_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Homaatri — batch tester</title>
<style>
 *{box-sizing:border-box}
 body{margin:0;font-family:'Segoe UI',sans-serif;background:#0b141a;color:#e9edef}
 #bar{position:sticky;top:0;z-index:5;background:#075e54;padding:8px 12px;display:flex;flex-wrap:wrap;align-items:center;gap:8px}
 #bar b{font-size:15px} #status{font-size:12px;opacity:.9;margin-left:auto}
 #steps{display:flex;flex-wrap:wrap;gap:6px;width:100%}
 #steps button{padding:7px 10px;border:0;border-radius:6px;background:#0b7d6e;color:#fff;cursor:pointer;font-size:13px}
 #steps button:disabled{opacity:.45;cursor:default}
 #steps button.reset{background:#c0392b}
 .section{padding:6px 10px}
 .section h3{margin:8px 0 6px;font-size:13px;color:#8fb0a8;font-weight:600}
 .grid{display:flex;flex-wrap:wrap;gap:6px}
 .tile{width:220px;height:150px;background:#111b21;border:1px solid #223;border-radius:8px;display:flex;flex-direction:column;overflow:hidden}
 .tile.chef{border-color:#3a5} .tile.driver{border-color:#a83}
 .th{padding:4px 7px;font-size:11px;font-weight:600;background:#1f2c34;display:flex;justify-content:space-between;gap:4px}
 .th .ph{opacity:.6;font-weight:400}
 .lg{flex:1;overflow-y:auto;padding:5px 7px;font-size:11px;line-height:1.3;display:flex;flex-direction:column;gap:3px}
 .m{padding:3px 6px;border-radius:6px;white-space:pre-wrap;word-break:break-word}
 .me{align-self:flex-end;background:#005c4b} .bot{align-self:flex-start;background:#202c33}
 .m a{color:#53bdeb}
</style></head><body>
 <div id="bar"><b>Homaatri — batch tester</b><span id="status">loading roster…</span>
  <div id="steps">
   <button class="reset" data-step="reset">🌱 Reset &amp; Seed</button>
   <button data-step="greeting">👋 Greeting</button>
   <button data-step="name">📝 Name+Addr</button>
   <button data-step="location">📍 Location</button>
   <button data-step="menu">🍔 Menu</button>
   <button data-step="order">🍽️ Order</button>
   <button data-step="pay">💳 Pay</button>
   <button data-step="status">📦 Status</button>
   <button data-step="dietary">🌶️ Cust: dietary</button>
   <button data-step="topup">➕ Cust: top-up</button>
   <button data-step="chef_dietary">🍳 Chef: accept diet</button>
   <button data-step="chef_topup">🍳 Chef: accept add-on</button>
   <button data-step="pay">💳 Pay (delta)</button>
   <button data-step="cutoff">⏰ Cutoff</button>
   <button data-step="chef_batch">🍳 Chef: batch</button>
   <button data-step="chef_ready">🍳 Chef: mark ready</button>
   <button data-step="driver_route">🛵 Drv: route</button>
   <button data-step="driver_pickup">🛵 Drv: pickup</button>
   <button data-step="driver_deliver">🛵 Drv: deliver</button>
   <button data-step="cancel">❌ Cust: cancel</button>
   <button data-step="chef_cancel">🍳 Chef: appr. cancel</button>
   <button data-step="review">⭐ Review</button>
  </div>
 </div>
 <div class="section"><h3 id="adminh">🚨 Admin queue</h3><div id="adminq" style="font-size:12px;color:#e9c46a;line-height:1.6">(empty)</div>
   <div id="summary" style="font-size:12px;color:#9fd; margin-top:6px">pipeline: —</div></div>
 <div class="section"><h3 id="ch">Customers</h3><div id="customers" class="grid"></div></div>
 <div class="section"><h3 id="hh">Chefs</h3><div id="chefs" class="grid"></div></div>
 <div class="section"><h3 id="dh">Drivers</h3><div id="drivers" class="grid"></div></div>
<script>
 let roster=[], chefs=[], drivers=[]; const tiles={}; const pollers=[];
 const $=id=>document.getElementById(id);
 function esc(s){return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
 function linkify(s){return esc(s).replace(/(https?:\\/\\/[^\\s<]+)/g,'<a href="$1" target="_blank">$1</a>');}
 function tile(container, phone, label, kind){
   const w=document.createElement('div'); w.className='tile '+(kind||'');
   w.innerHTML=`<div class="th"><span>${label}</span><span class="ph">${phone}</span></div><div class="lg"></div>`;
   const lg=w.querySelector('.lg');
   const add=(cls,txt)=>{const d=document.createElement('div');d.className='m '+cls;d.innerHTML=linkify(txt);lg.appendChild(d);lg.scrollTop=lg.scrollHeight;};
   container.appendChild(w); tiles[phone]={add, clear:()=>lg.innerHTML=''};
   return tiles[phone];
 }
 async function pool(items, worker, limit, onProgress){
   let idx=0, done=0;
   async function run(){ while(idx<items.length){ const i=idx++; await worker(items[i]); done++; onProgress&&onProgress(done, items.length); } }
   await Promise.all(Array.from({length:Math.min(limit,items.length)}, run));
 }
 async function sendWebhook(phone, waMsg, label){
   const t=tiles[phone]; if(t) t.add('me', label);
   try{ const r=await fetch('/webhook',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({entry:[{changes:[{value:{messages:[waMsg]}}]}]})});
     const j=await r.json(); if(t) t.add('bot', j.reply||'(no reply)'); }
   catch(e){ if(t) t.add('bot','ERR: '+e); }
 }
 async function payOne(c){
   const t=tiles[c.phone]; if(t) t.add('me','💳 pay');
   try{ const r=await fetch('/pay',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:c.phone})});
     const j=await r.json(); if(t) t.add('bot', j.reply||'(no reply)'); }
   catch(e){ if(t) t.add('bot','ERR: '+e); }
 }
 const txt=(phone,body)=>sendWebhook(phone,{from:phone,type:'text',text:{body}}, body);
 // Each step targets a role group ('cust'|'chef'|'driver') and broadcasts one message to it.
 // The agents (with DB-backed context) act on their own state — a chef's "accept" resolves
 // whatever request was pushed to that chef, a driver's "delivered" advances their own route.
 const STEPS={
   greeting: {g:'cust', fn:c=>txt(c.phone,'hi')},
   name:     {g:'cust', fn:c=>txt(c.phone, c.name+', '+c.address)},
   location: {g:'cust', fn:c=>sendWebhook(c.phone,{from:c.phone,type:'location',location:{latitude:c.lat,longitude:c.lng}},'📍 '+c.lat+','+c.lng)},
   menu:     {g:'cust', fn:c=>txt(c.phone,`show me the menu for ${c.kitchen}`)},
   order:    {g:'cust', fn:c=>txt(c.phone,`order ${c.qty} ${c.dish} from ${c.kitchen}, and send me the payment link`)},
   pay:      {g:'cust', fn:c=>payOne(c)},
   status:   {g:'cust', fn:c=>txt(c.phone,'where is my order?')},
   dietary:  {g:'cust', fn:c=>txt(c.phone,`can you make my ${c.dish} less spicy please?`)},
   topup:    {g:'cust', fn:c=>txt(c.phone,`please add 1 more ${c.dish} to my order`)},
   cancel:   {g:'cust', fn:c=>txt(c.phone,'please cancel my order')},
   review:   {g:'cust', fn:c=>txt(c.phone,'the food was delicious! 5 stars for the kitchen and 5 for the driver')},
   chef_batch:   {g:'chef', fn:c=>txt(c.phone,'show my batch')},
   chef_dietary: {g:'chef', fn:c=>txt(c.phone,'accept the dietary request')},
   chef_topup:   {g:'chef', fn:c=>txt(c.phone,'accept the add-on request')},
   chef_ready:   {g:'chef', fn:c=>txt(c.phone,'all my orders are packed and ready for pickup')},
   chef_cancel:  {g:'chef', fn:c=>txt(c.phone,'approve the cancellation')},
   driver_route:   {g:'driver', fn:c=>txt(c.phone,'show my route')},
   driver_pickup:  {g:'driver', fn:c=>txt(c.phone,'i have picked up the orders from the kitchen')},
   driver_deliver: {g:'driver', fn:c=>txt(c.phone,'delivered this stop')},
 };
 function setStatus(t){ $('status').textContent=t; }
 function setBusy(b){ document.querySelectorAll('#steps button').forEach(x=>x.disabled=b); }
 async function runStep(step){
   setBusy(true);
   try {
     if(step==='reset'){ setStatus('resetting + seeding…');
       await fetch('/batch/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
       Object.values(tiles).forEach(t=>t.clear()); pollers.forEach(p=>p.seen=0);
       setStatus('reset done — 10 chefs + 10 drivers seeded. Press Greeting.'); return; }
     if(step==='cutoff'){ setStatus('running cutoff…');
       const r=await fetch('/cutoff',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
       const j=await r.json().catch(()=>({message:'HTTP '+r.status}));
       setStatus('cutoff: '+String(j.message||('HTTP '+r.status)).slice(0,140)); return; }
     const s=STEPS[step];
     const items = s.g==='chef' ? chefs : s.g==='driver' ? drivers : roster;
     await pool(items, s.fn, 8, (d,n)=>setStatus(step+': '+d+'/'+n));
     setStatus(step+': done ('+items.length+')');
   } catch(e){ setStatus(step+' ERROR: '+e); }
   finally { setBusy(false); }   // always re-enable the buttons, even on error
 }
 document.querySelectorAll('#steps button').forEach(b=>b.onclick=()=>runStep(b.dataset.step));
 // admin escalation queue (read-only)
 async function pollAdmin(){ try{ const j=await (await fetch('/admin/queue')).json(); const items=j.items||[];
   $('adminh').textContent='🚨 Admin queue ('+items.length+')';
   $('adminq').innerHTML = items.length ? items.map(i=>'• ['+(i.type||'?')+'] '+(i.from||'')+': '+esc(i.summary||'')+(i.order_id?(' ('+i.order_id+')'):'')).join('<br>') : '(empty)';
   const s=await (await fetch('/admin/summary')).json(); const p=s.pipeline||{};
   $('summary').textContent='pipeline ('+s.total+'): '+Object.keys(p).filter(k=>p[k]).map(k=>k+' '+p[k]).join(' · ')+'   |   kitchens active '+s.kitchens_active+'/'+s.kitchens_total;
 }catch(e){} }
 setInterval(pollAdmin, 5000); pollAdmin();
 // chef/driver inbox polling
 function startPoll(phone){
   const p={phone, seen:0}; pollers.push(p);
   setInterval(async()=>{ try{ const r=await fetch('/outbox?phone='+encodeURIComponent(phone)); const j=await r.json();
     const m=j.messages||[]; for(let i=p.seen;i<m.length;i++){ tiles[phone] && tiles[phone].add('bot','📨 '+m[i].text); } p.seen=m.length; }catch(e){} }, 5000);
 }
 (async function(){
   const j=await (await fetch('/batch/roster')).json();
   roster=j.customers; chefs=j.chefs; drivers=j.drivers;
   $('ch').textContent='Customers ('+roster.length+')';
   $('hh').textContent='Chefs ('+j.chefs.length+')';
   $('dh').textContent='Drivers ('+j.drivers.length+')';
   roster.forEach(c=>tile($('customers'), c.phone, c.name, 'cust'));
   j.chefs.forEach(c=>{ tile($('chefs'), c.phone, '🍳 '+c.name, 'chef'); startPoll(c.phone); });
   j.drivers.forEach(d=>{ tile($('drivers'), d.phone, '🛵 '+d.name, 'driver'); startPoll(d.phone); });
   setStatus('roster loaded ('+roster.length+' customers). Press 🌱 Reset & Seed first.');
 })();
</script></body></html>"""
