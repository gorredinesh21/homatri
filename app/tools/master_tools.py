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

from datetime import date
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionFactory, transaction
from app.executors.customer import (
    execute_payment_record_creation,
    execute_payment_status_update,
)
from app.models.customer import CustomerOrder, CustomerPayment
from app.models.driver import DriverProfile, DriverTripStatus
from app.services.maps_service import maps_service
from app.services.payment_service import razorpay_service


# =============================================================================
# HELPER: call_maps_route  (Master · same-domain · external helper, no DB)
#
# Optimize a kitchen -> deliveries route. Real Google Routes API when a key is
# set, else a deterministic nearest-neighbour mock (see app/services/maps_service).
# Used internally by run_cutoff_batch; not an LLM-facing @tool (raw coordinates).
# =============================================================================
async def _call_maps_route(
    *, origin: dict[str, float], stops: list[dict[str, Any]]
) -> dict[str, Any]:
    """Return {mode, order, total_distance_km, estimated_duration_mins, maps_url}.

    `order` is the stops' original indices in optimized visit order.
    """
    return await maps_service.optimize_route(origin=origin, stops=stops)


# =============================================================================
# TOOL: allocate_driver  (Master · cross-domain · READ selection)
#
# Picks one available driver for a batch (1 chef -> 1 driver per window). Pure
# selection — it does NOT write the trip (that needs a route_id + total_stops,
# which only exist after the route is built). run_cutoff_batch creates the trip
# via execute_driver_trip_initialization once the route exists.
# =============================================================================
class AllocateDriverInput(BaseModel):
    window: str = Field(..., description="'LUNCH' or 'DINNER'.")
    service_date: str = Field(..., description="ISO date of the batch, e.g. '2026-08-05'.")
    chef_phone: str | None = Field(default=None, description="The batch's kitchen (for logging; not used for selection).")


async def _allocate_driver(
    session: AsyncSession, *, window: str, service_date: date, chef_phone: str | None = None
) -> dict[str, Any]:
    """Select one available driver for the window. {status: ASSIGNED|NO_DRIVER, ...}.

    Available = active_status AND is_on_shift AND not already on a trip for this
    (service_date, window). No location match — DriverProfile has no coordinates.
    Guard: none available -> NO_DRIVER (caller escalates via escalate_to_admin).
    """
    taken = {
        r for (r,) in (
            await session.execute(
                select(DriverTripStatus.driver_phone).where(
                    DriverTripStatus.service_date == service_date,
                    DriverTripStatus.meal_window == window,
                )
            )
        ).all()
    }
    drivers = (
        await session.execute(
            select(DriverProfile)
            .where(DriverProfile.active_status.is_(True), DriverProfile.is_on_shift.is_(True))
            .order_by(DriverProfile.driver_phone)
        )
    ).scalars().all()
    available = [d for d in drivers if d.driver_phone not in taken]
    if not available:
        return {
            "status": "NO_DRIVER",
            "message": (
                f"No driver available for {window.lower()} on {service_date}. "
                f"Escalate to admin (escalate_to_admin)."
            ),
        }

    d = available[0]
    return {
        "status": "ASSIGNED",
        "driver_phone": d.driver_phone,
        "driver_name": d.driver_name,
        "vehicle": f"{d.vehicle_type} {d.vehicle_number}",
        "message": f"Driver {d.driver_name} ({d.driver_phone}) assigned for {window.lower()} on {service_date}.",
    }


@tool("allocate_driver", args_schema=AllocateDriverInput)
async def allocate_driver(window: str, service_date: str, chef_phone: str | None = None) -> str:
    """Master: pick an available driver for a batch window (1 chef -> 1 driver)."""
    async with SessionFactory() as session:
        res = await _allocate_driver(
            session, window=window, service_date=date.fromisoformat(service_date), chef_phone=chef_phone,
        )
        return res["message"]


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
