"""Master / System domain write executors (Category 4 & Shared Runtime).

Single-owner write executors for system_* and conversation_messages tables.
Handles meal window locks, route creation, stop updates, HITL sessions, payment webhooks,
outbound queueing, chat log inserts, and system audit logs.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import generate_id
from app.executors.customer import execute_order_status_transition
from app.models.shared import ConversationMessage
from app.models.system import (
    SystemAgentLog,
    SystemDeliveryRoute,
    SystemDeliveryStop,
    SystemDeliveryStopOrder,
    SystemHitlSession,
    SystemMealWindow,
    SystemOutboundQueue,
    SystemPaymentWebhookEvent,
)


# =============================================================================
# EXECUTOR 1: Meal Window Lock & Creation
# =============================================================================
async def execute_meal_window_lock_and_creation(
    session: AsyncSession,
    *,
    service_date: date,
    meal_type: str = "LUNCH",
    cutoff_at: datetime,
    status: str = "OPEN",
) -> SystemMealWindow:
    """Executor #1 — Create or update a system meal window (idempotent slot)."""
    window = (
        await session.execute(
            select(SystemMealWindow).where(
                SystemMealWindow.service_date == service_date,
                SystemMealWindow.meal_type == meal_type,
            )
        )
    ).scalar_one_or_none()

    if window is None:
        window_id = generate_id("win")
        window = SystemMealWindow(
            window_id=window_id,
            service_date=service_date,
            meal_type=meal_type,
            cutoff_at=cutoff_at,
            status=status,
            total_confirmed_orders=0,
            total_revenue=Decimal("0.00"),
        )
        session.add(window)
    else:
        window.status = status
        if status == "LOCKED_PROCESSING" and window.locked_at is None:
            window.locked_at = datetime.now()
        elif status == "COMPLETED" and window.completed_at is None:
            window.completed_at = datetime.now()

    await session.flush()
    return window


# =============================================================================
# EXECUTOR 2: Cutoff Batch Lock & Route Dispatch Creation
# =============================================================================
async def execute_cutoff_batch_lock_and_routes_creation(
    session: AsyncSession,
    *,
    window_id: str,
    driver_phone: str,
    service_date: date,
    meal_window: str,
    total_stops: int,
    total_orders: int,
    total_distance_km: Decimal = Decimal("0.00"),
    estimated_duration_mins: int = 30,
    stops_data: list[dict],
) -> SystemDeliveryRoute:
    """Executor #2 — Lock cutoff window, create GCP delivery route, stops, and stop-orders.

    Also transitions all confirmed orders in the batch to 'BATCHED' via Customer DW1!
    """
    # Lock the meal window (honest to this executor's name; sets locked_at).
    window = await session.get(SystemMealWindow, window_id)
    if window and window.status == "OPEN":
        window.status = "LOCKED_PROCESSING"
        if window.locked_at is None:
            window.locked_at = datetime.now()
        await session.flush()

    route_id = generate_id("rt")
    route = SystemDeliveryRoute(
        route_id=route_id,
        window_id=window_id,
        driver_phone=driver_phone,
        service_date=service_date,
        meal_window=meal_window,
        total_stops=total_stops,
        total_orders=total_orders,
        total_distance_km=total_distance_km,
        estimated_duration_mins=estimated_duration_mins,
        status="ASSIGNED",
    )
    session.add(route)
    await session.flush()

    for idx, stop_info in enumerate(stops_data, start=1):
        stop_id = generate_id("stp")
        est_arr = stop_info["estimated_arrival"]
        if isinstance(est_arr, str):
            if "T" in est_arr:
                est_arr = datetime.fromisoformat(est_arr)
            else:
                parts = est_arr.split(":")
                est_arr = datetime(service_date.year, service_date.month, service_date.day, int(parts[0]), int(parts[1]))
        stop = SystemDeliveryStop(
            stop_id=stop_id,
            route_id=route_id,
            stop_index=idx,
            stop_type=stop_info.get("stop_type", "DROPOFF_GATE"),
            target_ref_id=stop_info["target_ref_id"],
            location_name=stop_info["location_name"],
            address=stop_info["address"],
            latitude=Decimal(str(stop_info["latitude"])),
            longitude=Decimal(str(stop_info["longitude"])),
            single_leg_maps_url=stop_info.get("single_leg_maps_url"),
            estimated_arrival=est_arr,
            status="PENDING",
        )


        session.add(stop)
        await session.flush()

        # Link order IDs to stop and trigger DW1 (CONFIRMED -> BATCHED)
        for order_id in stop_info.get("order_ids", []):
            stop_order = SystemDeliveryStopOrder(
                stop_id=stop_id,
                order_id=order_id,
            )
            session.add(stop_order)

            # Delegate to Customer DW1: transition order to BATCHED
            await execute_order_status_transition(
                session,
                order_id=order_id,
                target_status="BATCHED",
                actor_role="MASTER_BATCH_CUTOFF",
                reason=f"Assigned to route {route_id} stop {idx}",
            )

    await session.flush()
    return route


# =============================================================================
# EXECUTOR 3: Stop Status Update
# =============================================================================
async def execute_stop_status_update(
    session: AsyncSession,
    *,
    stop_id: str,
    target_status: str,
    actual_arrival: datetime | None = None,
) -> SystemDeliveryStop:
    """Executor #3 — Single owner for updating delivery stop status (PENDING -> ARRIVED -> COMPLETED)."""
    stop = await session.get(SystemDeliveryStop, stop_id)
    assert stop is not None, f"Delivery stop not found: {stop_id}"
    assert target_status in {"PENDING", "ARRIVED", "COMPLETED"}, f"Invalid stop target status: {target_status}"

    stop.status = target_status
    if target_status == "ARRIVED":
        stop.actual_arrival = actual_arrival or datetime.now()

    await session.flush()
    return stop


