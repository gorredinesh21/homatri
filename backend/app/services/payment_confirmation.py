"""Payment confirmation engine — settles REAL Razorpay payments for WhatsApp orders.

Two independent paths converge on `settle_payment_if_paid`, and both trust only
Razorpay's server-side payment-link status (never a client claim):

  1. A background poller (started in the server lifespan) scans recent PENDING
     CustomerPayments every 15s and settles any whose link is now `paid`.
     This needs ZERO dashboard configuration and always works.

  2. POST /payments/razorpay — Razorpay's server-to-server webhook
     (payment_link.paid), if/when it is configured in the Razorpay dashboard.
     The signature is verified when a real webhook secret is set, and the link
     status is re-fetched from the API before settling.

On settlement the order cascades PENDING_PAYMENT -> CONFIRMED (DW2 -> DW1),
the customer gets the confirmation on WhatsApp, and any pending PAYMENT_CONFIRM
pause note is cleared.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from backend.app.db.session import SessionFactory, transaction
from backend.app.executors.customer import execute_payment_status_update
from backend.app.executors.master import execute_conversation_message_insert
from backend.app.models.customer import CustomerOrder, CustomerPayment
from backend.app.services.payment_service import razorpay_service
from backend.app.services.whatsapp_service import send_whatsapp_text_message
from backend.app.tools.pause import clear_pending

logger = logging.getLogger("payment_confirmation")

# How long after minting we keep polling a PENDING payment link (Razorpay links
# stay valid for ~6 months, but a customer paying for tonight's dinner pays within minutes).
POLL_WINDOW_MINS = 120
POLL_INTERVAL_SECS = 15


async def settle_payment_if_paid(payment: CustomerPayment) -> dict[str, Any]:
    """Fetch the link status from Razorpay; settle if paid. Idempotent.

    Returns {settled: bool, reason}. Sends the WhatsApp confirmation itself.
    """
    if payment.status == "PAID":
        return {"settled": True, "reason": "already_paid"}

    if not payment.gateway_order_id:
        return {"settled": False, "reason": "no_link"}

    link = await razorpay_service.fetch_payment_link(payment.gateway_order_id)
    if link.get("mode") == "ERROR":
        logger.warning(f"⚠️ Link fetch failed for {payment.payment_id}: {link.get('error', '')[:120]}")
        return {"settled": False, "reason": "gateway_error"}
    if link.get("status") != "paid":
        return {"settled": False, "reason": f"link_{link.get('status')}"}

    txn = link.get("transaction_id")
    confirm_msg = ""
    async with transaction() as session:
        # Re-read inside the transaction (another path may have settled it meanwhile).
        fresh = await session.get(CustomerPayment, payment.payment_id)
        if fresh is None:
            return {"settled": False, "reason": "vanished"}
        if fresh.status == "PAID":
            return {"settled": True, "reason": "already_paid"}
        await execute_payment_status_update(
            session, payment_id=fresh.payment_id, target_status="PAID",
            gateway_transaction_id=txn,
        )
        order = await session.get(CustomerOrder, fresh.order_id)
        # Record in the unified chat log so the agent's next turn sees the confirmation.
        confirm_msg = (
            f"✅ Payment of ₹{float(fresh.amount_due):.0f} received for order {fresh.order_id} — "
            f"your order from {order.kitchen_name if order else 'the kitchen'} is CONFIRMED. 🍽️\n"
            f"I'll message you here when it's being cooked and on the way."
        )
        await execute_conversation_message_insert(
            session=session, phone=fresh.customer_phone, actor_role="CUSTOMER",
            direction="OUTBOUND", source="SYSTEM", message_text=confirm_msg,
            related_order_id=fresh.order_id,
        )

    # Outside the transaction: deliver + release the pause.
    clear_pending(payment.customer_phone)
    await send_whatsapp_text_message(payment.customer_phone, confirm_msg)
    logger.info(f"🟢 Settled payment {payment.payment_id} (txn {txn}) for order {payment.order_id}")
    return {"settled": True, "reason": "paid"}


async def check_payment_for_order(order: CustomerOrder) -> dict[str, Any]:
    """Check (and settle if paid) the newest PENDING payment on an order."""
    async with SessionFactory() as session:
        payment = (
            await session.execute(
                select(CustomerPayment)
                .where(CustomerPayment.order_id == order.order_id, CustomerPayment.status == "PENDING")
                .order_by(CustomerPayment.created_at.desc())
            )
        ).scalars().first()
        if payment is None:
            return {"settled": False, "reason": "no_pending_payment"}
    return await settle_payment_if_paid(payment)


async def payment_poller_loop(stop: asyncio.Event) -> None:
    """Scan recent PENDING payments and settle any whose Razorpay link is paid."""
    logger.info("💳 Payment poller started (15s interval)")
    while not stop.is_set():
        try:
            async with SessionFactory() as session:
                since = datetime.utcnow() - timedelta(minutes=POLL_WINDOW_MINS)
                pending = (
                    await session.execute(
                        select(CustomerPayment).where(
                            CustomerPayment.status == "PENDING",
                            CustomerPayment.created_at >= since,
                        ).order_by(CustomerPayment.created_at.asc()).limit(25)
                    )
                ).scalars().all()
            for p in pending:
                if not p.gateway_order_id or "mock" in (p.gateway_order_id or "").lower():
                    continue
                await settle_payment_if_paid(p)
        except Exception as e:
            logger.error(f"🔴 Payment poller iteration failed: {e}", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL_SECS)
        except asyncio.TimeoutError:
            pass
    logger.info("💳 Payment poller stopped")
