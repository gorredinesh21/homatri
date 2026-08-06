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

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import generate_id
from app.db.session import SessionFactory, transaction
from app.executors.customer import (
    execute_payment_record_creation,
    execute_payment_status_update,
)
from app.executors.driver import execute_driver_trip_initialization
from app.executors.master import (
    execute_cutoff_batch_lock_and_routes_creation,
    execute_hitl_session_create_or_resume,
    execute_meal_window_lock_and_creation,
    execute_outbound_whatsapp_enqueue,
    execute_system_audit_log,
)
from app.models.chef import ChefProfile
from app.models.customer import (
    CustomerOrder,
    CustomerOrderItem,
    CustomerPayment,
    CustomerProfile,
)
from app.models.driver import DriverProfile, DriverTripStatus
from app.models.system import SystemMealWindow
from app.services.maps_service import maps_service
from app.services.payment_service import razorpay_service

# Meal cutoffs (duplicated from app.tools.common to avoid a tools-package import cycle).
LUNCH_CUTOFF = time(11, 30)
DINNER_CUTOFF = time(18, 30)
COOK_MINUTES = 45   # kitchen prep before pickup
LEG_MINUTES = 10    # rough travel between stops


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


# =============================================================================
# TOOL: run_cutoff_batch  (Master · scheduled engine — the Flow 5 orchestrator)
#
# At cutoff: for each kitchen with CONFIRMED orders in the window, allocate a
# driver, optimize the route, create route+stops+stop-orders (flipping orders to
# BATCHED), write the driver trip, and dispatch the chef checklist + driver route.
# Pure orchestration — no LLM. Triggered by a scheduler (or the harness /cutoff).
# =============================================================================
class RunCutoffBatchInput(BaseModel):
    window: str = Field(..., description="'LUNCH' or 'DINNER'.")
    service_date: str = Field(..., description="ISO date of the batch, e.g. '2026-08-05'.")


