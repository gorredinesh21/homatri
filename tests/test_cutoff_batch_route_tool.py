"""Integration test suite for execute_cutoff_batch_and_route_optimization_tool."""

import pytest
from datetime import date, datetime

from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chef import ChefProfile
from app.models.customer import CustomerProfile, CustomerOrder
from app.models.driver import DriverProfile
from app.models.system import SystemMealWindow, SystemDeliveryRoute, SystemDeliveryStop
from app.tools.master_tools import execute_cutoff_batch_and_route_optimization


@pytest.mark.asyncio
async def test_execute_cutoff_batch_and_route_optimization_success(db_session: AsyncSession):
    session = db_session

    # 1. Seed Chef, Customer, Driver Profiles
    chef = ChefProfile(
        chef_phone="9876543210",
        kitchen_name="Cloud 36 Kitchen",
        chef_name="Chef Cloud",
        address="Cloud 36, Sector 11, Ghansoli",
        latitude=Decimal("19.1190086"),
        longitude=Decimal("72.9934054"),
        active_status=True,
    )

    session.add(chef)

    cust1 = CustomerProfile(
        customer_phone="9123456789",
        name="Rajesh Kumar",
        apartment_name="Indravati CHS Gate 1",
        delivery_address="Indravati CHS, Sector 6, Ghansoli",
        latitude=Decimal("19.1214684"),
        longitude=Decimal("73.0036295"),
    )
    cust2 = CustomerProfile(
        customer_phone="9123456790",
        name="Priya Sharma",
        apartment_name="Akshar Elementa Gate 2",
        delivery_address="Akshar Elementa, Kopar Khairne",
        latitude=Decimal("19.1071958"),
        longitude=Decimal("73.0058203"),
    )
    session.add_all([cust1, cust2])

    driver = DriverProfile(
        driver_phone="9988776655",
        driver_name="Ramesh Driver",
        vehicle_number="MH-43-AB-1234",
        is_on_shift=True,
        active_status=True,
    )

    session.add(driver)
    await session.flush()


    # 2. Seed SystemMealWindow
    window = SystemMealWindow(
        window_id="win_lunch_20260801",
        service_date=date(2026, 8, 1),
        meal_type="LUNCH",
        cutoff_at=datetime(2026, 8, 1, 12, 0),
        status="OPEN",
    )

    session.add(window)

    # 3. Seed 2 Confirmed Orders
    order1 = CustomerOrder(
        order_id="ord_test_cut_01",
        customer_phone=cust1.customer_phone,
        chef_phone=chef.chef_phone,
        kitchen_name=chef.kitchen_name,
        cart_subtotal=Decimal("250.00"),
        delivery_fee=Decimal("30.00"),
        total_amount=Decimal("280.00"),
        service_date=date(2026, 8, 1),
        meal_window="LUNCH",
        status="CONFIRMED",
    )
    order2 = CustomerOrder(
        order_id="ord_test_cut_02",
        customer_phone=cust2.customer_phone,
        chef_phone=chef.chef_phone,
        kitchen_name=chef.kitchen_name,
        cart_subtotal=Decimal("300.00"),
        delivery_fee=Decimal("30.00"),
        total_amount=Decimal("330.00"),
        service_date=date(2026, 8, 1),
        meal_window="LUNCH",
        status="CONFIRMED",
    )

    session.add_all([order1, order2])
    await session.flush()


    # 4. Define Stop Data
    stops_data = [
        {
            "target_ref_id": chef.chef_phone,
            "location_name": chef.kitchen_name,
            "address": chef.address,
            "latitude": 19.1190086,
            "longitude": 72.9934054,
            "estimated_arrival": "12:15",
            "order_ids": [order1.order_id, order2.order_id],
        },
        {
            "target_ref_id": cust1.customer_phone,
            "location_name": cust1.apartment_name,
            "address": cust1.delivery_address,
            "latitude": 19.1214684,
            "longitude": 73.0036295,
            "estimated_arrival": "12:30",
            "order_ids": [order1.order_id],
        },
        {
            "target_ref_id": cust2.customer_phone,
            "location_name": cust2.apartment_name,
            "address": cust2.delivery_address,
            "latitude": 19.1071958,
            "longitude": 73.0058203,
            "estimated_arrival": "12:45",
            "order_ids": [order2.order_id],
        },
    ]


    # 5. Execute Cutoff Batch & Route Optimization
    route = await execute_cutoff_batch_and_route_optimization(
        session,
        window_id=window.window_id,
        driver_phone=driver.driver_phone,
        service_date="2026-08-01",
        meal_window="LUNCH",
        stops_data=stops_data,
    )

    # 6. Verify Results
    assert route is not None
    assert route.window_id == "win_lunch_20260801"
    assert route.driver_phone == "9988776655"
    assert route.total_stops == 3
    assert route.total_orders == 2
    assert route.status == "ASSIGNED"

    # Verify Order Statuses Transitioned to BATCHED
    await session.refresh(order1)
    await session.refresh(order2)
    assert order1.status == "BATCHED"
    assert order2.status == "BATCHED"

    # Verify Meal Window Status Transitioned to LOCKED_PROCESSING
    await session.refresh(window)
    assert window.status == "LOCKED_PROCESSING"
