"""Driver domain write executors (Category 3).

Single-owner write executors for driver_profiles and driver_trip_status tables.
Tracks driver registration, route assignments, and trip execution phase updates.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.ids import generate_id
from backend.app.models.driver import DriverProfile, DriverTripStatus
from backend.app.models.enums import TripStatus, TRIP_ACTIVE_PHASES


# =============================================================================
# EXECUTOR 1: Driver Profile Onboarding / Upsert
# =============================================================================
async def execute_driver_profile_upsert(
    session: AsyncSession,
    *,
    driver_phone: str,
    driver_name: str,
    vehicle_type: str = "BIKE",
    vehicle_number: str,
    vehicle_model: str | None = None,
    driver_license_number: str | None = None,
    alternate_phone: str | None = None,
    is_on_shift: bool = True,
    active_status: bool = True,
) -> DriverProfile:
    """Executor #1 — Onboard a driver or update profile settings (idempotent)."""
    driver = await session.get(DriverProfile, driver_phone)

    if driver is None:
        driver = DriverProfile(
            driver_phone=driver_phone,
            driver_name=driver_name,
            vehicle_type=vehicle_type,
            vehicle_number=vehicle_number,
            vehicle_model=vehicle_model,
            driver_license_number=driver_license_number,
            alternate_phone=alternate_phone,
            is_on_shift=is_on_shift,
            active_status=active_status,
        )
        session.add(driver)
    else:
        driver.driver_name = driver_name
        driver.vehicle_type = vehicle_type
        driver.vehicle_number = vehicle_number
        if vehicle_model is not None:
            driver.vehicle_model = vehicle_model
        if driver_license_number is not None:
            driver.driver_license_number = driver_license_number
        if alternate_phone is not None:
            driver.alternate_phone = alternate_phone
        driver.is_on_shift = is_on_shift
        driver.active_status = active_status

    await session.flush()
    return driver


# =============================================================================
# EXECUTOR 2: Initialize Driver Trip Record
# =============================================================================
async def execute_driver_trip_initialization(
    session: AsyncSession,
    *,
    driver_phone: str,
    route_id: str,
    service_date: date,
    meal_window: str = "LUNCH",
    total_stops: int,
) -> DriverTripStatus:
    """Executor #2 — Create a new driver trip status record for an assigned route."""
    driver = await session.get(DriverProfile, driver_phone)
    assert driver is not None, f"Driver profile not found for phone: {driver_phone}"
    assert driver.active_status is True, f"Driver {driver_phone} is suspended / inactive"

    trip_id = generate_id("trp")
    trip = DriverTripStatus(
        trip_id=trip_id,
        driver_phone=driver_phone,
        route_id=route_id,
        service_date=service_date,
        meal_window=meal_window,
        status="ASSIGNED",
        current_stop_index=1,
        total_stops=total_stops,
        completed_stops=0,
    )
    session.add(trip)

    # Link current assigned route ID on driver profile
    driver.current_assigned_route_id = route_id
    await session.flush()

    return trip


# =============================================================================
# EXECUTOR 3: Driver Trip Phase & Progress Update
# =============================================================================
async def execute_driver_trip_phase_update(
    session: AsyncSession,
    *,
    driver_phone: str,
    route_id: str,
    target_status: str,
    current_stop_index: int | None = None,
    completed_stops: int | None = None,
    delay_notes: str | None = None,
) -> DriverTripStatus:
    """Executor #3 — Single owner for driver trip execution phase updates.

    Valid phases: ASSIGNED, EN_ROUTE_PICKUP, AT_KITCHEN, EN_ROUTE_DELIVERY, AT_GATE, COMPLETED.
    Updates timestamps automatically when starting route or completing.
    """
    # Fetch active trip for driver and route
    trip = (
        await session.execute(
            select(DriverTripStatus).where(
                DriverTripStatus.driver_phone == driver_phone,
                DriverTripStatus.route_id == route_id,
            )
        )
    ).scalar_one_or_none()

    assert trip is not None, f"Active trip not found for driver {driver_phone} on route {route_id}"
    assert target_status in {
        "ASSIGNED",
        "EN_ROUTE_PICKUP",
        "AT_KITCHEN",
        "EN_ROUTE_DELIVERY",
        "AT_GATE",
        "COMPLETED",
    }, f"Invalid trip target status: {target_status}"

    now = datetime.now()
    trip.status = target_status

    if target_status == "EN_ROUTE_PICKUP" and trip.trip_started_at is None:
        trip.trip_started_at = now

    if current_stop_index is not None:
        trip.current_stop_index = current_stop_index

    if completed_stops is not None:
        trip.completed_stops = completed_stops

    if delay_notes is not None:
        trip.delay_notes = delay_notes

    if target_status == "COMPLETED":
        trip.completed_stops = trip.total_stops
        trip.trip_completed_at = now
        # Clear driver's active route assignment upon completion
        driver = await session.get(DriverProfile, driver_phone)
        if driver and driver.current_assigned_route_id == route_id:
            driver.current_assigned_route_id = None

    await session.flush()
    return trip
