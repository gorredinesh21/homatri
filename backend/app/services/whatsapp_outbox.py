"""Outbox dispatcher — actually delivers queued WhatsApp notifications.

Everything in the backend that notifies a human (order confirmed by payment,
kitchen packed, rider picked up, delivery PIN, escalations…) enqueues a row in
`system_outbound_queue` via `execute_outbound_whatsapp_enqueue`. Historically
those rows just sat there (the queue was only read by the tester UI).

This dispatcher, started in the server lifespan, drains the queue every few
seconds and sends each row over the real Meta Cloud API:

  - CUSTOMER rows: always sent — these are the customer's order updates.
  - ADMIN rows: sent to settings.ops_phone when configured.
  - CHEF / DRIVER rows: attempted too. Chefs and riders now live in their web
    portals, but one-way order notifications to their phones are still useful
    once the Meta app is live. Until then Meta rejects non-allowed recipients
    and the row lands in FAILED with the reason recorded (no retry storm).

Retry policy: up to 3 attempts, exponential-ish backoff via next scan (rows
stay QUEUED with attempts+1 on transient errors; FAILED is terminal).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from backend.app.db.session import SessionFactory, transaction
from backend.app.models.system import SystemOutboundQueue
from backend.app.services.whatsapp_service import send_whatsapp_text_message

logger = logging.getLogger("outbox_dispatcher")

MAX_ATTEMPTS = 3
SCAN_INTERVAL_SECS = 5
BATCH = 20

# Recipient roles we deliver to WhatsApp. CHEF/DRIVER sends will fail safely
# with Meta's recipient-not-allowed error while the app is in development mode.
DELIVER_ROLES = {"CUSTOMER", "ADMIN", "CHEF", "DRIVER"}


async def dispatch_once() -> int:
    """One dispatch pass. Returns how many messages were sent."""
    sent = 0
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                select(SystemOutboundQueue)
                .where(SystemOutboundQueue.status == "QUEUED")
                .order_by(SystemOutboundQueue.created_at.asc())
                .limit(BATCH)
            )
        ).scalars().all()

    for row in rows:
        if row.recipient_role not in DELIVER_ROLES:
            continue
        if row.attempts >= MAX_ATTEMPTS:
            async with transaction() as s:
                fresh = await s.get(SystemOutboundQueue, row.message_id)
                if fresh is not None and fresh.status == "QUEUED":
                    fresh.status = "FAILED"
                    fresh.error_detail = "max attempts reached"
            continue

        res = await send_whatsapp_text_message(row.recipient_phone, row.message_text)
        ok = bool(res.get("messages")) and not res.get("error")

        async with transaction() as s:
            fresh = await s.get(SystemOutboundQueue, row.message_id)
            if fresh is None:
                continue
            if ok:
                fresh.status = "SENT"
                fresh.sent_at = datetime.now(timezone.utc)
                fresh.wa_message_id = (res.get("messages") or [{}])[0].get("id")
                fresh.attempts += 1
                sent += 1
                logger.info(f"📤 Outbox delivered {row.message_id} -> {row.recipient_role} {row.recipient_phone}")
            else:
                fresh.attempts += 1
                err = res.get("error")
                if isinstance(err, (dict, list)):
                    err = json.dumps(err)[:400]
                fresh.error_detail = str(err or "unknown send failure")[:400]
                # Mock-logged (no creds) or hard recipient errors -> don't retry forever.
                if res.get("status") == "mock_logged" or fresh.attempts >= MAX_ATTEMPTS:
                    fresh.status = "FAILED"
                logger.warning(
                    f"⚠️ Outbox send failed {row.message_id} (attempt {fresh.attempts}): "
                    f"{(err or '')[:150]}"
                )
    return sent


async def outbox_dispatcher_loop(stop: asyncio.Event) -> None:
    logger.info("📤 Outbox dispatcher started (5s interval)")
    while not stop.is_set():
        try:
            await dispatch_once()
        except Exception as e:
            logger.error(f"🔴 Outbox dispatcher iteration failed: {e}", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=SCAN_INTERVAL_SECS)
        except asyncio.TimeoutError:
            pass
    logger.info("📤 Outbox dispatcher stopped")
