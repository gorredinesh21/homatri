"""Integration test suite for Chef Tool 9: get_assigned_driver_eta_tool."""

import pytest
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chef import ChefProfile
from app.models.customer import CustomerOrder, CustomerProfile
from app.models.driver import DriverProfile
from app.models.system import SystemDeliveryRoute, SystemDeliveryStop, SystemDeliveryStopOrder, SystemMealWindow
from app.tools.chef_tools import get_assigned_driver_eta


@pytest.mark.asyncio
async def test_get_assigned_driver_eta_success(db_session: AsyncSession):
    session = db_session

    # 1. Seed Chef, Customer, Driver, Order, Route & Pickup Stop
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
        window_id="win_eta_01",
        service_date=date(2026, 8, 2),
        meal_type="LUNCH",
        cutoff_at=datetime.now(),
        status="LOCKED_PROCESSING",
    )
    session.add(win)
    await session.flush()

    order = CustomerOrder(
        order_id="ord_eta_test_01",
        customer_phone=cust.customer_phone,
        chef_phone=chef.chef_phone,
        kitchen_name=chef.kitchen_name,
        meal_window="LUNCH",
        service_date=date(2026, 8, 2),
        status="COOKING",
        cart_subtotal=Decimal("250.00"),
        delivery_fee=Decimal("30.00"),
        total_amount=Decimal("280.00"),
    )
    session.add(order)
    await session.flush()

    route = SystemDeliveryRoute(
        route_id="rt_eta_01",
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

    stop = SystemDeliveryStop(
        stop_id="stp_eta_01",
        route_id=route.route_id,
        stop_index=1,
        stop_type="PICKUP_KITCHEN",
        target_ref_id=chef.chef_phone,
        location_name=chef.kitchen_name,
        address=chef.address,
        latitude=chef.latitude,
        longitude=chef.longitude,
        estimated_arrival=datetime(2026, 8, 2, 12, 15),
    )
    stop_order = SystemDeliveryStopOrder(
        stop_id=stop.stop_id,
        order_id=order.order_id,
    )
    session.add_all([stop, stop_order])
    await session.flush()

    # 2. Query Driver ETA
    res = await get_assigned_driver_eta(
        session,
        chef_phone=chef.chef_phone,
        order_id=order.order_id,
    )

    # 3. Verify Response Attributes
    assert res["has_assigned_driver"] is True
    assert res["driver_name"] == "Vikram Driver"
    assert res["driver_phone"] == driver.driver_phone
    assert res["vehicle_info"] == "BIKE (MH43AB1234)"
    assert "12:15 PM" in res["estimated_arrival"]
    assert res["stop_status"] == "PENDING"
