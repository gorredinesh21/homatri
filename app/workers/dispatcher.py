"""Background processing of inbound WhatsApp messages.

The webhook must ACK Meta within ~3s, so it only parses + enqueues; the actual
LLM/DB work runs here in a FastAPI BackgroundTask with its own DB session. This
is the seam where a Redis/arq worker would slot in later — the webhook would
enqueue to Redis instead of a BackgroundTask, and this function becomes the
worker body, unchanged.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.payments.factory import get_payment_provider
from app.services.conversation import process_inbound
from app.whatsapp.base import InboundMessage
from app.whatsapp.factory import get_whatsapp_provider

log = get_logger("dispatcher")


async def handle_inbound_message(msg: InboundMessage) -> None:
    wa = get_whatsapp_provider()
    pay = get_payment_provider()
    try:
        async with SessionLocal() as session:
            await process_inbound(session, wa, pay, msg)
    except Exception:  # noqa: BLE001
        log.exception("failed to process inbound from %s", msg.from_phone)
