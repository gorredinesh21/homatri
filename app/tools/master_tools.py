"""Master-domain tools — the operator that owns the payment gateway (Flow 4).

The customer never touches Razorpay. Master mints the link AND receives the
webhook. Today there is no Master *agent*; the Customer tool `request_payment`
calls `_mint_payment_link` as a deterministic relay, and the `/pay` callback
(via the confirm_payment resume handler) calls `_process_payment_webhook`.
When the Master agent is built, these two `@tool`s bind to it unchanged.

Same pattern as the customer tools: inner `_fn(session, ...)` (unit-testable)
+ a `@tool` wrapper that opens a transaction.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import transaction
from app.executors.customer import (
    execute_payment_record_creation,
    execute_payment_status_update,
)
from app.models.customer import CustomerOrder, CustomerPayment
from app.services.payment_service import razorpay_service


# =============================================================================
# TOOL: mint_payment_link  (Master · cross-domain · WRITE via delegate)
# =============================================================================
class MintPaymentLinkInput(BaseModel):
    order_id: str = Field(..., description="The order to mint a payment link for, e.g. 'ord_...'.")


async def _mint_payment_link(session: AsyncSession, *, order_id: str) -> dict[str, Any]:
    """Mint a gateway payment link + a PENDING payment record. {status, payment_id?, link?, amount?, message}.

    Guards (defensive — the customer tool guards too):
      - order not found           -> NOT_FOUND
      - order not PENDING_PAYMENT  -> NOT_PAYABLE
      - amount <= 0                -> EMPTY
    """
    order = await session.get(CustomerOrder, order_id)
    if order is None:
        return {"status": "NOT_FOUND", "message": f"Order {order_id} not found."}
    if order.status != "PENDING_PAYMENT":
        return {"status": "NOT_PAYABLE", "message": f"Order {order_id} is {order.status.lower()} — nothing to mint."}
    if order.total_amount is None or float(order.total_amount) <= 0:
        return {"status": "EMPTY", "message": f"Order {order_id} has no payable amount."}

    link = await razorpay_service.create_payment_link(
        order_id=order.order_id, amount_in_rupees=float(order.total_amount),
        customer_phone=order.customer_phone,
        description=f"Homaatri order {order.order_id} — {order.kitchen_name}",
    )

    # Reuse an existing PENDING payment (re-mint) rather than stacking duplicates.
    payment = (
        await session.execute(
            select(CustomerPayment).where(
                CustomerPayment.order_id == order.order_id,
                CustomerPayment.status == "PENDING",
            )
        )
    ).scalars().first()
    if payment is not None:
        payment.payment_link_url = link["short_url"]
        payment.gateway_order_id = link["payment_link_id"]
        await session.flush()
    else:
        payment = await execute_payment_record_creation(
            session, order_id=order.order_id, amount_due=order.total_amount,
            gateway_order_id=link["payment_link_id"], payment_link_url=link["short_url"],
        )

    return {
        "status": "MINTED",
        "order_id": order.order_id,
        "payment_id": payment.payment_id,
        "amount": float(order.total_amount),
        "link": link["short_url"],
        "message": f"Payment link minted for order {order.order_id} (₹{float(order.total_amount):.0f}).",
    }


@tool("mint_payment_link", args_schema=MintPaymentLinkInput)
async def mint_payment_link(order_id: str) -> str:
    """Master: create a payment link for an order and store a PENDING payment record."""
    async with transaction() as session:
        res = await _mint_payment_link(session, order_id=order_id)
        return res["message"]


# =============================================================================
# TOOL: process_payment_webhook  (Master · cross-domain · WRITE via delegate)
# =============================================================================
class ProcessPaymentWebhookInput(BaseModel):
    payment_id: str = Field(..., description="The payment record the gateway callback is for, e.g. 'pay_...'.")
    transaction_id: str | None = Field(default=None, description="Gateway transaction id, if any.")
    signature: str | None = Field(default=None, description="Gateway HMAC signature, if any (verified in real mode).")


async def _process_payment_webhook(
    session: AsyncSession, *, payment_id: str, transaction_id: str | None = None,
    signature: str | None = None, raw_body: bytes | None = None,
) -> dict[str, Any]:
    """Verify + confirm a payment: mark PAID -> cascade order to CONFIRMED (DW2 -> DW1).

    Idempotent: a repeat callback for an already-PAID payment is a no-op.
    Returns {status, order_id?, message}.
    """
    # Signature check (mock mode returns True; real mode verifies HMAC).
    if signature is not None and raw_body is not None:
        if not razorpay_service.verify_webhook_signature(raw_body, signature):
            return {"status": "BAD_SIGNATURE", "message": "Payment webhook signature verification failed."}

    payment = await session.get(CustomerPayment, payment_id)
    if payment is None:
        return {"status": "NOT_FOUND", "message": f"Payment {payment_id} not found."}
    if payment.status == "PAID":
        return {"status": "ALREADY_PAID", "order_id": payment.order_id, "message": "Payment already confirmed."}

    await execute_payment_status_update(
        session, payment_id=payment_id, target_status="PAID", gateway_transaction_id=transaction_id,
    )
    return {"status": "PAID", "order_id": payment.order_id, "message": f"Order {payment.order_id} confirmed."}


@tool("process_payment_webhook", args_schema=ProcessPaymentWebhookInput)
async def process_payment_webhook(payment_id: str, transaction_id: str | None = None, signature: str | None = None) -> str:
    """Master: verify a gateway callback, mark the payment PAID, and confirm the order."""
    async with transaction() as session:
        res = await _process_payment_webhook(
            session, payment_id=payment_id, transaction_id=transaction_id, signature=signature,
        )
        return res["message"]