async def _run_cutoff_batch(
    session: AsyncSession, *, window: str, service_date: date
) -> dict[str, Any]:
    """Batch every kitchen's CONFIRMED orders (window is NOT locked — re-runnable). {status, batches, ...}.

    Guard: no CONFIRMED orders -> NO_ORDERS. Idempotent at the ORDER level (a re-run
    finds nothing confirmed and returns NO_ORDERS; orders confirmed after a run still
    get batched next time). The window stays OPEN so ordering never gets blocked.
    """
    cutoff_time = LUNCH_CUTOFF if window.upper() == "LUNCH" else DINNER_CUTOFF
    cutoff_at = datetime.combine(service_date, cutoff_time)

    # NOTE: we do NOT lock the window for now (dev request) — the window stays OPEN
    # so cutoff is re-runnable and orders confirmed after a run still get batched.
    # Idempotency is at the ORDER level (only CONFIRMED orders are batched; a re-run
    # with nothing confirmed returns NO_ORDERS). Proper locking wires in later.
    win = (
        await session.execute(
            select(SystemMealWindow).where(
                SystemMealWindow.service_date == service_date,
                SystemMealWindow.meal_type == window,
            )
        )
    ).scalar_one_or_none()

    # CONFIRMED orders for this window/date, grouped by kitchen.
    orders = (
        await session.execute(
            select(CustomerOrder).where(
                CustomerOrder.status == "CONFIRMED",
                CustomerOrder.meal_window == window,
                CustomerOrder.service_date == service_date,
            )
        )
    ).scalars().all()
    if not orders:
        return {"status": "NO_ORDERS", "batches": [],
                "message": f"No confirmed orders for {window.lower()} on {service_date}."}

    by_chef: dict[str, list[CustomerOrder]] = defaultdict(list)
    for o in orders:
        by_chef[o.chef_phone].append(o)

    # Ensure the meal window row exists (OPEN); the batch executor locks it.
    if win is None:
        win = await execute_meal_window_lock_and_creation(
            session, service_date=service_date, meal_type=window, cutoff_at=cutoff_at, status="OPEN",
        )

    batches: list[dict[str, Any]] = []
    for chef_phone, chef_orders in by_chef.items():
        chef = await session.get(ChefProfile, chef_phone)

        # 1) driver
        alloc = await _allocate_driver(session, window=window, service_date=service_date, chef_phone=chef_phone)
        if alloc["status"] == "NO_DRIVER":
            await execute_system_audit_log(
                session, event_type="NO_DRIVER", source_role="MASTER", severity="WARN",
                payload={"chef_phone": chef_phone, "window": window, "service_date": str(service_date)},
            )
            batches.append({"chef_phone": chef_phone, "status": "NO_DRIVER"})
            continue
        driver_phone = alloc["driver_phone"]

        # 2) deliveries (one dropoff per order/customer) + route optimization
        deliveries = []
        for o in chef_orders:
            cust = await session.get(CustomerProfile, o.customer_phone)
            deliveries.append({"lat": float(cust.latitude), "lng": float(cust.longitude), "order": o, "cust": cust})
        origin = {"lat": float(chef.latitude), "lng": float(chef.longitude)}
        route = await _call_maps_route(origin=origin, stops=deliveries)
        ordered = [deliveries[i] for i in route["order"]]

        # 3) stops_data: kitchen PICKUP first, then optimized DROPOFFs
        stops_data: list[dict[str, Any]] = [{
            "stop_type": "PICKUP", "target_ref_id": chef_phone, "location_name": chef.kitchen_name,
            "address": chef.address, "latitude": origin["lat"], "longitude": origin["lng"],
            "estimated_arrival": cutoff_at + timedelta(minutes=COOK_MINUTES),
            "order_ids": [], "single_leg_maps_url": route["maps_url"],
        }]
        arrival = cutoff_at + timedelta(minutes=COOK_MINUTES)
        for d in ordered:
            arrival = arrival + timedelta(minutes=LEG_MINUTES)
            stops_data.append({
                "stop_type": "DROPOFF_GATE", "target_ref_id": d["order"].customer_phone,
                "location_name": d["cust"].name, "address": d["cust"].delivery_address,
                "latitude": d["lat"], "longitude": d["lng"], "estimated_arrival": arrival,
                "order_ids": [d["order"].order_id],
            })
        total_stops = len(stops_data)

        # 4) create route + stops + stop-orders (flips orders -> BATCHED)
        route_row = await execute_cutoff_batch_lock_and_routes_creation(
            session, window_id=win.window_id, driver_phone=driver_phone, service_date=service_date,
            meal_window=window, total_stops=total_stops, total_orders=len(chef_orders),
            total_distance_km=Decimal(str(route["total_distance_km"])),
            estimated_duration_mins=route["estimated_duration_mins"], stops_data=stops_data,
        )

        # 5) driver trip (now that route_id + total_stops exist)
        await execute_driver_trip_initialization(
            session, driver_phone=driver_phone, route_id=route_row.route_id,
            service_date=service_date, meal_window=window, total_stops=total_stops,
        )

        # 6) dispatch — chef cook checklist (order-wise + summary) + driver route
        items = (
            await session.execute(
                select(CustomerOrderItem).where(
                    CustomerOrderItem.order_id.in_([o.order_id for o in chef_orders])
                )
            )
        ).scalars().all()
        by_order: dict[str, list] = defaultdict(list)
        summary: dict[str, int] = defaultdict(int)
        for it in items:
            by_order[it.order_id].append(it)
            summary[it.dish_name] += it.quantity
        name_of = {d["order"].order_id: d["cust"].name for d in deliveries}

        order_blocks = []
        for n, o in enumerate(chef_orders, 1):
            lines = "\n".join(f"      • {it.quantity}× {it.dish_name}" for it in by_order.get(o.order_id, []))
            order_blocks.append(f"  {n}. {name_of.get(o.order_id, o.customer_phone)} · {o.order_id}\n{lines}")
        summary_lines = "\n".join(f"  • {qty}× {name}" for name, qty in summary.items())
        pickup_time = (cutoff_at + timedelta(minutes=COOK_MINUTES)).strftime("%I:%M %p")
        chef_msg = (
            f"🍳 {window.title()} batch locked — {len(chef_orders)} order(s).\n\n"
            f"ORDERS:\n" + "\n".join(order_blocks) + "\n\n"
            f"COOK SUMMARY:\n{summary_lines}\n\n"
            f"🛵 {alloc['driver_name']} picks up around {pickup_time}."
        )
        await execute_outbound_whatsapp_enqueue(
            session, recipient_phone=chef_phone, recipient_role="CHEF", message_text=chef_msg,
        )
        driver_msg = (
            f"🛵 New {window.lower()} route: {len(chef_orders)} orders, {total_stops} stops "
            f"(~{route['total_distance_km']} km). Pick up at {chef.kitchen_name}.\nRoute: {route['maps_url']}"
        )
        await execute_outbound_whatsapp_enqueue(
            session, recipient_phone=driver_phone, recipient_role="DRIVER", message_text=driver_msg,
        )

        await execute_system_audit_log(
            session, event_type="BATCH_CREATED", source_role="MASTER",
            payload={"route_id": route_row.route_id, "chef_phone": chef_phone,
                     "driver_phone": driver_phone, "orders": len(chef_orders), "stops": total_stops},
        )
        batches.append({
            "chef_phone": chef_phone, "kitchen_name": chef.kitchen_name, "route_id": route_row.route_id,
            "driver_phone": driver_phone, "orders": len(chef_orders), "stops": total_stops, "status": "BATCHED",
        })

    # Keep the window OPEN (the batch executor flips it to LOCKED_PROCESSING; undo
    # that for now so cutoff stays re-runnable). Locking is a later feature.
    if win is not None:
        win.status = "OPEN"
        win.locked_at = None
        await session.flush()

    n_batched = sum(1 for b in batches if b["status"] == "BATCHED")
    total_orders = sum(b.get("orders", 0) for b in batches if b["status"] == "BATCHED")
    return {
        "status": "BATCHED" if n_batched else "NO_DRIVER",
        "batches": batches,
        "total_orders": total_orders,
        "message": f"Cutoff for {window.lower()} {service_date}: {n_batched} kitchen(s) batched, {total_orders} order(s).",
    }


