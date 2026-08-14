"""Production FastAPI Server Entry Point & Webhook Receiver.

Stateless REST API backend servicing:
1. Meta WhatsApp Webhooks (`GET /webhook` verification & `POST /webhook` inbound message routing)
2. WhatsApp Web Concierge Tester frontend (`frontend/tester/index.html`)
3. Interactive Payment Simulator (`frontend/payment/mock_payment.html`)
4. Multi-Customer Batch Simulator (`POST /batch/...`)
"""

from __future__ import annotations

import asyncio
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

from backend.app.agents.agents import chef_agent, customer_agent, driver_agent
from backend.app.agents.llm import get_llm
from backend.app.api.whatsapp import normalize_phone, parse_webhook, verify_challenge
from backend.app.core.config import settings
from backend.app.db.session import SessionFactory
from backend.app.executors.master import execute_conversation_message_insert
from backend.app.models.chef import ChefProfile
from backend.app.models.customer import CustomerOrder
from backend.app.models.driver import DriverProfile
from backend.app.models.shared import ConversationMessage
from backend.app.models.system import SystemOutboundQueue
from backend.app.router import route
from backend.app.services.whatsapp_service import send_whatsapp_text_message
from backend.app.tools.pause import RESUME_HANDLERS, get_pending

# Register pause & resume handlers
import backend.app.tools.customer_tools  # noqa: F401
import backend.app.tools.topup  # noqa: F401

from backend.app.api.admin import router as admin_router

logger = logging.getLogger("homatri_server")
WEBHOOK_VERIFY_TOKEN = getattr(settings, "webhook_verify_token", "homatri_verify")

app = FastAPI(
    title="Homaatri Agentic Backend Engine",
    description="Stateless agentic multi-role WhatsApp concierges powered by GCP Vertex AI",
    version="1.0.0"
)

# Register Admin Operations Router
app.include_router(admin_router)

# Enable CORS for decoupled frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


@app.post("/webhook")
async def webhook(req: Request):
    """Inbound message webhook (Meta Cloud API JSON shape) → parse → router → reply."""
    raw_payload = await req.json()
    msg = parse_webhook(raw_payload)

    if msg is None:
        return JSONResponse({"status": "ignored"})  # Delivery statuses, etc.

    phone = msg["phone"]
    role = await _determine_role(phone)

    # 1. Log inbound message to conversation history
    intext = msg.get("text") or f"(shared location: {msg.get('location')})"
    await _log_message(
        phone, actor_role=role, direction="INBOUND",
        source="WHATSAPP", text=intext,
        message_type="LOCATION" if msg.get("type") == "location" else "TEXT"
    )

    # 2. Pass normalized message through check-first router
    t0 = time.perf_counter()
    result = await route(msg, _agent_runner_factory(role, phone))
    dt_ms = (time.perf_counter() - t0) * 1000.0
    logger.info(f"⏱️ [TOTAL-TIMING] {phone} ({role}): {dt_ms:.2f} ms")

    # 3. Log outbound reply
    outtext = result.get("reply", "")
    await _log_message(
        phone, actor_role=role, direction="OUTBOUND",
        source="WHATSAPP", text=outtext
    )

    # 4. Dispatch outbound reply to real Meta WhatsApp if API credentials configured
    asyncio.create_task(send_whatsapp_text_message(phone, outtext))

    return JSONResponse(result)


# ==============================================================================
# 3. HELPER FUNCTIONS & AGENT RUNNER
# ==============================================================================
async def _determine_role(phone: str) -> str:
    """Determine role based on seeded database profile."""
    async with SessionFactory() as session:
        if await session.get(ChefProfile, phone) is not None:
            return "CHEF"
        if await session.get(DriverProfile, phone) is not None:
            return "DRIVER"
    return "CUSTOMER"


def _agent_runner_factory(role: str, phone: str):
    """Factory creating the specific agent runner for a phone/role turn."""
    async def _run_agent(prompt: str) -> str:
        # Select active persona agent
        if role == "CHEF":
            agent = chef_agent
        elif role == "DRIVER":
            agent = driver_agent
        else:
            agent = customer_agent

        # Invoke agent with system prompt + user message
        t0 = time.perf_counter()
        res = await agent.agent.ainvoke({"messages": [("user", prompt)]})
        dt_ms = (time.perf_counter() - t0) * 1000.0
        logger.info(f"⏱️ [LLM-TIMING] {role} agent call: {dt_ms:.2f} ms")

        # Extract text reply from agent response
        last_msg = res["messages"][-1]
        return getattr(last_msg, "content", str(last_msg))

    return _run_agent


async def _log_message(
    phone: str, actor_role: str, direction: str,
    source: str, text: str, message_type: str = "TEXT"
):
    """Persist conversation audit log to database."""
    async with SessionFactory() as session:
        await execute_conversation_message_insert(
            session=session,
            actor_phone=phone,
            actor_role=actor_role,
            direction=direction,
            source=source,
            message_type=message_type,
            content_text=text
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
            .where(ConversationMessage.actor_phone == phone)
            .order_by(ConversationMessage.created_at.asc())
        )
        messages = res.scalars().all()
        out = []
        for m in messages:
            out.append({
                "from": "user" if m.direction == "INBOUND" else "agent",
                "text": m.content_text,
                "created_at": m.created_at.isoformat() if m.created_at else ""
            })
        return JSONResponse(out)
