"""Driver Domain LLM Tools (Category 2).

Encapsulates Driver Concierge Agent tools with Guard 2 Pre-Condition Assertions.
Tool 1: get_driver_profile_tool (Read-only, Same Domain).
Tool 2: register_driver_profile_tool (Write Executor #16, Same Domain).
Tool 3: get_driver_active_delivery_route_tool (Read-only, Same Domain).
Tool 4: update_driver_duty_status_tool (Write Executor #17, Same Domain).
"""

from datetime import date, datetime
from typing import Any, Optional


from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.executors.driver import execute_driver_profile_upsert
from app.models.driver import DriverProfile, DriverTripStatus
from app.models.system import SystemDeliveryRoute, SystemDeliveryStop


# =============================================================================
# TOOL 1: get_driver_profile_tool
# =============================================================================
class GetDriverProfileInput(BaseModel):
    driver_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of the driver (e.g. '9999988888')",
    )


async def get_driver_profile(
    session: AsyncSession,
    driver_phone: str,
) -> dict[str, Any] | None:
    """Query database for driver profile details with Guard 2 Pre-Condition Assertions."""
    assert driver_phone and len(driver_phone) >= 10, f"Invalid driver phone number: {driver_phone}"

    driver = await session.get(DriverProfile, driver_phone)
    if driver is None:
        return None

    return {
        "driver_phone": driver.driver_phone,
        "driver_name": driver.driver_name,
        "vehicle_type": driver.vehicle_type,
        "vehicle_number": driver.vehicle_number,
        "vehicle_model": driver.vehicle_model,
        "driver_license_number": driver.driver_license_number,
        "alternate_phone": driver.alternate_phone,
        "current_assigned_route_id": driver.current_assigned_route_id,
        "is_on_shift": driver.is_on_shift,
        "active_status": driver.active_status,
    }


@tool("get_driver_profile_tool", args_schema=GetDriverProfileInput)
async def get_driver_profile_tool(driver_phone: str) -> str:
    """Retrieve identity, vehicle details, shift availability, and active route assignments for a driver."""
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        data = await get_driver_profile(session, driver_phone=driver_phone)
        if data is None:
            return (
                f"Driver profile for phone {driver_phone} is NOT registered yet (UNREGISTERED).\n"
                f"Please prompt driver for their full name, vehicle type, and vehicle number to complete onboarding."
            )

        shift_str = "ON SHIFT (AVAILABLE)" if data["is_on_shift"] else "OFF SHIFT (UNAVAILABLE)"
        status_str = "ACTIVE" if data["active_status"] else "SUSPENDED"
        route_str = data["current_assigned_route_id"] or "No active route assigned"

        return (
            f"Driver Profile for {data['driver_name']} ({data['driver_phone']}):\n"
            f"Vehicle: {data['vehicle_type']} ({data['vehicle_number']})\n"
            f"License: {data['driver_license_number'] or 'N/A'}\n"
            f"Duty Status: {shift_str}\n"
            f"Account Status: {status_str}\n"
            f"Current Assigned Route: {route_str}"
        )


# =============================================================================
# TOOL 2: register_driver_profile_tool
# =============================================================================
class RegisterDriverProfileInput(BaseModel):
    driver_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of the driver (e.g. '9999988888')",
    )
    driver_name: str = Field(
        ...,
        description="Driver's full name (e.g. 'Raju Delivery Partner')",
    )
    vehicle_type: Optional[str] = Field(
        default="BIKE",
        description="Vehicle classification: 'BIKE', 'SCOOTER', 'EV_BIKE', or 'CAR'",
    )
    vehicle_number: str = Field(
        ...,
        description="Vehicle registration plate number (e.g. 'TS 09 EA 1234')",
    )
    vehicle_model: Optional[str] = Field(
        default=None,
        description="Vehicle make and model (e.g. 'Hero Splendor')",
    )
    driver_license_number: Optional[str] = Field(
        default=None,
        description="Driving license number (e.g. 'DL-1420110012345')",
    )


