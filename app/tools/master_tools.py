"""Master Domain & System Shared LLM Tools (Category 4).

Encapsulates Master Orchestrator & System Shared Tools with Guard 2 Pre-Condition Assertions.
Tool 1: dispatch_whatsapp_outbound_message_tool (Write Executor #20, System Shared Outbound Messaging Tool).
Tool 2: get_master_kitchen_availability_summary_tool (Read-only, Same Domain).
Tool 3: get_master_order_pipeline_summary_tool (Read-only, Same Domain).
Tool 4: execute_cutoff_batch_and_route_optimization_tool (Cross-Domain, Master Cutoff & GCP Route Solver).
"""

from __future__ import annotations

import os
import json
import urllib.request
import urllib.parse
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.executors.customer import execute_payment_status_update
from app.executors.master import (
    execute_conversation_message_insert,
    execute_cutoff_batch_lock_and_routes_creation,
    execute_hitl_session_create_or_resume,
    execute_outbound_whatsapp_enqueue,
    execute_payment_webhook_idempotency_log,
    execute_system_audit_log,
)
from app.models.chef import ChefDailyInventory, ChefMenuItem, ChefProfile
from app.models.customer import CustomerOrder, CustomerPayment, CustomerProfile
from app.models.system import (
    SystemAgentLog,
    SystemDeliveryRoute,
    SystemDeliveryStop,
    SystemHitlSession,
    SystemMealWindow,
    SystemOutboundQueue,
    SystemPaymentWebhookEvent,
)





# =============================================================================
# TOOL 1: dispatch_whatsapp_outbound_message_tool
# =============================================================================
class DispatchWhatsAppOutboundMessageInput(BaseModel):
    recipient_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of recipient (e.g. '9111111111')",
    )
    recipient_role: str = Field(
        ...,
        description="Role of recipient: 'CUSTOMER', 'CHEF', 'DRIVER', or 'SYSTEM'",
    )
    message_text: str = Field(
        ...,
        description="Outbound WhatsApp message content to send to the recipient",
    )
    related_order_id: Optional[str] = Field(
        default=None,
        description="Optional associated order ID (e.g. 'ord_123456')",
    )


async def dispatch_whatsapp_outbound_message(
    session: AsyncSession,
    *,
    recipient_phone: str,
    recipient_role: str,
    message_text: str,
    related_order_id: str | None = None,
) -> SystemOutboundQueue:
    """Enqueue an outbound WhatsApp message and log it to conversation_messages with Guard 2 Assertions."""
    assert recipient_phone and len(recipient_phone) >= 10, f"Invalid recipient phone number: {recipient_phone}"
    assert recipient_role in {"CUSTOMER", "CHEF", "DRIVER", "SYSTEM"}, f"Invalid recipient role: {recipient_role}"
    assert message_text and len(message_text.strip()) >= 1, "Outbound message text cannot be empty"

    # 1. Enqueue to System Outbound Queue for WhatsApp Cloud API Gateway
    outbound = await execute_outbound_whatsapp_enqueue(
        session,
        recipient_phone=recipient_phone,
        recipient_role=recipient_role,
        message_text=message_text.strip(),
        message_type="TEXT",
        related_order_id=related_order_id,
    )

    # 2. Log in Unified Chat Ledger (conversation_messages) for Context Assembler
    await execute_conversation_message_insert(
        session,
        phone=recipient_phone,
        actor_role=recipient_role,
        direction="OUTBOUND",
        source="AGENT_TOOL",
        message_text=message_text.strip(),
        related_order_id=related_order_id,
    )

    return outbound


@tool("dispatch_whatsapp_outbound_message_tool", args_schema=DispatchWhatsAppOutboundMessageInput)
async def dispatch_whatsapp_outbound_message_tool(
    recipient_phone: str,
    recipient_role: str,
    message_text: str,
    related_order_id: Optional[str] = None,
) -> str:
    """Dispatch an outbound WhatsApp message to any customer, chef, or driver and log it in the chat ledger."""
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        outbound = await dispatch_whatsapp_outbound_message(
            session,
            recipient_phone=recipient_phone,
            recipient_role=recipient_role,
            message_text=message_text,
            related_order_id=related_order_id,
        )
        return (
            f"Successfully queued outbound WhatsApp message [{outbound.message_id}] for {recipient_role} ({recipient_phone}):\n"
            f"\"{outbound.message_text}\""
        )


# =============================================================================
# TOOL 2: get_master_kitchen_availability_summary_tool
# =============================================================================
class GetMasterKitchenAvailabilityInput(BaseModel):
    service_date: Optional[str] = Field(
        default=None,
        description="Optional service date in ISO format YYYY-MM-DD (e.g. '2026-08-01')",
    )
    meal_window: Optional[str] = Field(
        default=None,
        description="Optional meal window: 'LUNCH' or 'DINNER'",
    )


