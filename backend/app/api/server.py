"""Production FastAPI Server Entry Point & Webhook Receiver.

Stateless REST API backend servicing:
1. Meta WhatsApp Webhooks (`GET /webhook` verification & `POST /webhook` inbound message routing)
2. WhatsApp Web Concierge Tester frontend (`frontend/tester/index.html`)
3. Interactive Payment Simulator (`frontend/payment/mock_payment.html`)
4. Multi-Customer Batch Simulator (`POST /batch/...`)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

# Force UTF-8 stream encoding
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from backend.app.agents.agents import customer_agent
from backend.app.api.whatsapp import normalize_phone, parse_webhook, verify_challenge
from backend.app.core.config import settings
from backend.app.db.session import SessionFactory
from backend.app.executors.master import execute_conversation_message_insert
from backend.app.models.customer import CustomerOrder, CustomerProfile
from backend.app.models.shared import ConversationMessage
from backend.app.router import route
from backend.app.services.payment_confirmation import payment_poller_loop, settle_payment_if_paid
from backend.app.services.whatsapp_outbox import outbox_dispatcher_loop
from backend.app.services.whatsapp_service import send_whatsapp_text_message
from backend.app.tools.pause import RESUME_HANDLERS, get_pending

# Register pause & resume handlers
import backend.app.tools.customer_tools  # noqa: F401

from backend.app.api.admin import router as admin_router
from backend.app.api.v1.auth import router as auth_router
from backend.app.api.v1.bulk import router as bulk_router
from backend.app.api.v1.orders import router as orders_router
from backend.app.api.v1.customer import router as customer_router
from backend.app.api.v1.reels import router as reels_router
from backend.app.api.v1.chat import router as chat_router
from backend.app.api.v1.kitchens import router as kitchens_router
from backend.app.api.v1.chef_ops import router as chef_ops_router
from backend.app.api.v1.rider_ops import router as rider_ops_router

logger = logging.getLogger("homatri_server")
WEBHOOK_VERIFY_TOKEN = settings.webhook_verify_token

from contextlib import asynccontextmanager

_BACKGROUND_LOOPS: dict[str, asyncio.Task] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: verify DB tables + launch the payment poller and outbox dispatcher."""
    try:
        from backend.app.db.base import Base
        from backend.app.db.session import engine
        import backend.app.models  # noqa: F401
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("🟢 Database tables verified/created successfully on startup!")
    except Exception as e:
        logger.error(f"🔴 Database table creation on startup notice: {e}")

    stop = asyncio.Event()
    _BACKGROUND_LOOPS["payments"] = asyncio.create_task(payment_poller_loop(stop))
    _BACKGROUND_LOOPS["outbox"] = asyncio.create_task(outbox_dispatcher_loop(stop))
    try:
        yield
    finally:
        stop.set()
        for name, task in _BACKGROUND_LOOPS.items():
            task.cancel()
        logger.info("Background loops stopped.")

app = FastAPI(
    title="Homaatri Backend",
    description="Single customer WhatsApp AI concierge + web portals API (chefs & riders on web, customers on WhatsApp)",
    version="2.0.0",
    lifespan=lifespan
)

# Register Admin Operations Router
app.include_router(admin_router)
app.include_router(auth_router, prefix="/api/v1")
app.include_router(bulk_router, prefix="/api/v1")
app.include_router(orders_router, prefix="/api/v1")
app.include_router(customer_router, prefix="/api/v1")
app.include_router(reels_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
app.include_router(kitchens_router, prefix="/api/v1")
app.include_router(chef_ops_router, prefix="/api/v1")
app.include_router(rider_ops_router, prefix="/api/v1")

# Enable CORS for the Next.js app (cookies require explicit origins).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3005",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3005",
        "https://homatri.com",
        "https://www.homatri.com",
    ],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi import WebSocket, WebSocketDisconnect
from backend.app.core.security import decode_access_token
from backend.app.services.rider_gps import record_rider_fix

_uploads = os.path.abspath(getattr(settings, "uploads_dir", "backend/uploads"))
os.makedirs(os.path.join(_uploads, "reels"), exist_ok=True)
if os.path.isdir(_uploads):
    app.mount("/media", StaticFiles(directory=_uploads), name="media")


