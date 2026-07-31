"""Category 3 — Driver executor integration tests (runs against PostgreSQL)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import pytest

from app.executors import driver as driver_exec
from app.models.driver import DriverProfile, DriverTripStatus
from app.models.system import SystemDeliveryRoute, SystemMealWindow


DRIVER = "9988776655"
ROUTE_ID = "rt_test0001"
SERVICE_DATE = date(2026, 7, 31)


async def _seed_driver_and_route(s) -> None:
    # 1. Driver Profile
    s.add(
        DriverProfile(
            driver_phone=DRIVER,
            driver_name="Raju Driver",
            vehicle_type="BIKE",
            vehicle_number="TS 09 EQ 1234",
            is_on_shift=True,
            active_status=True,
        )
    )
    # 2. Meal Window
    s.add(
        SystemMealWindow(
            window_id="win_test0001",
            service_date=SERVICE_DATE,
            meal_type="LUNCH",
            cutoff_at=SERVICE_DATE,
            status="OPEN",
        )
    )
    await s.flush()

    # 3. System Delivery Route (Stub for FK)
    s.add(
        SystemDeliveryRoute(
            route_id=ROUTE_ID,
            window_id="win_test0001",
            driver_phone=DRIVER,
            service_date=SERVICE_DATE,
            meal_window="LUNCH",
            total_stops=3,
            total_orders=5,
            total_distance_km=Decimal("4.5"),
            estimated_duration_mins=25,
            status="ASSIGNED",
        )
    )
    await s.flush()



async def test_driver_profile_upsert(db_session):
    profile = await driver_exec.execute_driver_profile_upsert(
        db_session,
        driver_phone="9999988888",
        driver_name="Vikram",
        vehicle_type="EV",
        vehicle_number="TS 07 EV 5678",
        vehicle_model="Ather 450X",
    )
    assert profile.driver_phone == "9999988888"
    assert profile.vehicle_type == "EV"
    assert profile.active_status is True


async def test_driver_trip_initialization_and_phase_updates(db_session):
    await _seed_driver_and_route(db_session)

    # 1. Initialize Driver Trip
    trip = await driver_exec.execute_driver_trip_initialization(
        db_session,
        driver_phone=DRIVER,
        route_id=ROUTE_ID,
        service_date=SERVICE_DATE,
        meal_window="LUNCH",
        total_stops=3,
    )
    assert trip.trip_id.startswith("trp_")
    assert trip.status == "ASSIGNED"

    # Verify driver profile has active_assigned_route_id linked
    driver = await db_session.get(DriverProfile, DRIVER)
    assert driver.current_assigned_route_id == ROUTE_ID

    # 2. Transition phase to EN_ROUTE_PICKUP
    t1 = await driver_exec.execute_driver_trip_phase_update(
        db_session,
        driver_phone=DRIVER,
        route_id=ROUTE_ID,
        target_status="EN_ROUTE_PICKUP",
        current_stop_index=1,
    )
    assert t1.status == "EN_ROUTE_PICKUP"
    assert t1.trip_started_at is not None

    # 3. Transition phase to AT_KITCHEN
    t2 = await driver_exec.execute_driver_trip_phase_update(
        db_session,
        driver_phone=DRIVER,
        route_id=ROUTE_ID,
        target_status="AT_KITCHEN",
        completed_stops=1,
    )
    assert t2.status == "AT_KITCHEN"
    assert t2.completed_stops == 1

    # 4. Complete trip
    t3 = await driver_exec.execute_driver_trip_phase_update(
        db_session,
        driver_phone=DRIVER,
        route_id=ROUTE_ID,
        target_status="COMPLETED",
    )
    assert t3.status == "COMPLETED"
    assert t3.completed_stops == 3
    assert t3.trip_completed_at is not None
    assert driver.current_assigned_route_id is None  # Unlinked on completion!