async def register_driver_profile(
    session: AsyncSession,
    *,
    driver_phone: str,
    driver_name: str,
    vehicle_type: str = "BIKE",
    vehicle_number: str,
    vehicle_model: str | None = None,
    driver_license_number: str | None = None,
) -> DriverProfile:
    """Onboard a new driver or update vehicle details with Guard 2 Pre-Condition Assertions."""
    assert driver_name and len(driver_name.strip()) >= 2, f"Driver name must be >= 2 chars, got '{driver_name}'"
    assert vehicle_number and len(vehicle_number.strip()) >= 4, (
        f"Vehicle number must be >= 4 chars, got '{vehicle_number}'"
    )
    assert vehicle_type in {"BIKE", "SCOOTER", "EV_BIKE", "CAR"}, f"Invalid vehicle type: {vehicle_type}"

    driver = await execute_driver_profile_upsert(
        session,
        driver_phone=driver_phone,
        driver_name=driver_name.strip(),
        vehicle_type=vehicle_type,
        vehicle_number=vehicle_number.strip(),
        vehicle_model=vehicle_model.strip() if vehicle_model else None,
        driver_license_number=driver_license_number.strip() if driver_license_number else None,
        is_on_shift=True,
        active_status=True,
    )
    return driver


@tool("register_driver_profile_tool", args_schema=RegisterDriverProfileInput)
async def register_driver_profile_tool(
    driver_phone: str,
    driver_name: str,
    vehicle_number: str,
    vehicle_type: Optional[str] = "BIKE",
    vehicle_model: Optional[str] = None,
    driver_license_number: Optional[str] = None,
) -> str:
    """Register or update a delivery partner profile with vehicle and license credentials."""
    from app.db.session import transaction

    async with transaction() as session:
        driver = await register_driver_profile(
            session,
            driver_phone=driver_phone,
            driver_name=driver_name,
            vehicle_type=vehicle_type or "BIKE",
            vehicle_number=vehicle_number,
            vehicle_model=vehicle_model,
            driver_license_number=driver_license_number,
        )

        return (
            f"Registration COMPLETE for Driver {driver.driver_name} ({driver.driver_phone})!\n"
            f"Vehicle Assigned: {driver.vehicle_type} ({driver.vehicle_number})\n"
            f"Shift Status: ON SHIFT (Ready to accept batch route assignments)."
        )


# =============================================================================
# TOOL 3: get_driver_active_delivery_route_tool
# =============================================================================
class GetDriverActiveRouteInput(BaseModel):
    driver_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of the driver (e.g. '9999988888')",
    )


async def get_driver_active_delivery_route(
    session: AsyncSession,
    *,
    driver_phone: str,
) -> dict[str, Any] | None:
    """Retrieve active delivery route and stop details for a driver with Guard 2 Pre-Condition Assertions."""
    assert driver_phone and len(driver_phone) >= 10, f"Invalid driver phone number: {driver_phone}"

    driver = await session.get(DriverProfile, driver_phone)
    assert driver is not None, f"Driver profile not found for phone: {driver_phone}"
    assert driver.active_status is True, f"Driver account {driver_phone} is suspended / inactive."

    stmt_route = (
        select(SystemDeliveryRoute)
        .where(
            SystemDeliveryRoute.driver_phone == driver_phone,
            SystemDeliveryRoute.status.in_(["ASSIGNED", "EN_ROUTE_PICKUP", "AT_KITCHEN", "EN_ROUTE_DELIVERY", "AT_GATE"]),
        )
        .order_by(SystemDeliveryRoute.created_at.desc())
    )
    route = (await session.execute(stmt_route)).scalar_one_or_none()

    if route is None:
        return None

    stmt_stops = (
        select(SystemDeliveryStop)
        .where(SystemDeliveryStop.route_id == route.route_id)
        .order_by(SystemDeliveryStop.stop_index.asc())
    )
    stops = (await session.execute(stmt_stops)).scalars().all()

    return {
        "route_id": route.route_id,
        "service_date": route.service_date.isoformat(),
        "meal_window": route.meal_window,
        "status": route.status,
        "total_stops": route.total_stops,
        "total_orders": route.total_orders,
        "total_distance_km": float(route.total_distance_km) if route.total_distance_km else 0.0,
        "stops": [
            {
                "stop_id": stop.stop_id,
                "stop_index": stop.stop_index,
                "stop_type": stop.stop_type,
                "location_name": stop.location_name,
                "address": stop.address,
                "latitude": float(stop.latitude),
                "longitude": float(stop.longitude),
                "status": stop.status,
            }
            for stop in stops
        ],
    }


