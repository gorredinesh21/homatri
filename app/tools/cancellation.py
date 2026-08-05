"""Order cancellation — direct before cooking, chef-approved once cooking has started.

cancel_order (Customer): looks at the customer's last order and either cancels it
outright (pre-cook), rejects it (packed/on the way), or — if COOKING — asks the chef
to approve. respond_to_cancellation (Chef) approves/denies a cook-time request.

Refunds are NEVER processed here: a cancelled *paid* order is flagged (audit entry
`ORDER_CANCELLED` with `refund_due`), and the admin settles refunds at end of day.
Harness note: negotiation state is in-memory; the outcome is pushed to the customer
via the outbound queue (widgets poll it).
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import transaction
from app.executors.customer import execute_order_status_transition
from app.executors.master import execute_outbound_whatsapp_enqueue, execute_system_audit_log
from app.models.customer import CustomerOrder, CustomerPayment

TERMINAL = ("DELIVERED", "CANCELLED")
TOO_LATE = ("PACKED", "PICKED_UP")
PRE_COOK = ("DRAFT_CART", "PENDING_PAYMENT", "CONFIRMED", "BATCHED")

# In-memory cancel-negotiation store, keyed by customer_phone.
_cancellations: dict[str, dict[str, Any]] = {}


def _open_cancel_for_chef(chef_phone: str) -> dict | None:
    for n in _cancellations.values():
        if n["chef_phone"] == chef_phone and n["status"] == "WAITING_CHEF":
            return n
    return None


def clear_cancellation(phone: str) -> None:
    _cancellations.pop(phone, None)


async def _do_cancel(session: AsyncSession, *, order: CustomerOrder, reason: str, actor_role: str) -> float:
    """Transition the order to CANCELLED, flag any owed refund, and log it for the admin.

    Returns the refund amount owed (0 if the order was never paid). No refund is processed.
    """
    await execute_order_status_transition(
        session, order_id=order.order_id, target_status="CANCELLED", actor_role=actor_role, reason=reason)
    paid = (
        await session.execute(
            select(CustomerPayment).where(
                CustomerPayment.order_id == order.order_id, CustomerPayment.status == "PAID")
        )
    ).scalars().first()
    refund_due = float(paid.amount_paid or paid.amount_due) if paid is not None else 0.0
    await execute_system_audit_log(
        session, event_type="ORDER_CANCELLED", source_role=actor_role, order_id=order.order_id,
        payload={"reason": reason, "was_paid": paid is not None, "refund_due": refund_due},
        severity="INFO",
    )
    return refund_due


def _cancelled_msg(order: CustomerOrder, refund_due: float) -> str:
    base = f"Your order {order.order_id} from {order.kitchen_name} has been cancelled."
    if refund_due:
        return base + f" You paid ₹{refund_due:.0f} — you'll be refunded within 24 hours."
    return base


# =============================================================================
# TOOL: cancel_order  (Customer)
# =============================================================================
class CancelOrderInput(BaseModel):
    customer_phone: str = Field(..., description="Normalized 10-digit customer phone.")
    reason: str | None = Field(default=None, description="Why the customer is cancelling (optional).")


async def _cancel_order(session: AsyncSession, *, customer_phone: str, reason: str | None = None) -> dict[str, Any]:
    """Cancel the customer's last order. {status, refund_due?, message}.

    - pre-cook (PENDING_PAYMENT/CONFIRMED/BATCHED) -> cancel now
    - COOKING                                       -> ask the chef (AWAITING_CHEF)
    - PACKED/PICKED_UP                              -> TOO_LATE
    - DELIVERED/CANCELLED                           -> CANNOT_CANCEL
    """
    reason = (reason or "Customer requested cancellation").strip()
    order = (
        await session.execute(
            select(CustomerOrder).where(CustomerOrder.customer_phone == customer_phone)
            .order_by(CustomerOrder.created_at.desc())
        )
    ).scalars().first()
    if order is None:
        return {"status": "NO_ORDER", "message": "You don't have any orders to cancel."}

    st = order.status
    if st in TERMINAL:
        return {"status": "CANNOT_CANCEL", "message": f"Order {order.order_id} is {st.lower()} — it can't be cancelled."}
    if st in TOO_LATE:
        return {"status": "TOO_LATE", "message": f"Order {order.order_id} is {st.lower()} — it's packed / on the way, too late to cancel."}
    if st == "COOKING":
        _cancellations[customer_phone] = {
            "order_id": order.order_id, "chef_phone": order.chef_phone, "kitchen_name": order.kitchen_name,
            "customer_phone": customer_phone, "reason": reason, "status": "WAITING_CHEF",
        }
        await execute_outbound_whatsapp_enqueue(
            session, recipient_phone=order.chef_phone, recipient_role="CHEF", related_order_id=order.order_id,
            message_text=(f"🚫 Cancellation request for order {order.order_id} (already cooking). "
                          f"Reason: {reason}. Reply 'approve' or 'deny'."))
        return {"status": "AWAITING_CHEF",
                "message": "The kitchen has already started cooking — I've asked them if it can still be cancelled. I'll let you know shortly."}

    refund = await _do_cancel(session, order=order, reason=reason, actor_role="CUSTOMER")
    return {"status": "CANCELLED", "refund_due": refund, "message": _cancelled_msg(order, refund)}


@tool("cancel_order", args_schema=CancelOrderInput)
async def cancel_order(customer_phone: str, reason: str | None = None) -> str:
    """Cancel the customer's order (chef-approved if it's already cooking); flags any refund for the admin."""
    async with transaction() as session:
        res = await _cancel_order(session, customer_phone=customer_phone, reason=reason)
        return res["message"]


# =============================================================================
# TOOL: respond_to_cancellation  (Chef)
# =============================================================================
class RespondToCancellationInput(BaseModel):
    chef_phone: str = Field(..., description="Normalized 10-digit chef phone.")
    decision: str = Field(..., description="'approve' or 'deny'.")


async def _respond_to_cancellation(session: AsyncSession, *, chef_phone: str, decision: str) -> dict[str, Any]:
    """Chef approves/denies a cook-time cancellation; resolves + notifies the customer.

    Guards: no open request -> NO_REQUEST.
    """
    n = _open_cancel_for_chef(chef_phone)
    if n is None:
        return {"status": "NO_REQUEST", "message": "You have no pending cancellation requests."}
    d = decision.strip().upper()
    cust = n["customer_phone"]
    order = await session.get(CustomerOrder, n["order_id"])

    if d in ("APPROVE", "APPROVED", "YES", "OK"):
        refund = await _do_cancel(session, order=order, reason=n["reason"], actor_role="CHEF")
        n["status"] = "RESOLVED"
        await execute_outbound_whatsapp_enqueue(
            session, recipient_phone=cust, recipient_role="CUSTOMER", related_order_id=order.order_id,
            message_text=(f"✅ The kitchen approved your cancellation. Order {order.order_id} is cancelled."
                          + (f" Refund of ₹{refund:.0f} within 24 hours." if refund else "")))
        return {"status": "APPROVED", "message": f"Cancellation approved — order {order.order_id} cancelled, customer notified."}

    if d in ("DENY", "DENIED", "NO", "REJECT"):
        n["status"] = "RESOLVED"
        await execute_outbound_whatsapp_enqueue(
            session, recipient_phone=cust, recipient_role="CUSTOMER", related_order_id=order.order_id,
            message_text=f"❌ The kitchen has already cooked order {order.order_id}, so it can't be cancelled. Sorry!")
        return {"status": "DENIED", "message": "Cancellation denied — customer notified."}

    return {"status": "INVALID", "message": "Reply 'approve' or 'deny'."}


@tool("respond_to_cancellation", args_schema=RespondToCancellationInput)
async def respond_to_cancellation(chef_phone: str, decision: str) -> str:
    """Chef: approve or deny a customer's request to cancel an order that's already cooking."""
    async with transaction() as session:
        res = await _respond_to_cancellation(session, chef_phone=chef_phone, decision=decision)
        return res["message"]