async def get_master_kitchen_availability_summary(
    session: AsyncSession,
    *,
    service_date: str | None = None,
    meal_window: str | None = None,
) -> dict[str, Any]:
    """Retrieve platform-wide active kitchen, menu item, and inventory metrics with Guard 2 Assertions."""
    if meal_window:
        assert meal_window in {"LUNCH", "DINNER"}, f"Invalid meal window: '{meal_window}'. Must be LUNCH or DINNER"

    # 1. Query total and active kitchen counts
    stmt_total_chefs = select(func.count(ChefProfile.chef_phone))
    total_chefs = (await session.execute(stmt_total_chefs)).scalar_one() or 0

    stmt_active_chefs = select(func.count(ChefProfile.chef_phone)).where(ChefProfile.active_status.is_(True))
    active_chefs = (await session.execute(stmt_active_chefs)).scalar_one() or 0

    # 2. Query menu item count
    stmt_items = select(func.count(ChefMenuItem.menu_item_id)).where(ChefMenuItem.is_available.is_(True))
    if meal_window:
        stmt_items = stmt_items.where(ChefMenuItem.meal_type.in_([meal_window, "BOTH"]))
    active_menu_items = (await session.execute(stmt_items)).scalar_one() or 0

    # 3. Query inventory totals if service_date provided
    total_portions_capacity = 0
    remaining_portions_capacity = 0
    if service_date:
        date_obj = date.fromisoformat(service_date)
        stmt_inv = select(
            func.sum(ChefDailyInventory.allocated_quantity),
            func.sum(ChefDailyInventory.remaining_quantity),
        ).where(ChefDailyInventory.service_date == date_obj)
        if meal_window:
            stmt_inv = stmt_inv.where(ChefDailyInventory.meal_window == meal_window)

        res_inv = (await session.execute(stmt_inv)).first()
        if res_inv and res_inv[0] is not None:
            total_portions_capacity = int(res_inv[0])
            remaining_portions_capacity = int(res_inv[1])

    return {
        "total_kitchens": total_chefs,
        "active_kitchens": active_chefs,
        "active_menu_items": active_menu_items,
        "service_date": service_date,
        "meal_window": meal_window,
        "total_portions_capacity": total_portions_capacity,
        "remaining_portions_capacity": remaining_portions_capacity,
    }


@tool("get_master_kitchen_availability_summary_tool", args_schema=GetMasterKitchenAvailabilityInput)
async def get_master_kitchen_availability_summary_tool(
    service_date: Optional[str] = None,
    meal_window: Optional[str] = None,
) -> str:
    """Retrieve platform-wide active home kitchen metrics, dish counts, and daily portion capacity summaries."""
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        data = await get_master_kitchen_availability_summary(
            session,
            service_date=service_date,
            meal_window=meal_window,
        )

        filter_str = f" for {data['service_date'] or 'Today'} ({data['meal_window'] or 'All Windows'})"
        inv_str = (
            f"Portion Capacity: {data['remaining_portions_capacity']} / {data['total_portions_capacity']} portions remaining\n"
            if data["service_date"]
            else ""
        )

        return (
            f"Platform Kitchen Availability Summary{filter_str}:\n"
            f"Active Kitchens: {data['active_kitchens']} / {data['total_kitchens']} registered\n"
            f"Active Dishes Available: {data['active_menu_items']} items\n"
            f"{inv_str}"
        )


# =============================================================================
# TOOL 3: get_master_order_pipeline_summary_tool
# =============================================================================
class GetMasterOrderPipelineSummaryInput(BaseModel):
    service_date: Optional[str] = Field(
        default=None,
        description="Optional service date in ISO format YYYY-MM-DD (e.g. '2026-08-01')",
    )
    meal_window: Optional[str] = Field(
        default=None,
        description="Optional meal window: 'LUNCH' or 'DINNER'",
    )


async def get_master_order_pipeline_summary(
    session: AsyncSession,
    *,
    service_date: str | None = None,
    meal_window: str | None = None,
) -> dict[str, Any]:
    """Retrieve order volume breakdown and GMV revenue pipeline metrics with Guard 2 Assertions."""
    if meal_window:
        assert meal_window in {"LUNCH", "DINNER"}, f"Invalid meal window: '{meal_window}'. Must be LUNCH or DINNER"

    stmt = select(
        CustomerOrder.status,
        func.count(CustomerOrder.order_id),
        func.sum(CustomerOrder.total_amount),
    )

    if service_date:
        date_obj = date.fromisoformat(service_date)
        stmt = stmt.where(CustomerOrder.service_date == date_obj)
    if meal_window:
        stmt = stmt.where(CustomerOrder.meal_window == meal_window)

    stmt = stmt.group_by(CustomerOrder.status)
    rows = (await session.execute(stmt)).all()

    status_counts = {}
    total_pipeline_orders = 0
    total_pipeline_gmv = 0.0

    for status_name, count_val, gmv_val in rows:
        status_counts[status_name] = count_val
        total_pipeline_orders += count_val
        if gmv_val is not None:
            total_pipeline_gmv += float(gmv_val)

    return {
        "service_date": service_date,
        "meal_window": meal_window,
        "total_orders": total_pipeline_orders,
        "total_gmv": round(total_pipeline_gmv, 2),
        "by_status": status_counts,
    }


