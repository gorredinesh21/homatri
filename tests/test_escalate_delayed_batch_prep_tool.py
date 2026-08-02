"""Integration test suite for escalate_delayed_batch_prep_tool."""

import pytest
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chef import ChefProfile
from app.models.driver import DriverProfile
from app.models.system import SystemAgentLog, SystemDeliveryRoute, SystemDeliveryStop, SystemMealWindow, SystemOutboundQueue
from app.tools.master_tools import escalate_delayed_batch_prep


@pytest.mark.asyncio
async def test_escalate_delayed_batch_prep_success(db_session: AsyncSession):
    session = db_session

    # 0. Seed SystemMealWindow
    meal_window_row = SystemMealWindow(
        window_id="win_test_lunch",
        service_date=date(2026, 8, 2),
        meal_type="LUNCH",
        cutoff_at=datetime.now(),
        status="LOCKED_PROCESSING",
    )
    session.add(meal_window_row)
    await session.flush()

    # 1. Seed Chef and Driver Profiles

    chef = ChefProfile(
        chef_phone="9876543210",
        kitchen_name="Cloud 36 Kitchen",
        chef_name="Chef Cloud",
        address="Cloud 36, Sector 11, Ghansoli",
        latitude=Decimal("19.1190086"),
        longitude=Decimal("72.9934054"),
        active_status=True,
    )
    driver = DriverProfile(
        driver_phone="9988776655",
        driver_name="Ramesh Driver",
        vehicle_number="MH-43-AB-1234",
        is_on_shift=True,
        active_status=True,
    )
    session.add_all([chef, driver])
    await session.flush()

    # 2. Seed SystemDeliveryRoute & SystemDeliveryStop
    route = SystemDeliveryRoute(
        route_id="rt_test_delay_01",
        window_id="win_test_lunch",
        driver_phone=driver.driver_phone,
        service_date=date(2026, 8, 2),
        meal_window="LUNCH",
        total_stops=2,
        total_orders=1,
        status="ASSIGNED",
    )
    session.add(route)
    await session.flush()

    stop = SystemDeliveryStop(
        stop_id="stp_test_delay_01",
        route_id=route.route_id,
        stop_index=1,
        stop_type="PICKUP_KITCHEN",
        target_ref_id=chef.chef_phone,
        location_name=chef.kitchen_name,
        address=chef.address,
        latitude=Decimal("19.1190086"),
        longitude=Decimal("72.9934054"),
        estimated_arrival=datetime.fromisoformat("2026-08-02T12:15:00"),

        status="PENDING",
    )
    session.add(stop)
    await session.flush()

    # 3. Call escalate_delayed_batch_prep
    res = await escalate_delayed_batch_prep(
        session,
        chef_phone=chef.chef_phone,
        service_date="2026-08-02",
        meal_window="LUNCH",
        delay_minutes=15,
        delay_reason="Extra roti batch cooking required",
        related_order_ids=["ord_delay_101"],
    )

    # 4. Verify Return Summary
    assert res is not None
    assert res["chef_phone"] == "9876543210"
    assert res["delay_minutes"] == 15
    assert res["driver_notified"] == "9988776655"

    # 5. Verify SystemAgentLog row
    stmt_log = select(SystemAgentLog).where(SystemAgentLog.log_id == res["log_id"])
    audit_log = (await session.execute(stmt_log)).scalar_one_or_none()
    assert audit_log is not None
    assert audit_log.event_type == "KITCHEN_PREP_DELAY"
    assert audit_log.severity == "WARNING"

    # 6. Verify Outbound Queue messages for Chef & Driver
    stmt_chef_out = select(SystemOutboundQueue).where(
        SystemOutboundQueue.recipient_phone == chef.chef_phone,
        SystemOutboundQueue.recipient_role == "CHEF",
    )
    chef_outbound = (await session.execute(stmt_chef_out)).scalar_one_or_none()
    assert chef_outbound is not None
    assert "15 minutes" in chef_outbound.message_text

    stmt_driver_out = select(SystemOutboundQueue).where(
        SystemOutboundQueue.recipient_phone == driver.driver_phone,
        SystemOutboundQueue.recipient_role == "DRIVER",
    )
    driver_outbound = (await session.execute(stmt_driver_out)).scalar_one_or_none()
    assert driver_outbound is not None
    assert "15-minute cooking delay" in driver_outbound.message_text


@pytest.mark.asyncio
async def test_escalate_delayed_batch_prep_invalid_delay_assertion(db_session: AsyncSession):
    session = db_session
    with pytest.raises(AssertionError, match="Invalid delay_minutes"):
        await escalate_delayed_batch_prep(
            session,
            chef_phone="9876543210",
            service_date="2026-08-02",
            meal_window="LUNCH",
            delay_minutes=0,  # invalid delay
            delay_reason="Reason text",
        )