@tool("get_driver_active_delivery_route_tool", args_schema=GetDriverActiveRouteInput)
async def get_driver_active_delivery_route_tool(driver_phone: str) -> str:
    """Retrieve active delivery route assignment, kitchen pickups, and customer dropoff stop lists for a driver."""
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        data = await get_driver_active_delivery_route(session, driver_phone=driver_phone)
        if data is None:
            return f"No active delivery route currently assigned to driver {driver_phone}."

        stops_summary = "\n".join(
            f"{s['stop_index']}. [{s['stop_type']}] {s['location_name']} — {s['address']} [{s['status']}]"
            for s in data["stops"]
        )

        return (
            f"Active Delivery Route #{data['route_id']} ({data['service_date']} {data['meal_window']}) [{data['status']}]:\n"
            f"Total Stops: {data['total_stops']} | Total Orders: {data['total_orders']} | Est. Distance: {data['total_distance_km']} km\n"
            f"Stops List:\n{stops_summary}"
        )


# =============================================================================
# TOOL 4: update_driver_duty_status_tool
# =============================================================================
class UpdateDriverDutyStatusInput(BaseModel):
    driver_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of the driver (e.g. '9999988888')",
    )
    is_on_shift: bool = Field(
        ...,
        description="True to go ON SHIFT (available for orders), False to go OFF SHIFT (duty ended)",
    )


async def update_driver_duty_status(
    session: AsyncSession,
    *,
    driver_phone: str,
    is_on_shift: bool,
) -> DriverProfile:
    """Toggle driver's shift availability with Guard 2 Pre-Condition Assertions."""
    assert driver_phone and len(driver_phone) >= 10, f"Invalid driver phone number: {driver_phone}"

    driver = await session.get(DriverProfile, driver_phone)
    assert driver is not None, f"Driver profile not found for phone: {driver_phone}"

    driver.is_on_shift = is_on_shift
    await session.flush()
    return driver


@tool("update_driver_duty_status_tool", args_schema=UpdateDriverDutyStatusInput)
async def update_driver_duty_status_tool(
    driver_phone: str,
    is_on_shift: bool,
) -> str:
    """Toggle driver availability status (ON SHIFT / OFF SHIFT) for receiving batch route dispatches."""
    from app.db.session import transaction

    async with transaction() as session:
        driver = await update_driver_duty_status(
            session,
            driver_phone=driver_phone,
            is_on_shift=is_on_shift,
        )

        status_text = "ON SHIFT (Ready for batch routes)" if driver.is_on_shift else "OFF SHIFT (Duty ended)"
        return f"Driver {driver.driver_name} ({driver.driver_phone}) is now: {status_text}"


# =============================================================================
# TOOL 5: report_delivery_delay_or_gate_issue_tool
# =============================================================================
class ReportDeliveryDelayOrGateIssueInput(BaseModel):
    driver_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of driver (e.g. '9111222333')",
    )
    stop_id: str = Field(
        ...,
        description="Current delivery stop ID (e.g. 'stp_123456')",
    )
    issue_type: str = Field(
        ...,
        description="'GATE_SECURITY', 'CUSTOMER_UNREACHABLE', or 'TRAFFIC_DELAY'",
    )
    delay_minutes: Optional[int] = Field(
        default=10,
        description="Estimated delay in minutes (5 to 30 mins)",
    )
    issue_notes: str = Field(
        ...,
        description="Detailed description of issue (e.g. 'Security guard at Gate 2 refusing entry without flat owner passcode')",
    )