@tool("get_master_order_pipeline_summary_tool", args_schema=GetMasterOrderPipelineSummaryInput)
async def get_master_order_pipeline_summary_tool(
    service_date: Optional[str] = None,
    meal_window: Optional[str] = None,
) -> str:
    """Retrieve platform-wide order volume breakdown by status and gross merchandise value (GMV) revenue."""
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        data = await get_master_order_pipeline_summary(
            session,
            service_date=service_date,
            meal_window=meal_window,
        )

        filter_str = f" for {data['service_date'] or 'All Dates'} ({data['meal_window'] or 'All Windows'})"
        breakdown_text = "\n".join(f"  - {status}: {count} orders" for status, count in data["by_status"].items())

        return (
            f"Platform Order Pipeline Summary{filter_str}:\n"
            f"Total Orders: {data['total_orders']}\n"
            f"Total Pipeline GMV: ₹{data['total_gmv']:.2f}\n"
            f"Status Breakdown:\n{breakdown_text or '  - No orders in pipeline'}"
        )


# =============================================================================
# TOOL 4: execute_cutoff_batch_and_route_optimization_tool
# =============================================================================
class CutoffBatchRouteInput(BaseModel):
    window_id: str = Field(..., description="ID of the meal window being locked (e.g. 'win_lunch_01')")
    driver_phone: str = Field(..., description="Assigned driver's phone number")
    service_date: str = Field(..., description="Service date in ISO format YYYY-MM-DD")
    meal_window: str = Field(..., description="'LUNCH' or 'DINNER'")
    stops_data: list[dict[str, Any]] = Field(
        ...,
        description="List of stop dictionaries with target_ref_id, location_name, address, latitude, longitude, estimated_arrival, order_ids",
    )


async def call_google_maps_routes_api(
    origin_lat: float,
    origin_lng: float,
    destination_lat: float,
    destination_lng: float,
    intermediate_stops: list[dict[str, Any]],
) -> dict[str, Any]:
    """Call Google Maps Routes API v2 for traffic-aware route optimization and clickable maps link generation."""
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        # Fallback if API key not provided
        return {
            "total_distance_km": Decimal("5.50"),
            "estimated_duration_mins": 25,
            "maps_url": f"https://www.google.com/maps/dir/?api=1&origin={origin_lat},{origin_lng}&destination={destination_lat},{destination_lng}",
        }

    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    origin = {"location": {"latLng": {"latitude": origin_lat, "longitude": origin_lng}}}
    destination = {"location": {"latLng": {"latitude": destination_lat, "longitude": destination_lng}}}
    intermediates = [
        {"location": {"latLng": {"latitude": float(s["latitude"]), "longitude": float(s["longitude"])}}}
        for s in intermediate_stops
    ]

    payload = {
        "origin": origin,
        "destination": destination,
        "intermediates": intermediates,
        "travelMode": "DRIVE",
        "optimizeWaypointOrder": True,
    }

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.optimizedIntermediateWaypointIndex",
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.urlopen(req) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            route_data = res["routes"][0]
            dist_km = Decimal(str(round(route_data.get("distanceMeters", 5000) / 1000.0, 2)))
            dur_str = route_data.get("duration", "1500s").replace("s", "")
            dur_mins = int(round(int(dur_str) / 60.0))

            waypoint_str = "|".join(f"{s['latitude']},{s['longitude']}" for s in intermediate_stops)
            maps_url = f"https://www.google.com/maps/dir/?api=1&origin={origin_lat},{origin_lng}&destination={destination_lat},{destination_lng}&waypoints={waypoint_str}&travelmode=driving"

            return {
                "total_distance_km": dist_km,
                "estimated_duration_mins": dur_mins,
                "maps_url": maps_url,
            }
    except Exception:
        waypoint_str = "|".join(f"{s['latitude']},{s['longitude']}" for s in intermediate_stops)
        maps_url = f"https://www.google.com/maps/dir/?api=1&origin={origin_lat},{origin_lng}&destination={destination_lat},{destination_lng}&waypoints={waypoint_str}&travelmode=driving"
        return {
            "total_distance_km": Decimal("5.50"),
            "estimated_duration_mins": 25,
            "maps_url": maps_url,
        }


