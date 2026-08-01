"""Category 2 — Driver LLM tools integration tests (runs against PostgreSQL)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import pytest

from app.models.driver import DriverProfile
from app.models.system import SystemDeliveryRoute, SystemDeliveryStop, SystemMealWindow
from app.tools import driver_tools

DRIVER = "9999988888"


async def test_get_driver_profile_registered_and_unregistered(db_session):
    # 1. Query unregistered phone -> returns None
    unreg = await driver_tools.get_driver_profile(db_session, driver_phone=DRIVER)
    assert unreg is None

    # 2. Seed registered driver profile
    db_session.add(
        DriverProfile(
            driver_phone=DRIVER,
            driver_name="Raju Partner",
            vehicle_type="BIKE",
            vehicle_number="TS 09 EA 1234",
            is_on_shift=True,
            active_status=True,
        )
    )
    await db_session.flush()

    # 3. Query registered driver -> returns profile
    reg = await driver_tools.get_driver_profile(db_session, driver_phone=DRIVER)
    assert reg is not None
    assert reg["driver_name"] == "Raju Partner"
    assert reg["vehicle_number"] == "TS 09 EA 1234"
    assert reg["is_on_shift"] is True


async def test_register_driver_profile_tool_success_and_assertions(db_session):
    # Register new driver
    driver = await driver_tools.register_driver_profile(
        db_session,
        driver_phone=DRIVER,
        driver_name="Raju Partner",
        vehicle_type="BIKE",
        vehicle_number="TS 09 EA 5678",
        vehicle_model="Hero Splendor",
        driver_license_number="DL-1420110012345",
    )
    assert driver.driver_name == "Raju Partner"
    assert driver.vehicle_number == "TS 09 EA 5678"
    assert driver.is_on_shift is True

    # Guard 2 Assertion: Invalid vehicle type raises AssertionError
    with pytest.raises(AssertionError):
        await driver_tools.register_driver_profile(
            db_session,
            driver_phone=DRIVER,
            driver_name="Raju Partner",
            vehicle_type="HELICOPTER",  # Invalid vehicle type!
            vehicle_number="TS 09 EA 5678",
        )


async def test_get_driver_active_delivery_route_tool(db_session):
    # 1. Seed meal window and driver
    db_session.add(
        SystemMealWindow(
            window_id="win_lunch_01",
            meal_type="LUNCH",
            service_date=date(2026, 8, 1),
            cutoff_at=datetime(2026, 8, 1, 10, 30),
        )
    )
    db_session.add(
        DriverProfile(
            driver_phone=DRIVER,
            driver_name="Raju Partner",
            vehicle_type="BIKE",
            vehicle_number="TS 09 EA 1234",
            is_on_shift=True,
            active_status=True,
        )
    )
    await db_session.flush()

    # 2. Seed active route and stop
    db_session.add(
        SystemDeliveryRoute(
            route_id="rt_test_101",
            window_id="win_lunch_01",
            driver_phone=DRIVER,
            service_date=date(2026, 8, 1),
            meal_window="LUNCH",
            total_stops=2,
            total_orders=2,
            total_distance_km=Decimal("4.50"),
            status="ASSIGNED",
        )
    )
    await db_session.flush()

    db_session.add(
        SystemDeliveryStop(
            stop_id="stp_test_101",
            route_id="rt_test_101",
            stop_index=1,
            stop_type="KITCHEN_PICKUP",
            target_ref_id="9876543210",
            location_name="Ramesh Kitchen",
            address="Flat 402, Hitech City",
            latitude=Decimal("17.44800000"),
            longitude=Decimal("78.38100000"),
            estimated_arrival=datetime(2026, 8, 1, 12, 0),
            status="PENDING",
        )
    )
    await db_session.flush()

    # 3. Query active route
    route_data = await driver_tools.get_driver_active_delivery_route(db_session, driver_phone=DRIVER)
    assert route_data is not None
    assert route_data["route_id"] == "rt_test_101"
    assert len(route_data["stops"]) == 1
    assert route_data["stops"][0]["location_name"] == "Ramesh Kitchen"


async def test_update_driver_duty_status_tool(db_session):
    db_session.add(
        DriverProfile(
            driver_phone=DRIVER,
            driver_name="Raju Partner",
            vehicle_type="BIKE",
            vehicle_number="TS 09 EA 1234",
            is_on_shift=True,
            active_status=True,
        )
    )
    await db_session.flush()

    # Toggle to OFF SHIFT
    driver_off = await driver_tools.update_driver_duty_status(
        db_session,
        driver_phone=DRIVER,
        is_on_shift=False,
    )
    assert driver_off.is_on_shift is False

    # Toggle back to ON SHIFT
    driver_on = await driver_tools.update_driver_duty_status(
        db_session,
        driver_phone=DRIVER,
        is_on_shift=True,
    )
    assert driver_on.is_on_shift is True