@tool("run_cutoff_batch", args_schema=RunCutoffBatchInput)
async def run_cutoff_batch(window: str, service_date: str) -> str:
    """Master engine: lock the window, batch every kitchen's confirmed orders, dispatch chef & driver."""
    async with transaction() as session:
        res = await _run_cutoff_batch(session, window=window, service_date=date.fromisoformat(service_date))
        return res["message"]


# =============================================================================
# TOOL: escalate_to_admin  (UNIVERSAL — bound to every agent)
#
# The one human-in-the-loop escape hatch. Any agent that's stuck (an exception it
# can't resolve after trying) records an escalation in the admin queue
# (system_hitl_sessions, waiting_on=ADMIN) + an audit entry. Deterministic tool;
# the *decision* to escalate is the agent's (LLM) judgment.
# =============================================================================
ESCALATION_TYPES = ("NO_DRIVER", "REPEATED_FAILURE", "AMBIGUOUS", "STUCK")


class EscalateToAdminInput(BaseModel):
    source_role: str = Field(..., description="Who is escalating: CUSTOMER / CHEF / DRIVER / MASTER.")
    escalation_type: str = Field(..., description="NO_DRIVER | REPEATED_FAILURE | AMBIGUOUS | STUCK.")
    summary: str = Field(..., description="Short human-readable description of what's wrong.")
    order_id: str | None = Field(default=None, description="Related order id, if any.")


async def _escalate_to_admin(
    session: AsyncSession, *, source_role: str, escalation_type: str, summary: str,
    order_id: str | None = None, refs: dict | None = None,
) -> dict[str, Any]:
    """Record an escalation for the admin queue + audit. {status: ESCALATED, hitl_id, message}."""
    etype = escalation_type if escalation_type in ESCALATION_TYPES else "STUCK"
    hitl = await execute_hitl_session_create_or_resume(
        session, thread_id=order_id or generate_id("esc"), interrupt_type="ESCALATION",
        waiting_on_role="ADMIN", order_id=order_id,
        payload={"type": etype, "summary": summary, "source_role": source_role, "refs": refs or {}},
        status="WAITING", expires_in_mins=24 * 60,
    )
    await execute_system_audit_log(
        session, event_type="ESCALATED", source_role=source_role, target_role="ADMIN",
        order_id=order_id, payload={"type": etype, "summary": summary}, severity="HIGH")
    return {"status": "ESCALATED", "hitl_id": hitl.session_id,
            "message": f"I've flagged this to the admin team (ref {hitl.session_id}). They'll follow up shortly."}


@tool("escalate_to_admin", args_schema=EscalateToAdminInput)
async def escalate_to_admin(source_role: str, escalation_type: str, summary: str, order_id: str | None = None) -> str:
    """Escalate to a human admin when you're stuck and can't resolve something yourself. Last resort."""
    async with transaction() as session:
        res = await _escalate_to_admin(
            session, source_role=source_role, escalation_type=escalation_type, summary=summary, order_id=order_id)
        return res["message"]