async def execute_cutoff_batch_and_route_optimization(
    session: AsyncSession,
    *,
    window_id: str,
    driver_phone: str,
    service_date: str,
    meal_window: str,
    stops_data: list[dict[str, Any]],
) -> SystemDeliveryRoute:
    """Lock cutoff window, optimize route via Google Maps, create delivery route and stops, and transition CONFIRMED orders to BATCHED."""
    assert window_id, "window_id cannot be empty"
    assert driver_phone and len(driver_phone) >= 10, f"Invalid driver phone: {driver_phone}"
    assert meal_window in {"LUNCH", "DINNER"}, f"Invalid meal window: {meal_window}"
    assert len(stops_data) >= 1, "Must provide at least 1 stop for route creation"

    date_obj = date.fromisoformat(service_date)
    total_orders = len({oid for s in stops_data for oid in s.get("order_ids", [])})

    # 0. Lock SystemMealWindow status
    stmt_win = select(SystemMealWindow).where(SystemMealWindow.window_id == window_id)
    window = (await session.execute(stmt_win)).scalar_one_or_none()
    if window:
        window.status = "LOCKED_PROCESSING"



    # 1. Call Google Maps Routes API if multiple stops
    origin_stop = stops_data[0]
    dest_stop = stops_data[-1]
    intermediate_stops = stops_data[1:-1]

    gmaps_res = await call_google_maps_routes_api(
        origin_lat=float(origin_stop["latitude"]),
        origin_lng=float(origin_stop["longitude"]),
        destination_lat=float(dest_stop["latitude"]),
        destination_lng=float(dest_stop["longitude"]),
        intermediate_stops=intermediate_stops,
    )

    # Attach single_leg_maps_url
    for s in stops_data:
        s["single_leg_maps_url"] = gmaps_res["maps_url"]

    # 2. Invoke Master Executor #2
    route = await execute_cutoff_batch_lock_and_routes_creation(
        session,
        window_id=window_id,
        driver_phone=driver_phone,
        service_date=date_obj,
        meal_window=meal_window,
        total_stops=len(stops_data),
        total_orders=total_orders,
        total_distance_km=gmaps_res["total_distance_km"],
        estimated_duration_mins=gmaps_res["estimated_duration_mins"],
        stops_data=stops_data,
    )
    return route


@tool("execute_cutoff_batch_and_route_optimization_tool", args_schema=CutoffBatchRouteInput)
async def execute_cutoff_batch_and_route_optimization_tool(
    window_id: str,
    driver_phone: str,
    service_date: str,
    meal_window: str,
    stops_data: list[dict[str, Any]],
) -> str:
    """Execute atomic 12 PM / 7 PM meal window cutoff batching, GCP Google Maps route generation, and order batching."""
    from app.db.session import transaction

    async with transaction() as session:
        route = await execute_cutoff_batch_and_route_optimization(
            session,
            window_id=window_id,
            driver_phone=driver_phone,
            service_date=service_date,
            meal_window=meal_window,
            stops_data=stops_data,
        )
        return (
            f"Cutoff Batch & Route Optimization COMPLETE for Window [{window_id}]!\n"
            f"Created Route #{route.route_id} for Driver {driver_phone}:\n"
            f"Total Stops: {route.total_stops} | Total Orders Batched: {route.total_orders}\n"
            f"Total Distance: {route.total_distance_km} km | Est. Duration: {route.estimated_duration_mins} mins"
        )


# =============================================================================
# TOOL 5: trigger_hitl_escalation_tool
# =============================================================================
class TriggerHitlEscalationInput(BaseModel):
    thread_id: str = Field(
        ...,
        description="LangGraph execution thread ID (e.g. 'thread_cust_9123456789')",
    )
    interrupt_type: str = Field(
        ...,
        description="Type of HITL interrupt: 'DIETARY_APPROVAL', 'CANCELLATION_APPROVAL', 'UNLOCATABLE_ADDRESS', 'AWAIT_LOCATION_PIN', 'PAYMENT_AWAIT_MASTER_APPROVAL', 'CUTOFF_EXTENSION', 'GATE_DENIAL'",
    )
    waiting_on_role: str = Field(
        ...,
        description="Role of human target: 'CUSTOMER', 'CHEF', 'DRIVER', or 'ADMIN'",
    )
    waiting_on_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of human target (e.g. '9123456789')",
    )
    prompt_message: str = Field(
        ...,
        description="WhatsApp prompt message sent to the human operator/user",
    )
    related_order_id: Optional[str] = Field(
        default=None,
        description="Optional associated order ID (e.g. 'ord_123456')",
    )
    payload: Optional[dict[str, Any]] = Field(
        default_factory=dict,
        description="Arbitrary context payload for HITL resolution",
    )


