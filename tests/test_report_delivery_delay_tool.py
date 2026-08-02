"""Integration test suite for Driver Tool 4: report_delivery_delay_or_gate_issue_tool."""

import pytest
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chef import ChefProfile
from app.models.customer import CustomerOrder, CustomerProfile
from app.models.driver import DriverProfile
from app.models.system import (
    SystemAgentLog,
    SystemDeliveryRoute,
    SystemDeliveryStop,
    SystemDeliveryStopOrder,
    SystemHitlSession,
    SystemMealWindow,
    SystemOutboundQueue,
)
from app.tools.driver_tools import report_delivery_delay_or_gate_issue


@pytest.mark.asyncio
async def test_report_delivery_delay_or_gate_issue_success(db_session: AsyncSession):
    session = db_session

    # 1. Seed Chef, Customer, Driver, Order, Route & Dropoff Stop
    chef = ChefProfile(
        chef_phone="9876543210",
        kitchen_name="Indravati Tiffins",
        chef_name="Chef Sunita",
        address="Sector 4, Ghansoli",
        latitude=Decimal("19.1190086"),
        longitude=Decimal("72.9934054"),
    )
    cust = CustomerProfile(
        customer_phone="9123456789",
        name="Ramesh Test",
        delivery_address="Indravati CHS, Ghansoli",
        latitude=Decimal("19.1214684"),
        longitude=Decimal("73.0036295"),
    )
    driver = DriverProfile(
        driver_phone="9111222333",
        driver_name="Vikram Driver",
        vehicle_type="BIKE",
        vehicle_number="MH43AB1234",
    )
    session.add_all([chef, cust, driver])
    await session.flush()

    win = SystemMealWindow(
        window_id="win_delay_01",
        service_date=date(2026, 8, 2),
        meal_type="LUNCH",
        cutoff_at=datetime.now(),
        status="LOCKED_PROCESSING",
    )
    session.add(win)
    await session.flush()

    order = CustomerOrder(
        order_id="ord_delay_test_01",
        customer_phone=cust.customer_phone,
        chef_phone=chef.chef_phone,
        kitchen_name=chef.kitchen_name,
        meal_window="LUNCH",
        service_date=date(2026, 8, 2),
        status="PICKED_UP",
        cart_subtotal=Decimal("250.00"),
        delivery_fee=Decimal("30.00"),
        total_amount=Decimal("280.00"),
    )
    session.add(order)
    await session.flush()

    route = SystemDeliveryRoute(
        route_id="rt_delay_01",
        window_id=win.window_id,
        service_date=date(2026, 8, 2),
        meal_window="LUNCH",
        driver_phone=driver.driver_phone,
        total_stops=2,
        total_orders=1,
        status="EN_ROUTE_DELIVERY",
    )
    session.add(route)
    await session.flush()

    stop = SystemDeliveryStop(
        stop_id="stp_delay_01",
        route_id=route.route_id,
        stop_index=2,
        stop_type="DROPOFF_GATE",
        target_ref_id=cust.customer_phone,
        location_name="Indravati CHS Gate",
        address=cust.delivery_address,
        latitude=cust.latitude,
        longitude=cust.longitude,
        estimated_arrival=datetime.now(),
        status="PENDING",
    )
    stop_order = SystemDeliveryStopOrder(
        stop_id=stop.stop_id,
        order_id=order.order_id,
    )
    session.add_all([stop, stop_order])
    await session.flush()

    # 2. Report Delivery Gate Issue
    res = await report_delivery_delay_or_gate_issue(
        session,
        driver_phone=driver.driver_phone,
        stop_id=stop.stop_id,
        issue_type="GATE_SECURITY",
        delay_minutes=15,
        issue_notes="Security guard at Gate 2 refusing entry without flat passcode",
    )

    # 3. Verify HITL Session Created & Stop Status DELAYED
    assert res["status"] == "WAITING_CUSTOMER_RESPONSE"
    assert res["issue_type"] == "GATE_SECURITY"
    assert res["customer_phone"] == cust.customer_phone

    await session.refresh(stop)
    assert stop.status == "DELAYED"

    # 4. Verify System HITL Session & WhatsApp Alert
    hitl = await session.get(SystemHitlSession, res["hitl_session_id"])
    assert hitl is not None
    assert hitl.interrupt_type == "GATE_SECURITY_OR_DELAY"
    assert hitl.status == "WAITING"

    stmt_out = select(SystemOutboundQueue).where(SystemOutboundQueue.recipient_phone == cust.customer_phone)
    outbound = (await session.execute(stmt_out)).scalar_one_or_none()
    assert outbound is not None
    assert "DELIVERY ISSUE ALERT" in outbound.message_text
    assert "GATE_SECURITY" in outbound.message_text