async def report_delivery_delay_or_gate_issue(
    session: AsyncSession,
    *,
    driver_phone: str,
    stop_id: str,
    issue_type: str,
    delay_minutes: int = 10,
    issue_notes: str,
) -> dict[str, Any]:
    """Report delivery delay or gate security issue, creating a WAITING HITL session and notifying customer on WhatsApp."""
    assert driver_phone and len(driver_phone) >= 10, f"Invalid driver_phone: {driver_phone}"
    assert stop_id, "stop_id cannot be empty"
    assert issue_type in {"GATE_SECURITY", "CUSTOMER_UNREACHABLE", "TRAFFIC_DELAY"}, f"Invalid issue_type: '{issue_type}'"
    assert issue_notes and len(issue_notes.strip()) >= 5, "issue_notes must be at least 5 characters"

    stop = await session.get(SystemDeliveryStop, stop_id)
    assert stop is not None, f"Delivery stop not found: {stop_id}"

    route = await session.get(SystemDeliveryRoute, stop.route_id)
    assert route is not None, f"Delivery route not found: {stop.route_id}"
    assert route.driver_phone == driver_phone, f"Route {route.route_id} belongs to driver {route.driver_phone}, not {driver_phone}"

    # Lookup target order & customer
    from app.models.customer import CustomerOrder
    from app.models.system import SystemDeliveryStopOrder

    stmt_so = select(SystemDeliveryStopOrder.order_id).where(SystemDeliveryStopOrder.stop_id == stop_id)
    order_id = (await session.execute(stmt_so)).scalar_one_or_none()

    customer_phone = None
    if order_id:
        order = await session.get(CustomerOrder, order_id)
        if order:
            customer_phone = order.customer_phone

    if customer_phone is None and stop.stop_type == "DROPOFF_GATE":
        customer_phone = stop.target_ref_id

    assert customer_phone, f"Could not determine target customer_phone for stop {stop_id}"

    driver = await session.get(DriverProfile, driver_phone)
    driver_name = driver.driver_name if driver else "Delivery Driver"

    thread_id = f"thread_gate_{stop_id}"

    # 1. Create HITL Pause Session via Master Executor #4
    from app.executors.master import (
        execute_conversation_message_insert,
        execute_hitl_session_create_or_resume,
        execute_outbound_whatsapp_enqueue,
        execute_system_audit_log,
    )

    hitl = await execute_hitl_session_create_or_resume(
        session,
        thread_id=thread_id,
        interrupt_type="GATE_SECURITY_OR_DELAY",
        waiting_on_role="CUSTOMER",
        waiting_on_phone=customer_phone,
        payload={
            "driver_phone": driver_phone,
            "stop_id": stop_id,
            "issue_type": issue_type,
            "delay_minutes": delay_minutes,
            "issue_notes": issue_notes.strip(),
        },
        expires_in_mins=15,
        status="WAITING",
    )

    # 2. Update Delivery Stop Status to DELAYED
    stop.status = "DELAYED"
    await session.flush()

    # 3. Enqueue Urgent WhatsApp Alert to Customer
    cust_msg = (
        f"🚨 DELIVERY ISSUE ALERT:\n"
        f"Driver ({driver_name}) is at your location ({stop.location_name}) but reported an issue:\n"
        f"Issue: {issue_type} (+{delay_minutes} mins delay)\n"
        f"Details: \"{issue_notes.strip()}\"\n"
        f"Please reply immediately or instruct gate security to allow entry!"
    )
    await execute_outbound_whatsapp_enqueue(
        session,
        recipient_phone=customer_phone,
        recipient_role="CUSTOMER",
        message_text=cust_msg,
    )
    await execute_conversation_message_insert(
        session,
        phone=customer_phone,
        actor_role="CUSTOMER",
        direction="OUTBOUND",
        source="SYSTEM_ALERT",
        message_text=cust_msg,
    )

    # 4. Record Audit Event via Master Executor #8
    await execute_system_audit_log(
        session,
        event_type="DELIVERY_GATE_ISSUE_REPORTED",
        source_role="DRIVER",
        target_role="CUSTOMER",
        payload={
            "session_id": hitl.session_id,
            "stop_id": stop_id,
            "driver_phone": driver_phone,
            "customer_phone": customer_phone,
            "issue_type": issue_type,
            "delay_minutes": delay_minutes,
        },
        severity="WARNING",
    )

    return {
        "hitl_session_id": hitl.session_id,
        "stop_id": stop_id,
        "driver_phone": driver_phone,
        "customer_phone": customer_phone,
        "issue_type": issue_type,
        "status": "WAITING_CUSTOMER_RESPONSE",
    }


@tool("report_delivery_delay_or_gate_issue_tool", args_schema=ReportDeliveryDelayOrGateIssueInput)
async def report_delivery_delay_or_gate_issue_tool(
    driver_phone: str,
    stop_id: str,
    issue_type: str,
    delay_minutes: Optional[int] = 10,
    issue_notes: str = "",
) -> str:
    """Report a gate security blockage or traffic delivery delay, opening a WAITING HITL pause session and alerting the customer via WhatsApp."""
    from app.db.session import transaction

    async with transaction() as session:
        res = await report_delivery_delay_or_gate_issue(
            session,
            driver_phone=driver_phone,
            stop_id=stop_id,
            issue_type=issue_type,
            delay_minutes=delay_minutes or 10,
            issue_notes=issue_notes,
        )
        return (
            f"🚨 Delivery Issue Reported [{res['hitl_session_id']}]!\n"
            f"Stop #{res['stop_id']} | Issue: {res['issue_type']}\n"
            f"Customer ({res['customer_phone']}) urgently notified via WhatsApp.\n"
            f"Status: WAITING_CUSTOMER_RESPONSE"
        )