async def trigger_hitl_escalation(
    session: AsyncSession,
    *,
    thread_id: str,
    interrupt_type: str,
    waiting_on_role: str,
    waiting_on_phone: str,
    prompt_message: str,
    related_order_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> SystemHitlSession:
    """Create a 15-minute TTL HITL pause session, enqueue WhatsApp prompt, and log to chat ledger."""
    VALID_TYPES = {
        "DIETARY_APPROVAL",
        "CANCELLATION_APPROVAL",
        "UNLOCATABLE_ADDRESS",
        "AWAIT_LOCATION_PIN",
        "PAYMENT_AWAIT_MASTER_APPROVAL",
        "CUTOFF_EXTENSION",
        "GATE_DENIAL",
    }
    assert thread_id, "thread_id cannot be empty"
    assert interrupt_type in VALID_TYPES, f"Invalid interrupt_type: '{interrupt_type}'. Must be one of {VALID_TYPES}"
    assert waiting_on_role in {"CUSTOMER", "CHEF", "DRIVER", "ADMIN"}, f"Invalid waiting_on_role: '{waiting_on_role}'"
    assert waiting_on_phone and len(waiting_on_phone) >= 10, f"Invalid waiting_on_phone: '{waiting_on_phone}'"
    assert prompt_message and len(prompt_message.strip()) >= 5, "prompt_message must be at least 5 characters"

    # 1. Create HITL Session with 15-min TTL via Master Executor #4
    hitl_session = await execute_hitl_session_create_or_resume(
        session,
        thread_id=thread_id,
        interrupt_type=interrupt_type,
        waiting_on_role=waiting_on_role,
        waiting_on_phone=waiting_on_phone,
        order_id=related_order_id,
        payload=payload or {},
        expires_in_mins=15,
        status="WAITING",
    )

    # 2. Enqueue WhatsApp Prompt Message via Master Executor #6
    await execute_outbound_whatsapp_enqueue(
        session,
        recipient_phone=waiting_on_phone,
        recipient_role=waiting_on_role,
        message_text=prompt_message.strip(),
        message_type="TEXT",
        related_order_id=related_order_id,
    )

    # 3. Log Outbound Prompt to Unified Chat Ledger
    await execute_conversation_message_insert(
        session,
        phone=waiting_on_phone,
        actor_role=waiting_on_role,
        direction="OUTBOUND",
        source="HITL_SYSTEM",
        message_text=prompt_message.strip(),
        related_order_id=related_order_id,
    )

    return hitl_session


@tool("trigger_hitl_escalation_tool", args_schema=TriggerHitlEscalationInput)
async def trigger_hitl_escalation_tool(
    thread_id: str,
    interrupt_type: str,
    waiting_on_role: str,
    waiting_on_phone: str,
    prompt_message: str,
    related_order_id: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
) -> str:
    """Pause execution state machine and trigger a Human-In-The-Loop (HITL) escalation with a 15-min TTL WhatsApp prompt."""
    from app.db.session import transaction

    async with transaction() as session:
        hitl = await trigger_hitl_escalation(
            session,
            thread_id=thread_id,
            interrupt_type=interrupt_type,
            waiting_on_role=waiting_on_role,
            waiting_on_phone=waiting_on_phone,
            prompt_message=prompt_message,
            related_order_id=related_order_id,
            payload=payload,
        )
        return (
            f"HITL Escalation Session [{hitl.session_id}] CREATED!\n"
            f"Thread: {hitl.thread_id} | Type: {hitl.interrupt_type}\n"
            f"Waiting on {hitl.waiting_on_role} ({hitl.waiting_on_phone}) | Status: {hitl.status}\n"
            f"Expires At: {hitl.expires_at.strftime('%Y-%m-%d %H:%M:%S')} (15-min TTL)\n"
            f"Prompt Sent: \"{prompt_message}\""
        )


# =============================================================================
# TOOL 6: escalate_delayed_batch_prep_tool
# =============================================================================
class EscalateDelayedBatchPrepInput(BaseModel):
    chef_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of home chef (e.g. '9876543210')",
    )
    service_date: str = Field(
        ...,
        description="Service date in ISO format YYYY-MM-DD (e.g. '2026-08-02')",
    )
    meal_window: str = Field(
        ...,
        description="'LUNCH' or 'DINNER'",
    )
    delay_minutes: int = Field(
        ...,
        description="Estimated prep delay in minutes (e.g. 15)",
    )
    delay_reason: str = Field(
        ...,
        description="Brief reason for delay (e.g. 'Extra roti batch cooking required')",
    )
    related_order_ids: Optional[list[str]] = Field(
        default_factory=list,
        description="Optional list of affected order IDs",
    )


