"""Integration test suite for Driver Tool 5: confirm_stop_arrival_and_delivery_tool."""

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
    SystemMealWindow,
    SystemOutboundQueue,
)
from app.tools.driver_tools import confirm_stop_arrival_and_delivery


@pytest.mark.asyncio
async def test_confirm_stop_arrival_and_delivery_with_next_stop(db_session: AsyncSession):
    session = db_session

    # 1. Seed Chef, Customer, Driver, Order, Route & 2 Stops (Pickup & Dropoff)
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
        window_id="win_confirm_01",
        service_date=date(2026, 8, 2),
        meal_type="LUNCH",
        cutoff_at=datetime.now(),
        status="LOCKED_PROCESSING",
    )
    session.add(win)
    await session.flush()

    order = CustomerOrder(
        order_id="ord_confirm_test_01",
        customer_phone=cust.customer_phone,
        chef_phone=chef.chef_phone,
        kitchen_name=chef.kitchen_name,
        meal_window="LUNCH",
        service_date=date(2026, 8, 2),
        status="PACKED",
        cart_subtotal=Decimal("250.00"),
        delivery_fee=Decimal("30.00"),
        total_amount=Decimal("280.00"),
    )
    session.add(order)
    await session.flush()

    route = SystemDeliveryRoute(
        route_id="rt_confirm_01",
        window_id=win.window_id,
        service_date=date(2026, 8, 2),
        meal_window="LUNCH",
        driver_phone=driver.driver_phone,
        total_stops=2,
        total_orders=1,
        status="ASSIGNED",
    )
    session.add(route)
    await session.flush()

    stop1 = SystemDeliveryStop(
        stop_id="stp_confirm_01",
        route_id=route.route_id,
        stop_index=1,
        stop_type="PICKUP_KITCHEN",
        target_ref_id=chef.chef_phone,
        location_name=chef.kitchen_name,
        address=chef.address,
        latitude=chef.latitude,
        longitude=chef.longitude,
        estimated_arrival=datetime.now(),
        status="PENDING",
    )
    stop2 = SystemDeliveryStop(
        stop_id="stp_confirm_02",
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
    stop_order1 = SystemDeliveryStopOrder(stop_id=stop1.stop_id, order_id=order.order_id)
    stop_order2 = SystemDeliveryStopOrder(stop_id=stop2.stop_id, order_id=order.order_id)
    session.add_all([stop1, stop2, stop_order1, stop_order2])
    await session.flush()

    # 2. Complete Stop 1 (Pickup at Kitchen)
    res = await confirm_stop_arrival_and_delivery(
        session,
        driver_phone=driver.driver_phone,
        stop_id=stop1.stop_id,
        handover_notes="Food picked up hot from chef",
    )

    # 3. Verify Stop 1 COMPLETED & Next Stop Google Maps Navigation Link Generated
    assert res["stop_status"] == "COMPLETED"
    assert res["has_next_stop"] is True
    assert res["next_stop_info"]["stop_index"] == 2
    assert "https://www.google.com/maps/dir/?api=1&origin=" in res["next_navigation_url"]
    assert "destination=19.1214684,73.0036295" in res["next_navigation_url"]

    await session.refresh(stop1)
    assert stop1.status == "COMPLETED"

    await session.refresh(stop2)
    assert stop2.single_leg_maps_url == res["next_navigation_url"]

    await session.refresh(order)
    assert order.status == "PICKED_UP"

    # 4. Verify Google Maps Link WhatsApp Message Sent to Driver
    stmt_driver_out = select(SystemOutboundQueue).where(SystemOutboundQueue.recipient_phone == driver.driver_phone)
    driver_msg = (await session.execute(stmt_driver_out)).scalar_one_or_none()
    assert driver_msg is not None
    assert "NEXT STOP #2" in driver_msg.message_text
    assert "Tap here to navigate" in driver_msg.message_text

    # 5. Complete Stop 2 (Dropoff at Customer Gate)
    res2 = await confirm_stop_arrival_and_delivery(
        session,
        driver_phone=driver.driver_phone,
        stop_id=stop2.stop_id,
        handover_notes="Handed to customer at gate",
    )

    assert res2["stop_status"] == "COMPLETED"
    assert res2["has_next_stop"] is False

    await session.refresh(order)
    assert order.status == "DELIVERED"

    await session.refresh(route)
    assert route.status == "COMPLETED"