# =============================================================================
# TOOL 6: confirm_stop_arrival_and_delivery_tool
# =============================================================================
class ConfirmStopArrivalAndDeliveryInput(BaseModel):
    driver_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of driver (e.g. '9111222333')",
    )
    stop_id: str = Field(
        ...,
        description="Delivery stop ID being completed (e.g. 'stp_123456')",
    )
    handover_notes: Optional[str] = Field(
        default=None,
        description="Handover or delivery notes (e.g. 'Handed to customer directly at gate')",
    )


async def confirm_stop_arrival_and_delivery(
    session: AsyncSession,
    *,
    driver_phone: str,
    stop_id: str,
    handover_notes: str | None = None,
) -> dict[str, Any]:
    """Complete a delivery stop, transition order status (if dropoff), update trip progress, and generate Google Maps navigation link to next stop."""
    assert driver_phone and len(driver_phone) >= 10, f"Invalid driver_phone: {driver_phone}"
    assert stop_id, "stop_id cannot be empty"

    stop = await session.get(SystemDeliveryStop, stop_id)
    assert stop is not None, f"Delivery stop not found: {stop_id}"
    assert stop.status != "COMPLETED", f"Delivery stop {stop_id} is already COMPLETED"

    route = await session.get(SystemDeliveryRoute, stop.route_id)
    assert route is not None, f"Delivery route not found: {stop.route_id}"
    assert route.driver_phone == driver_phone, f"Route {route.route_id} belongs to driver {route.driver_phone}, not {driver_phone}"

    # 1. Mark Current Stop COMPLETED
    stop.status = "COMPLETED"
    stop.actual_arrival = datetime.now()
    await session.flush()

    # 2. If Dropoff Stop, transition CustomerOrder status to DELIVERED via DW1
    from app.models.system import SystemDeliveryStopOrder
    stmt_so = select(SystemDeliveryStopOrder.order_id).where(SystemDeliveryStopOrder.stop_id == stop_id)
    order_ids = (await session.execute(stmt_so)).scalars().all()

    order_status_summary = []
    from app.executors.customer import execute_order_status_transition
    from app.executors.master import (
        execute_conversation_message_insert,
        execute_outbound_whatsapp_enqueue,
        execute_system_audit_log,
    )
    from app.models.customer import CustomerOrder

    for oid in order_ids:
        order = await session.get(CustomerOrder, oid)
        if order:
            if stop.stop_type == "DROPOFF_GATE":
                await execute_order_status_transition(
                    session,
                    order_id=oid,
                    target_status="DELIVERED",
                    actor_role="DRIVER",
                    reason=f"Driver completed drop-off at stop {stop_id}",
                )
                order_status_summary.append("DELIVERED")

                # Enqueue WhatsApp delivery notice to Customer
                cust_msg = (
                    f"🎉 YOUR MEAL HAS BEEN DELIVERED!\n"
                    f"Order #{oid} from {order.kitchen_name} has arrived at your address ({stop.location_name}).\n"
                    f"Enjoy your fresh home-cooked meal!\n"
                    f"Please rate your food and delivery experience (1 to 5 ⭐)."
                )
                await execute_outbound_whatsapp_enqueue(
                    session,
                    recipient_phone=order.customer_phone,
                    recipient_role="CUSTOMER",
                    message_text=cust_msg,
                )
                await execute_conversation_message_insert(
                    session,
                    phone=order.customer_phone,
                    actor_role="CUSTOMER",
                    direction="OUTBOUND",
                    source="SYSTEM_ALERT",
                    message_text=cust_msg,
                )
            elif stop.stop_type == "PICKUP_KITCHEN":
                await execute_order_status_transition(
                    session,
                    order_id=oid,
                    target_status="PICKED_UP",
                    actor_role="DRIVER",
                    reason=f"Driver completed pickup at kitchen stop {stop_id}",
                )
                order_status_summary.append("PICKED_UP")

    # 3. Check for Next Stop on Route & Generate Google Maps Single-Leg Navigation Link
    stmt_next = (
        select(SystemDeliveryStop)
        .where(
            SystemDeliveryStop.route_id == route.route_id,
            SystemDeliveryStop.stop_index == stop.stop_index + 1,
        )
    )
    next_stop = (await session.execute(stmt_next)).scalar_one_or_none()

    next_navigation_url = None
    next_stop_info = None

    if next_stop:
        # Generate Google Maps Navigation URL from current stop -> next stop
        orig_lat, orig_lng = float(stop.latitude), float(stop.longitude)
        dest_lat, dest_lng = float(next_stop.latitude), float(next_stop.longitude)
        next_navigation_url = (
            f"https://www.google.com/maps/dir/?api=1&origin={orig_lat},{orig_lng}"
            f"&destination={dest_lat},{dest_lng}&travelmode=driving"
        )
        next_stop.single_leg_maps_url = next_navigation_url
        await session.flush()

        next_stop_info = {
            "stop_index": next_stop.stop_index,
            "stop_type": next_stop.stop_type,
            "location_name": next_stop.location_name,
            "address": next_stop.address,
            "navigation_url": next_navigation_url,
        }

        # Send Google Maps Route Link to Driver via WhatsApp
        driver_next_msg = (
            f"✅ Stop #{stop.stop_index} ({stop.location_name}) COMPLETED!\n\n"
            f"📍 NEXT STOP #{next_stop.stop_index} [{next_stop.stop_type}]:\n"
            f"Location: {next_stop.location_name}\n"
            f"Address: {next_stop.address}\n\n"
            f"🗺️ Tap here to navigate: {next_navigation_url}"
        )
        await execute_outbound_whatsapp_enqueue(
            session,
            recipient_phone=driver_phone,
            recipient_role="DRIVER",
            message_text=driver_next_msg,
        )
        await execute_conversation_message_insert(
            session,
            phone=driver_phone,
            actor_role="DRIVER",
            direction="OUTBOUND",
            source="SYSTEM_ALERT",
            message_text=driver_next_msg,
        )
    else:
        # All stops completed! Update DriverTripStatus
        driver_finish_msg = (
            f"🎉 ROUTE COMPLETED! All stops on Route #{route.route_id} have been completed successfully. Great job!"
        )
        await execute_outbound_whatsapp_enqueue(
            session,
            recipient_phone=driver_phone,
            recipient_role="DRIVER",
            message_text=driver_finish_msg,
        )
        route.status = "COMPLETED"
        await session.flush()

    # 4. Record Audit Event via Master Executor #8
    await execute_system_audit_log(
        session,
        event_type="DELIVERY_STOP_COMPLETED",
        source_role="DRIVER",
        target_role="SYSTEM",
        payload={
            "stop_id": stop_id,
            "route_id": route.route_id,
            "stop_type": stop.stop_type,
            "has_next_stop": next_stop is not None,
            "next_navigation_url": next_navigation_url,
        },
        severity="INFO",
    )

    return {
        "stop_id": stop_id,
        "route_id": route.route_id,
        "stop_type": stop.stop_type,
        "stop_status": "COMPLETED",
        "order_statuses": order_status_summary,
        "has_next_stop": next_stop is not None,
        "next_stop_info": next_stop_info,
        "next_navigation_url": next_navigation_url,
    }


@tool("confirm_stop_arrival_and_delivery_tool", args_schema=ConfirmStopArrivalAndDeliveryInput)
async def confirm_stop_arrival_and_delivery_tool(
    driver_phone: str,
    stop_id: str,
    handover_notes: Optional[str] = None,
) -> str:
    """Confirm arrival and completion of a pickup or dropoff delivery stop, automatically generating Google Maps navigation link to the next stop."""
    from app.db.session import transaction

    async with transaction() as session:
        res = await confirm_stop_arrival_and_delivery(
            session,
            driver_phone=driver_phone,
            stop_id=stop_id,
            handover_notes=handover_notes,
        )
        if res["has_next_stop"]:
            next_info = res["next_stop_info"]
            return (
                f"✅ Stop #{res['stop_id']} COMPLETED!\n"
                f"📍 Next Stop #{next_info['stop_index']}: {next_info['location_name']}\n"
                f"🗺️ Navigation Link: {next_info['navigation_url']}\n"
                f"Sent to driver via WhatsApp."
            )
        else:
            return (
                f"🎉 Stop #{res['stop_id']} COMPLETED! All stops on Route #{res['route_id']} finished!\n"
                f"Route marked COMPLETED."
            )


