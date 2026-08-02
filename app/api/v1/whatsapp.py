"""Meta WhatsApp Cloud API Webhook Router (app/api/v1/whatsapp.py).

Provides GET verification handshake & POST message ingress for Meta WhatsApp Business Cloud API.
"""

from __future__ import annotations

import os
from typing import Any
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from langchain_core.messages import HumanMessage

from app.agents.graph import homatri_app
from app.db.session import SessionFactory
from app.executors.master import (
    execute_conversation_message_insert,
    execute_outbound_whatsapp_enqueue,
)

router = APIRouter(prefix="/webhooks/whatsapp", tags=["Meta WhatsApp Webhook"])


@router.get("")
async def verify_whatsapp_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """Verify webhook endpoint during Meta Developer Portal setup."""
    expected_token = os.environ.get("WHATSAPP_VERIFY_TOKEN", "homatri_verify_token_2026")
    if hub_mode == "subscribe" and hub_token == expected_token:
        return Response(content=hub_challenge, media_type="text/plain", status_code=200)
    return Response(content="Verification token mismatch", status_code=403)


@router.post("")
async def handle_whatsapp_webhook(request: Request):
    """Ingest inbound WhatsApp messages from Meta, pass to LangGraph, and enqueue reply."""
    payload = await request.json()

    entry = payload.get("entry", [{}])[0]
    changes = entry.get("changes", [{}])[0]
    value = changes.get("value", {})
    messages = value.get("messages", [])

    if not messages:
        return {"status": "ignored", "reason": "No messages in payload"}

    msg = messages[0]
    sender_phone = msg.get("from")  # e.g. "919876543210"
    user_text = msg.get("text", {}).get("body", "")

    if not sender_phone or not user_text:
        return {"status": "ignored", "reason": "Missing sender_phone or text body"}

    # 1. Record Inbound WhatsApp Message in PostgreSQL Ledger
    async with SessionFactory() as session:
        await execute_conversation_message_insert(
            session,
            phone=sender_phone,
            actor_role="CUSTOMER",
            direction="INBOUND",
            source="META_WHATSAPP_CLOUD_API",
            message_text=user_text,
        )

    # 2. Invoke LangGraph Engine (homatri_app)
    inputs = {
        "messages": [HumanMessage(content=user_text)],
        "active_phone": sender_phone,
    }
    thread_id = f"thread_wa_{sender_phone}"
    config = {"configurable": {"thread_id": thread_id}}

    try:
        graph_res = await homatri_app.ainvoke(inputs, config=config)
        ai_reply_text = str(graph_res["messages"][-1].content)
    except Exception:
        ai_reply_text = f"🤖 Thanks for messaging Homaatri! We are processing your request."

    # 3. Enqueue Outbound WhatsApp Reply
    async with SessionFactory() as session:
        await execute_outbound_whatsapp_enqueue(
            session,
            recipient_phone=sender_phone,
            recipient_role="CUSTOMER",
            message_text=ai_reply_text,
        )
        await execute_conversation_message_insert(
            session,
            phone=sender_phone,
            actor_role="CUSTOMER",
            direction="OUTBOUND",
            source="LLM_AGENT_RESPONSE",
            message_text=ai_reply_text,
        )

    return {"status": "success", "recipient": sender_phone}