@app.websocket("/ws/v1/rider/location")
async def rider_location_ws(ws: WebSocket):
    token = ws.query_params.get("token") or (ws.headers.get("authorization") or "").replace("Bearer ", "")
    try:
        payload = decode_access_token(token)
    except Exception:
        await ws.close(code=4401)
        return
    if payload.get("role") != "RIDER":
        await ws.close(code=4403)
        return
    await ws.accept()
    phone = payload["sub"]
    try:
        while True:
            raw = await ws.receive_json()
            lat = float(raw.get("latitude"))
            lng = float(raw.get("longitude"))
            heading = raw.get("heading")
            fix = await record_rider_fix(phone, lat, lng, float(heading) if heading is not None else None)
            await ws.send_json({"ok": True, "timestamp": fix["timestamp"]})
    except WebSocketDisconnect:
        return
    except Exception:
        await ws.close()


# Mount Decoupled Frontend Static Files
if os.path.exists("frontend/tester"):
    app.mount("/static/tester", StaticFiles(directory="frontend/tester"), name="tester")
if os.path.exists("frontend/payment"):
    app.mount("/static/payment", StaticFiles(directory="frontend/payment"), name="payment")
if os.path.exists("frontend/admin"):
    app.mount("/static/admin", StaticFiles(directory="frontend/admin"), name="admin")