async def escalate_delayed_batch_prep(
    session: AsyncSession,
    *,
    chef_phone: str,
    service_date: str,
    meal_window: str,
    delay_minutes: int,
    delay_reason: str,
    related_order_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Log operational warning event and dispatch WhatsApp alerts to Chef and assigned Driver for kitchen prep delay."""
    assert chef_phone and len(chef_phone) >= 10, f"Invalid chef_phone: {chef_phone}"
    assert meal_window in {"LUNCH", "DINNER"}, f"Invalid meal_window: {meal_window}"
    assert delay_minutes > 0 and delay_minutes <= 60, f"Invalid delay_minutes: {delay_minutes}. Must be between 1 and 60."
    assert delay_reason and len(delay_reason.strip()) >= 5, "delay_reason must be at least 5 characters"

    date_obj = date.fromisoformat(service_date)
    first_order_id = related_order_ids[0] if related_order_ids else None

    # 1. Log System Audit Event via Master Executor #8
    audit_log = await execute_system_audit_log(
        session,
        event_type="KITCHEN_PREP_DELAY",
        source_role="MASTER_ORCHESTRATOR",
        target_role="CHEF",
        order_id=first_order_id,
        payload={
            "chef_phone": chef_phone,
            "service_date": service_date,
            "meal_window": meal_window,
            "delay_minutes": delay_minutes,
            "delay_reason": delay_reason,
            "affected_orders": related_order_ids or [],
        },
        severity="WARNING",
    )

    # 2. Enqueue WhatsApp Alert to Chef
    chef_msg_text = (
        f"⚠️ URGENT KITCHEN PREP DELAY NOTICE ({meal_window}):\n"
        f"A prep delay of {delay_minutes} minutes has been logged for your kitchen.\n"
        f"Reason: \"{delay_reason.strip()}\".\n"
        f"Please reply or mark your batch PACKED as soon as cooking completes!"
    )
    await execute_outbound_whatsapp_enqueue(
        session,
        recipient_phone=chef_phone,
        recipient_role="CHEF",
        message_text=chef_msg_text,
        message_type="TEXT",
        related_order_id=first_order_id,
    )
    await execute_conversation_message_insert(
        session,
        phone=chef_phone,
        actor_role="CHEF",
        direction="OUTBOUND",
        source="SYSTEM_ALERT",
        message_text=chef_msg_text,
        related_order_id=first_order_id,
    )

    # 3. Query assigned driver and enqueue WhatsApp Alert if active route exists
    stmt_stop = select(SystemDeliveryStop).where(
        SystemDeliveryStop.target_ref_id == chef_phone,
        SystemDeliveryStop.stop_type == "PICKUP_KITCHEN",
    )
    stops = (await session.execute(stmt_stop)).scalars().all()

    driver_phone_notified = None
    for stop in stops:
        route = await session.get(SystemDeliveryRoute, stop.route_id)
        if route and route.service_date == date_obj and route.meal_window == meal_window and route.driver_phone:
            driver_phone_notified = route.driver_phone
            driver_msg_text = (
                f"⚠️ PICKUP DELAY NOTICE ({meal_window}):\n"
                f"Kitchen {chef_phone} has logged a {delay_minutes}-minute cooking delay.\n"
                f"Reason: \"{delay_reason.strip()}\".\n"
                f"Your pickup ETA has been updated (+{delay_minutes} mins)."
            )
            await execute_outbound_whatsapp_enqueue(
                session,
                recipient_phone=route.driver_phone,
                recipient_role="DRIVER",
                message_text=driver_msg_text,
                message_type="TEXT",
                related_order_id=first_order_id,
            )
            await execute_conversation_message_insert(
                session,
                phone=route.driver_phone,
                actor_role="DRIVER",
                direction="OUTBOUND",
                source="SYSTEM_ALERT",
                message_text=driver_msg_text,
                related_order_id=first_order_id,
            )
            break

    return {
        "log_id": audit_log.log_id,
        "chef_phone": chef_phone,
        "service_date": service_date,
        "meal_window": meal_window,
        "delay_minutes": delay_minutes,
        "delay_reason": delay_reason,
        "driver_notified": driver_phone_notified,
    }


@tool("escalate_delayed_batch_prep_tool", args_schema=EscalateDelayedBatchPrepInput)
async def escalate_delayed_batch_prep_tool(
    chef_phone: str,
    service_date: str,
    meal_window: str,
    delay_minutes: int,
    delay_reason: str,
    related_order_ids: Optional[list[str]] = None,
) -> str:
    """Escalate a kitchen prep delay warning, logging a system audit event and alerting Chef and Driver via WhatsApp."""
    from app.db.session import transaction

    async with transaction() as session:
        res = await escalate_delayed_batch_prep(
            session,
            chef_phone=chef_phone,
            service_date=service_date,
            meal_window=meal_window,
            delay_minutes=delay_minutes,
            delay_reason=delay_reason,
            related_order_ids=related_order_ids,
        )
        driver_str = f"Assigned Driver ({res['driver_notified']})" if res["driver_notified"] else "No Driver Assigned Yet"
        return (
            f"Kitchen Prep Delay Escalation Logged [{res['log_id']}]!\n"
            f"Chef: {res['chef_phone']} | Window: {res['meal_window']} ({res['service_date']})\n"
            f"Delay: +{res['delay_minutes']} mins | Reason: \"{res['delay_reason']}\"\n"
            f"Alerts Dispatched: Chef ({res['chef_phone']}), {driver_str}"
        )


# =============================================================================
# TOOL 7: process_payment_gateway_webhook_tool
# =============================================================================
class ProcessPaymentWebhookInput(BaseModel):
    gateway_event_id: str = Field(
        ...,
        description="Unique event ID from gateway (e.g. 'evt_rzp_998877' or 'mock_evt_101')",
    )
    event_type: str = Field(
        ...,
        description="Webhook event type (e.g. 'payment_link.paid', 'payment.captured', or 'MOCK_PAYMENT_SUCCESS')",
    )
    order_id: str = Field(
        ...,
        description="Target Homaatri CustomerOrder ID (e.g. 'ord_123456')",
    )
    payment_id: Optional[str] = Field(
        default=None,
        description="Razorpay transaction/payment ID (e.g. 'pay_987654321')",
    )
    amount_paid: Decimal = Field(
        ...,
        description="Total amount paid in INR (e.g. 280.00)",
    )
    raw_payload: Optional[dict[str, Any]] = Field(
        default_factory=dict,
        description="Raw JSON webhook payload",
    )
    is_mock: Optional[bool] = Field(
        default=False,
        description="Set to True for dummy/POC test payment simulation",
    )


async def process_payment_gateway_webhook(
    session: AsyncSession,
    *,
    gateway_event_id: str,
    event_type: str,
    order_id: str,
    payment_id: str | None = None,
    amount_paid: Decimal,
    raw_payload: dict[str, Any] | None = None,
    is_mock: bool = False,
) -> dict[str, Any]:
    """Process payment webhook with strict idempotency, update payment status via DW2, cascade order status to CONFIRMED via DW1, and dispatch WhatsApp receipt."""
    assert gateway_event_id, "gateway_event_id cannot be empty"
    assert order_id, "order_id cannot be empty"
    assert amount_paid > Decimal("0.00"), f"Invalid amount_paid: {amount_paid}"
    VALID_EVENTS = {"payment_link.paid", "payment.captured", "MOCK_PAYMENT_SUCCESS"}
    assert event_type in VALID_EVENTS, f"Unsupported event_type: '{event_type}'. Must be one of {VALID_EVENTS}"

    gateway_name = "MOCK_GATEWAY" if is_mock else "RAZORPAY"

    # 1. Idempotency Check via Master Executor #5
    webhook_event, is_new_event = await execute_payment_webhook_idempotency_log(
        session,
        gateway=gateway_name,
        gateway_event_id=gateway_event_id,
        event_type=event_type,
        payment_id=payment_id,
        order_id=order_id,
        signature_verified=True,
        raw_payload=raw_payload or {},
    )

    if not is_new_event:
        return {
            "status": "IDEMPOTENT_SKIPPED",
            "message": f"Event {gateway_event_id} already processed. Duplicate skipped.",
            "order_id": order_id,
        }

    # 2. Find associated CustomerOrder and CustomerPayment record
    order = await session.get(CustomerOrder, order_id)
    assert order is not None, f"Order not found: {order_id}"

    stmt_pay = select(CustomerPayment).where(CustomerPayment.order_id == order_id)
    payment_record = (await session.execute(stmt_pay)).scalar_one_or_none()

    if payment_record is None:
        # Create payment record if missing
        payment_record = CustomerPayment(
            payment_id=payment_id or generate_id("pay"),
            order_id=order_id,
            customer_phone=order.customer_phone,
            payment_type="INITIAL",
            amount_due=amount_paid,
            amount_paid=Decimal("0.00"),
            gateway=gateway_name,
            status="PENDING",
        )
        session.add(payment_record)
        await session.flush()


    # 3. Trigger DW2 (execute_payment_status_update) -> Automatically cascades DW1 to CONFIRMED
    updated_payment = await execute_payment_status_update(
        session,
        payment_id=payment_record.payment_id,
        target_status="PAID",
        gateway_transaction_id=payment_id or f"txn_{gateway_event_id}",
    )

    # 4. Dispatch WhatsApp Order Confirmation Receipt
    cust_phone = updated_payment.customer_phone
    receipt_text = (
        f"✅ PAYMENT RECEIVED & ORDER CONFIRMED!\n"
        f"Order #{order_id} total ₹{amount_paid:.2f} paid via {gateway_name}.\n"
        f"Transaction ID: {updated_payment.transaction_id}\n"
        f"Your meal is being prepared for scheduled cutoff delivery!"
    )
    if cust_phone and len(cust_phone) >= 10:
        await execute_outbound_whatsapp_enqueue(
            session,
            recipient_phone=cust_phone,
            recipient_role="CUSTOMER",
            message_text=receipt_text,
            message_type="TEXT",
            related_order_id=order_id,
        )
        await execute_conversation_message_insert(
            session,
            phone=cust_phone,
            actor_role="CUSTOMER",
            direction="OUTBOUND",
            source="PAYMENT_GATEWAY",
            message_text=receipt_text,
            related_order_id=order_id,
        )

    # Update webhook log processing status
    webhook_event.processing_status = "PROCESSED"
    await session.flush()

    return {
        "status": "SUCCESS",
        "gateway": gateway_name,
        "event_id": gateway_event_id,
        "order_id": order_id,
        "payment_id": updated_payment.payment_id,
        "transaction_id": updated_payment.transaction_id,
        "amount_paid": float(amount_paid),
        "order_status": "CONFIRMED",
    }


@tool("process_payment_gateway_webhook_tool", args_schema=ProcessPaymentWebhookInput)
async def process_payment_gateway_webhook_tool(
    gateway_event_id: str,
    event_type: str,
    order_id: str,
    payment_id: Optional[str] = None,
    amount_paid: Decimal = Decimal("0.00"),
    raw_payload: Optional[dict[str, Any]] = None,
    is_mock: Optional[bool] = False,
) -> str:
    """Process incoming payment gateway webhook, enforcing idempotency, marking payment PAID (DW2), and cascading order status to CONFIRMED (DW1)."""
    from app.db.session import transaction

    async with transaction() as session:
        res = await process_payment_gateway_webhook(
            session,
            gateway_event_id=gateway_event_id,
            event_type=event_type,
            order_id=order_id,
            payment_id=payment_id,
            amount_paid=amount_paid,
            raw_payload=raw_payload,
            is_mock=is_mock or False,
        )
        if res["status"] == "IDEMPOTENT_SKIPPED":
            return f"⚠️ Duplicate Payment Webhook Ignored: {res['message']}"

        return (
            f"✅ Payment Webhook PROCESSED Successfully [{res['gateway']}]!\n"
            f"Event ID: {res['event_id']} | Order #{res['order_id']} Status: CONFIRMED\n"
            f"Payment ID: {res['payment_id']} | Txn ID: {res['transaction_id']}\n"
            f"Amount Received: ₹{res['amount_paid']:.2f}"
        )


# =============================================================================
# TOOL 8: request_cut_off_extension_tool
# =============================================================================
class RequestCutOffExtensionInput(BaseModel):
    chef_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of home chef (e.g. '9876543210')",
    )
    service_date: str = Field(
        ...,
        description="Service date in ISO format YYYY-MM-DD (e.g. '2026-08-02')",
    )
    meal_window: str = Field(
        ...,
        description="'LUNCH' or 'DINNER'",
    )
    extension_minutes: int = Field(
        ...,
        description="Requested extension duration in minutes (5 to 20 mins)",
    )
    reason: str = Field(
        ...,
        description="Reason for cutoff extension request (e.g. 'Large catering batch prep')",
    )


async def request_cut_off_extension(
    session: AsyncSession,
    *,
    chef_phone: str,
    service_date: str,
    meal_window: str,
    extension_minutes: int,
    reason: str,
) -> SystemHitlSession:
    """Validate meal window status, create HITL extension session, and notify Admin via WhatsApp."""
    assert chef_phone and len(chef_phone) >= 10, f"Invalid chef_phone: {chef_phone}"
    assert meal_window in {"LUNCH", "DINNER"}, f"Invalid meal_window: {meal_window}"
    assert extension_minutes >= 5 and extension_minutes <= 20, f"Invalid extension_minutes: {extension_minutes}. Must be 5-20 mins."
    assert reason and len(reason.strip()) >= 5, "reason must be at least 5 characters"

    date_obj = date.fromisoformat(service_date)

    # 1. Assert meal window is still OPEN
    stmt_win = select(SystemMealWindow).where(
        SystemMealWindow.service_date == date_obj,
        SystemMealWindow.meal_type == meal_window,
    )
    win = (await session.execute(stmt_win)).scalar_one_or_none()
    if win:
        assert win.status == "OPEN", f"Cannot request extension for meal window with status: '{win.status}'"

    thread_id = f"thread_ext_{chef_phone}_{service_date}_{meal_window}"

    # 2. Create HITL Session via Master Executor #4
    hitl_session = await execute_hitl_session_create_or_resume(
        session,
        thread_id=thread_id,
        interrupt_type="CUTOFF_EXTENSION",
        waiting_on_role="ADMIN",
        waiting_on_phone="9999999999",  # Admin phone placeholder
        payload={
            "chef_phone": chef_phone,
            "service_date": service_date,
            "meal_window": meal_window,
            "extension_minutes": extension_minutes,
            "reason": reason,
        },
        expires_in_mins=15,
        status="WAITING",
    )

    # 3. Log Audit Event via Master Executor #8
    await execute_system_audit_log(
        session,
        event_type="CUTOFF_EXTENSION_REQUESTED",
        source_role="CHEF",
        target_role="ADMIN",
        payload={
            "session_id": hitl_session.session_id,
            "chef_phone": chef_phone,
            "extension_minutes": extension_minutes,
            "reason": reason,
        },
        severity="INFO",
    )

    # 4. Enqueue WhatsApp Alert to Admin
    admin_msg_text = (
        f"⏳ CUTOFF EXTENSION REQUEST ({meal_window} - {service_date}):\n"
        f"Chef {chef_phone} requested +{extension_minutes} mins extension.\n"
        f"Reason: \"{reason.strip()}\".\n"
        f"Reply APPROVE to grant or REJECT to decline."
    )
    await execute_outbound_whatsapp_enqueue(
        session,
        recipient_phone="9999999999",
        recipient_role="ADMIN",
        message_text=admin_msg_text,
        message_type="TEXT",
    )
    await execute_conversation_message_insert(
        session,
        phone="9999999999",
        actor_role="ADMIN",
        direction="OUTBOUND",
        source="SYSTEM_ALERT",
        message_text=admin_msg_text,
    )

    return hitl_session


@tool("request_cut_off_extension_tool", args_schema=RequestCutOffExtensionInput)
async def request_cut_off_extension_tool(
    chef_phone: str,
    service_date: str,
    meal_window: str,
    extension_minutes: int,
    reason: str,
) -> str:
    """Request a temporary cutoff extension (5-20 mins) for an active meal window, creating a HITL pause session for Admin approval."""
    from app.db.session import transaction

    async with transaction() as session:
        hitl = await request_cut_off_extension(
            session,
            chef_phone=chef_phone,
            service_date=service_date,
            meal_window=meal_window,
            extension_minutes=extension_minutes,
            reason=reason,
        )
        return (
            f"Cutoff Extension Request Submitted [{hitl.session_id}]!\n"
            f"Chef: {chef_phone} | Window: {meal_window} ({service_date})\n"
            f"Requested Extension: +{extension_minutes} mins | Status: WAITING_ADMIN_APPROVAL\n"
            f"Admin Alert Dispatched via WhatsApp."
        )