# =============================================================================
# EXECUTOR 4: HITL Session Create or Resume
# =============================================================================
async def execute_hitl_session_create_or_resume(
    session: AsyncSession,
    *,
    thread_id: str,
    interrupt_type: str,
    waiting_on_role: str,
    waiting_on_phone: str | None = None,
    order_id: str | None = None,
    payload: dict | None = None,
    default_on_expiry: dict | None = None,
    expires_in_mins: int = 15,
    status: str = "WAITING",
) -> SystemHitlSession:
    """Executor #4 — Create a new HITL pause session or update/resume an existing one.

    Calculates expires_at = now() + expires_in_mins (15-min TTL standard).
    """
    now = datetime.now()
    session_id = generate_id("hitl")
    hitl = SystemHitlSession(
        session_id=session_id,
        thread_id=thread_id,
        interrupt_type=interrupt_type,
        waiting_on_role=waiting_on_role,
        waiting_on_phone=waiting_on_phone,
        order_id=order_id,
        payload=payload or {},
        default_on_expiry=default_on_expiry or {},
        status=status,
        expires_at=now + timedelta(minutes=expires_in_mins),
    )
    session.add(hitl)
    await session.flush()
    return hitl


# =============================================================================
# EXECUTOR 5: Payment Webhook Idempotency Logger
# =============================================================================
async def execute_payment_webhook_idempotency_log(
    session: AsyncSession,
    *,
    gateway: str = "RAZORPAY",
    gateway_event_id: str,
    event_type: str,
    payment_id: str | None = None,
    order_id: str | None = None,
    signature_verified: bool = True,
    raw_payload: dict | None = None,
) -> tuple[SystemPaymentWebhookEvent, bool]:
    """Executor #5 — Log payment gateway webhook with strict idempotency.

    Returns tuple (event_record, is_new_event).
    If gateway_event_id already processed, returns (existing_record, False).
    """
    existing = (
        await session.execute(
            select(SystemPaymentWebhookEvent).where(
                SystemPaymentWebhookEvent.gateway_event_id == gateway_event_id
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        return (existing, False)  # Duplicate event; skip processing

    event_id = generate_id("evt")
    event = SystemPaymentWebhookEvent(
        event_id=event_id,
        gateway=gateway,
        gateway_event_id=gateway_event_id,
        event_type=event_type,
        payment_id=payment_id,
        order_id=order_id,
        signature_verified=signature_verified,
        raw_payload=raw_payload or {},
        processing_status="RECEIVED",
    )
    session.add(event)
    await session.flush()
    return (event, True)


# =============================================================================
# EXECUTOR 6: Outbound WhatsApp Queue Enqueue
# =============================================================================
async def execute_outbound_whatsapp_enqueue(
    session: AsyncSession,
    *,
    recipient_phone: str,
    recipient_role: str,
    message_text: str,
    message_type: str = "TEXT",
    template_name: str | None = None,
    related_order_id: str | None = None,
) -> SystemOutboundQueue:
    """Executor #6 — Enqueue an outbound WhatsApp notification for background dispatch."""
    message_id = generate_id("out")
    outbound = SystemOutboundQueue(
        message_id=message_id,
        recipient_phone=recipient_phone,
        recipient_role=recipient_role,
        message_text=message_text,
        message_type=message_type,
        template_name=template_name,
        related_order_id=related_order_id,
        status="QUEUED",
        attempts=0,
    )
    session.add(outbound)
    await session.flush()
    return outbound


# =============================================================================
# EXECUTOR 7: Conversation Message Insert (Shared Runtime Unified Chat Log)
# =============================================================================
async def execute_conversation_message_insert(
    session: AsyncSession,
    *,
    phone: str,
    actor_role: str,
    direction: str,
    source: str,
    message_text: str | None = None,
    message_type: str = "TEXT",
    latitude: float | None = None,
    longitude: float | None = None,
    media_ref: str | None = None,
    related_order_id: str | None = None,
    wa_message_id: str | None = None,
    raw_payload: dict | None = None,
) -> ConversationMessage:
    """Executor #7 — Insert a chat log entry into conversation_messages (Unified Chat Log).

    Insert-only ledger. Read by Context Assembler before every LLM call.
    """
    message_id = generate_id("msg")
    msg = ConversationMessage(
        message_id=message_id,
        phone=phone,
        actor_role=actor_role,
        direction=direction,
        source=source,
        message_type=message_type,
        message_text=message_text,
        latitude=Decimal(str(latitude)) if latitude is not None else None,
        longitude=Decimal(str(longitude)) if longitude is not None else None,
        media_ref=media_ref,
        related_order_id=related_order_id,
        wa_message_id=wa_message_id,
        raw_payload=raw_payload or {},
    )
    session.add(msg)
    await session.flush()
    return msg


# =============================================================================
# EXECUTOR 8: System Audit Log
# =============================================================================
async def execute_system_audit_log(
    session: AsyncSession,
    *,
    event_type: str,
    source_role: str,
    target_role: str | None = None,
    order_id: str | None = None,
    payload: dict | None = None,
    severity: str = "INFO",
) -> SystemAgentLog:
    """Executor #8 — Record an operational audit event in system_agent_logs."""
    log_id = generate_id("log")
    audit = SystemAgentLog(
        log_id=log_id,
        event_type=event_type,
        source_role=source_role,
        target_role=target_role,
        order_id=order_id,
        payload=payload or {},
        severity=severity,
    )
    session.add(audit)
    await session.flush()
    return audit
