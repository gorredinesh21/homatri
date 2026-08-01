"""Driver Domain LLM Tools (Category 2).

Encapsulates Driver Concierge Agent tools with Guard 2 Pre-Condition Assertions.
Tool 1: get_driver_profile_tool (Read-only, Same Domain).
Tool 2: register_driver_profile_tool (Write Executor #16, Same Domain).
Tool 3: get_driver_active_delivery_route_tool (Read-only, Same Domain).
Tool 4: update_driver_duty_status_tool (Write Executor #17, Same Domain).
"""

from __future__ import annotations

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