# ==============================================================================
# 1. FRONTEND HOMEPAGE ROUTES (Serves Decoupled Frontend UI)
# ==============================================================================
@app.get("/privacy-policy", response_class=HTMLResponse)
async def serve_privacy_policy():
    """Serves the Official Homaatri Privacy Policy Page."""
    privacy_path = "frontend/privacy.html"
    if os.path.exists(privacy_path):
        with open(privacy_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Privacy Policy Page</h1>")


@app.get("/admin/login", response_class=HTMLResponse)
async def serve_admin_login():
    """Serves the Admin Login Page."""
    login_path = "frontend/admin/login.html"
    if os.path.exists(login_path):
        with open(login_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Admin Login Page Not Found</h1>")


@app.get("/admin", response_class=HTMLResponse)
async def serve_admin_portal():
    """Serves the Admin Operations Portal interface."""
    admin_path = "frontend/admin/index.html"
    if os.path.exists(admin_path):
        with open(admin_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Admin Portal UI Not Found</h1>")


@app.get("/", response_class=HTMLResponse)
async def serve_homepage():
    """Serves the WhatsApp Concierge Web Tester interface."""
    tester_path = "frontend/tester/index.html"
    if os.path.exists(tester_path):
        with open(tester_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Homaatri Backend Server Running</h1>")


@app.get("/static/mock_payment.html", response_class=HTMLResponse)
async def serve_mock_payment():
    """Serves the interactive mock payment simulator."""
    payment_path = "frontend/payment/mock_payment.html"
    if os.path.exists(payment_path):
        with open(payment_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Mock Payment Page</h1>")


# ==============================================================================
# 2. META WHATSAPP WEBHOOK ROUTES (REAL + TESTER INGRESS)
# ==============================================================================
@app.get("/webhook")
def webhook_verify(req: Request):
    """Meta GET Webhook Verification Handshake."""
    p = req.query_params
    ch = verify_challenge(
        p.get("hub.mode", ""),
        p.get("hub.verify_token", ""),
        p.get("hub.challenge", ""),
        WEBHOOK_VERIFY_TOKEN
    )
    if ch is not None:
        logger.info("🟢 Meta GET Webhook Handshake Verified Successfully!")
        return PlainTextResponse(ch, status_code=200)
    logger.warning("🔴 Meta GET Webhook Verification Failed: Invalid Token")
    return JSONResponse({"error": "verification failed"}, status_code=403)


_PROCESSED_WAMIDS: dict[str, float] = {}
_BACKGROUND_TASKS: set[asyncio.Task] = set()


async def _process_inbound_message(msg: dict[str, Any]):
    """Background worker: Log inbound -> Route & Run the customer agent -> Log outbound -> Dispatch to WhatsApp."""
    try:
        phone = msg["phone"]

        # 1. Log inbound message to conversation history
        intext = msg.get("text") or f"(shared location: {msg.get('location')})"
        await _log_message(
            phone, actor_role="CUSTOMER", direction="INBOUND",
            source="WHATSAPP", text=intext,
            message_type="LOCATION" if msg.get("type") == "location" else "TEXT"
        )

        # 2. Pass normalized message through check-first router -> single customer agent
        t0 = time.perf_counter()
        result = await route(msg, _agent_runner_factory(phone))
        dt_ms = (time.perf_counter() - t0) * 1000.0
        logger.info(f"⏱️ [TOTAL-TIMING] {phone}: {dt_ms:.2f} ms")

        # 3. Log outbound reply
        if isinstance(result, dict):
            outtext = result.get("reply", "")
        else:
            outtext = str(result)

        await _log_message(
            phone, actor_role="CUSTOMER", direction="OUTBOUND",
            source="WHATSAPP", text=outtext
        )

        # 4. Dispatch outbound reply to real Meta WhatsApp (credentials configured)
        await send_whatsapp_text_message(phone, outtext)
    except Exception as e:
        logger.error(f"🔴 Exception inside _process_inbound_message: {e}", exc_info=True)


@app.post("/webhook")
async def webhook(req: Request):
    """Inbound message webhook (Meta Cloud API JSON shape) → parse → instant 200 OK → background process."""
    raw_body = await req.body()

    # Optional Meta signature verification: only enforced when META_APP_SECRET is
    # configured (X-Hub-Signature-256 = HMAC-SHA256 of the raw body, sha256= prefix).
    if settings.meta_app_secret:
        import hashlib
        import hmac as _hmac
        sig = req.headers.get("x-hub-signature-256", "")
        expected = "sha256=" + _hmac.new(
            settings.meta_app_secret.encode(), raw_body, hashlib.sha256
        ).hexdigest()
        if not _hmac.compare_digest(sig, expected):
            logger.warning("⚠️ Webhook rejected: X-Hub-Signature-256 mismatch")
            return JSONResponse({"status": "invalid_signature"}, status_code=403)

    raw_payload = json.loads(raw_body)
    msg = parse_webhook(raw_payload)

    if msg is None:
        return JSONResponse({"status": "ignored"})  # Delivery statuses, etc.

    wamid = msg.get("wamid")
    if wamid:
        now_ts = time.time()
        # Clean up stale entries older than 300s
        for k in list(_PROCESSED_WAMIDS.keys()):
            if now_ts - _PROCESSED_WAMIDS[k] > 300:
                _PROCESSED_WAMIDS.pop(k, None)

        if wamid in _PROCESSED_WAMIDS:
            logger.info(f"⏭️ Ignored duplicate Meta webhook retry for wamid: {wamid}")
            return JSONResponse({"status": "duplicate_ignored"})
        _PROCESSED_WAMIDS[wamid] = now_ts

    # Process AI turn in strongly referenced background task
    task = asyncio.create_task(_process_inbound_message(msg))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    # Return HTTP 200 OK immediately to Meta (in ~5ms)
    return JSONResponse({"status": "ok"})


# ==============================================================================
# 2b. RAZORPAY PAYMENT WEBHOOK (server-to-server confirmation path)
# ==============================================================================
@app.post("/payments/razorpay")
async def razorpay_webhook(req: Request):
    """Razorpay server-to-server webhook (payment_link.paid etc.).

    Security: if a real webhook secret is configured, the X-Razorpay-Signature
    HMAC is verified; regardless, settlement only ever happens after the
    payment link status is re-fetched from the Razorpay API server-side — a
    spoofed body alone can never confirm an order.
    """
    import hashlib
    import hmac as _hmac
    from sqlalchemy import select as _select
    from backend.app.models.customer import CustomerPayment as _CustomerPayment

    raw = await req.body()
    secret = (settings.razorpay_webhook_secret or "").strip()
    if secret and "mock" not in secret.lower():
        sig = req.headers.get("x-razorpay-signature", "")
        expected = _hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(expected, sig.strip()):
            logger.warning("⚠️ Razorpay webhook signature mismatch — rejected")
            return JSONResponse({"status": "invalid_signature"}, status_code=403)

    try:
        payload = json.loads(raw)
    except Exception:
        return JSONResponse({"status": "ignored"})

    event = payload.get("event", "")
    link_id = None
    try:
        link_id = payload["payload"]["payment_link"]["entity"]["id"]
    except (KeyError, TypeError):
        pass

    if not link_id:
        logger.info(f"ℹ️ Razorpay webhook {event or 'unknown'} without payment_link — ignored")
        return JSONResponse({"status": "ignored"})

    async with SessionFactory() as session:
        payment = (
            await session.execute(
                _select(_CustomerPayment).where(
                    _CustomerPayment.gateway_order_id == link_id,
                    _CustomerPayment.status == "PENDING",
                )
            )
        ).scalars().first()

    if payment is None:
        logger.info(f"ℹ️ Razorpay webhook for unknown/settled link {link_id} — ignored")
        return JSONResponse({"status": "ok"})

    res = await settle_payment_if_paid(payment)
    logger.info(f"💳 Razorpay webhook {event} -> settle {payment.payment_id}: {res}")
    return JSONResponse({"status": "ok"})


@app.get("/pay/thanks", response_class=HTMLResponse)
async def serve_payment_thanks(order_id: str = ""):
    """Post-payment landing page (Razorpay redirects here after a successful pay)."""
    thanks_path = "frontend/payment/thanks.html"
    if os.path.exists(thanks_path):
        with open(thanks_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Payment received</h1><p>Check WhatsApp for your order confirmation.</p>")


@app.get("/payments/check")
async def payments_check(payment_link_id: str = ""):
    """Settle-on-demand: verify a payment link with the gateway and settle if paid.

    Called by the /pay/thanks page right after the customer pays (this also
    wakes a cold Cloud Run instance so the WhatsApp confirmation goes out
    within seconds). Trusts only the server-side Razorpay fetch.
    """
    from sqlalchemy import select as _select
    from backend.app.models.customer import CustomerPayment as _CustomerPayment

    link_id = (payment_link_id or "").strip()
    if not link_id:
        return JSONResponse({"status": "ignored"})
    async with SessionFactory() as session:
        payment = (
            await session.execute(
                _select(_CustomerPayment).where(
                    _CustomerPayment.gateway_order_id == link_id,
                    _CustomerPayment.status == "PENDING",
                ).order_by(_CustomerPayment.created_at.desc())
            )
        ).scalars().first()
    if payment is None:
        return JSONResponse({"status": "unknown_link"})
    res = await settle_payment_if_paid(payment)
    return JSONResponse({"status": res.get("reason", "checked"), "settled": res.get("settled", False)})


# ==============================================================================
# 3. HELPER FUNCTIONS & AGENT RUNNER
# ==============================================================================
async def _build_user_db_summary(phone: str) -> str:
    """Query Cloud SQL Postgres DB for live customer profile & active order state."""
    from sqlalchemy import select
    clean_phone = phone[-10:] if len(phone) >= 10 else phone
    async with SessionFactory() as session:
        profile = await session.get(CustomerProfile, clean_phone)
        if profile is None:
            return f"[LIVE DB STATE]: Customer ({clean_phone}) is NEW (Not registered in database)."

        # Check active orders
        res = await session.execute(
            select(CustomerOrder)
            .where(CustomerOrder.customer_phone == clean_phone)
            .order_by(CustomerOrder.created_at.desc())
            .limit(1)
        )
        latest_order = res.scalar_one_or_none()
        order_info = (
            f"Latest Order ID: {latest_order.order_id}, Status: {latest_order.status}, Amount: ₹{latest_order.total_amount}"
            if latest_order else "No active orders."
        )
        return (
            f"[LIVE DB STATE]: Customer Name: '{profile.name}', Address: '{profile.delivery_address}', "
            f"Is Registered: {profile.is_registered}, Location Pin: ({profile.latitude}, {profile.longitude}), "
            f"{order_info}"
        )


async def _fetch_recent_history(phone: str, limit: int = 6) -> list[Any]:
    """Query last `limit` messages for user from conversation_messages master table."""
    from langchain_core.messages import AIMessage, HumanMessage
    clean_phone = phone[-10:] if len(phone) >= 10 else phone
    async with SessionFactory() as session:
        from sqlalchemy import select
        res = await session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.phone.like(f"%{clean_phone}"))
            .order_by(ConversationMessage.created_at.desc())
            .limit(limit)
        )
        history = list(reversed(res.scalars().all()))

    msgs = []
    for msg in history:
        m_text = msg.message_text
        if not m_text or not m_text.strip():
            continue
        if msg.direction == "INBOUND":
            msgs.append(HumanMessage(content=m_text))
        elif msg.direction == "OUTBOUND":
            msgs.append(AIMessage(content=m_text))
    return msgs


def _agent_runner_factory(phone: str):
    """Factory creating the single customer agent runner for a phone's turn."""
    async def _run_agent(user_phone: str, prompt: str) -> str:
        agent = customer_agent

        # Normalize phone to 10 digits
        clean_phone = user_phone[-10:] if len(user_phone) >= 10 else user_phone

        # Fetch live database user summary
        db_summary = await _build_user_db_summary(clean_phone)

        # Inject system context so LLM never asks for phone number & knows user DB state
        system_ctx = (
            f"[SYSTEM NOTICE]: User's verified 10-digit phone number is '{clean_phone}'. "
            f"They are ALREADY messaging from this phone number on WhatsApp. "
            f"Do NOT ask the user for their phone number or mobile number.\n"
            f"{db_summary}"
        )

        t0 = time.perf_counter()
        from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
        from backend.app.agents.llm import shared_llm
        from backend.app.tools.pause import Pause

        # Fetch recent chat history from database
        history_msgs = await _fetch_recent_history(clean_phone, limit=6)
        messages = [
            SystemMessage(content=f"{agent.system_prompt}\n\n{system_ctx}"),
            *history_msgs,
            HumanMessage(content=prompt),
        ]

        llm = shared_llm()
        if agent.tools:
            llm = llm.bind_tools(list(agent.tools))

        # Tool-execution loop: call the LLM, execute any tool_calls against the
        # agent's tool_map, feed results back, repeat (bounded) until the model
        # returns a plain text reply. A Pause raised inside a tool unwinds the
        # turn and its message becomes the reply.
        MAX_TURNS = 6
        reply_text = ""
        try:
            for _turn in range(MAX_TURNS):
                res = await llm.ainvoke(messages)
                tool_calls = getattr(res, "tool_calls", None)
                if not tool_calls:
                    reply_text = getattr(res, "content", "")
                    if isinstance(reply_text, list):
                        reply_text = " ".join([str(item) for item in reply_text])
                    reply_text = str(reply_text).strip()
                    break
                messages.append(res)
                for tc in tool_calls:
                    tc_name = tc.get("name", "")
                    tc_id = tc.get("id", "") or ""
                    tool = agent.tool_map.get(tc_name)
                    if tool is None:
                        messages.append(ToolMessage(
                            content=f"Error: unknown tool '{tc_name}'",
                            tool_call_id=tc_id,
                        ))
                        continue
                    try:
                        out = await tool.ainvoke(tc.get("args", {}))
                        logger.info(f"🔧 [{clean_phone}] TOOL {tc_name} -> {str(out)[:120]}")
                    except Pause as p:
                        logger.info(f"⏸️ Agent paused turn for {clean_phone}: {p.message}")
                        return p.message
                    except Exception as te:
                        logger.error(f"🔴 Tool {tc_name} failed for {clean_phone}: {te}", exc_info=True)
                        out = f"Error executing {tc_name}: {te}"
                    messages.append(ToolMessage(content=str(out), tool_call_id=tc_id))
        except Pause as p:
            logger.info(f"⏸️ Agent paused turn for {clean_phone}: {p.message}")
            return p.message
        except Exception as e:
            if "Pause" in str(type(e)):
                msg = getattr(e, "message", str(e))
                logger.info(f"⏸️ Agent paused turn for {clean_phone}: {msg}")
                return msg
            logger.error(f"🔴 Error in _run_agent: {e}", exc_info=True)
            reply_text = "I've processed your request. How can I help you next?"

        if not reply_text:
            reply_text = "How can I help you with your order today?"

        dt_ms = (time.perf_counter() - t0) * 1000.0
        logger.info(f"⏱️ [LLM-TIMING] customer agent call for {clean_phone}: {dt_ms:.2f} ms")

        return reply_text

    return _run_agent


async def _log_message(
    phone: str, actor_role: str, direction: str,
    source: str, text: str, message_type: str = "TEXT"
):
    """Persist conversation audit log to database."""
    async with SessionFactory() as session:
        await execute_conversation_message_insert(
            session=session,
            phone=phone,
            actor_role=actor_role,
            direction=direction,
            source=source,
            message_type=message_type,
            message_text=text
        )
        await session.commit()


# ==============================================================================
# 4. OUTBOX POLLING FOR TESTER UI
# ==============================================================================
@app.get("/outbox")
async def get_outbox(phone: str):
    """Return conversation history for a phone number for the test UI."""
    async with SessionFactory() as session:
        from sqlalchemy import select
        res = await session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.phone == phone)
            .order_by(ConversationMessage.created_at.asc())
        )
        messages = res.scalars().all()
        out = []
        for m in messages:
            out.append({
                "from": "user" if m.direction == "INBOUND" else "agent",
                "text": m.message_text,
                "created_at": m.created_at.isoformat() if m.created_at else ""
            })
        return JSONResponse(out)
