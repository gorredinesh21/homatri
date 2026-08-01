"""Category 1 — Chef LLM tools integration tests (runs against PostgreSQL)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import pytest

from app.models.chef import ChefMenuItem, ChefProfile
from app.models.customer import CustomerOrder, CustomerOrderItem, CustomerProfile
from app.tools import chef_tools

CHEF = "9876543210"
CUSTOMER = "9111111111"
ITEM_1 = "itm_paneer01"
ITEM_2 = "itm_vegthali02"


async def _seed_chef_and_menu(s) -> None:
    s.add(
        ChefProfile(
            chef_phone=CHEF,
            kitchen_name="Ramesh Kitchen",
            chef_name="Ramesh",
            address="Flat 402, Hitech City",
            latitude=Decimal("17.44829380"),
            longitude=Decimal("78.38148410"),
        )
    )
    await s.flush()

    s.add(
        ChefMenuItem(
            menu_item_id=ITEM_1,
            chef_phone=CHEF,
            dish_name="Paneer Butter Masala",
            unit_price=Decimal("180.00"),
            meal_type="LUNCH",
            dietary_tag="VEG",
            is_available=True,
        )
    )
    s.add(
        ChefMenuItem(
            menu_item_id=ITEM_2,
            chef_phone=CHEF,
            dish_name="Special Veg Thali",
            unit_price=Decimal("150.00"),
            meal_type="LUNCH",
            dietary_tag="VEG",
            is_available=False,  # Out of stock
        )
    )
    await s.flush()


async def test_get_chef_profile_tool_success_and_assertions(db_session):
    await _seed_chef_and_menu(db_session)

    profile = await chef_tools.get_chef_profile(db_session, chef_phone=CHEF)
    assert profile["kitchen_name"] == "Ramesh Kitchen"
    assert profile["chef_name"] == "Ramesh"
    assert profile["latitude"] == 17.4482938

    # Missing chef phone raises AssertionError
    with pytest.raises(AssertionError):
        await chef_tools.get_chef_profile(db_session, chef_phone="0000000000")


async def test_get_chef_menu_returns_all_and_filtered_items(db_session):
    await _seed_chef_and_menu(db_session)

    # 1. Include unavailable -> returns 2 items
    all_items = await chef_tools.get_chef_menu(db_session, chef_phone=CHEF, include_unavailable=True)
    assert len(all_items) == 2

    # 2. Exclude unavailable -> returns 1 item only (Paneer Butter Masala)
    available_items = await chef_tools.get_chef_menu(db_session, chef_phone=CHEF, include_unavailable=False)
    assert len(available_items) == 1
    assert available_items[0]["dish_name"] == "Paneer Butter Masala"


async def test_get_chef_menu_assertions_on_missing_chef(db_session):
    with pytest.raises(AssertionError):
        await chef_tools.get_chef_menu(db_session, chef_phone="0000000000")


async def test_update_daily_dish_capacity_tool_success_and_assertions(db_session):
    await _seed_chef_and_menu(db_session)

    inv = await chef_tools.update_daily_dish_capacity(
        db_session,
        chef_phone=CHEF,
        menu_item_id=ITEM_1,
        service_date="2026-08-01",
        meal_window="LUNCH",
        max_capacity=15,
    )
    assert inv.max_capacity == 15

    # Test Guard 2 Assertion: Negative capacity must raise AssertionError
    with pytest.raises(AssertionError):
        await chef_tools.update_daily_dish_capacity(
            db_session,
            chef_phone=CHEF,
            menu_item_id=ITEM_1,
            service_date="2026-08-01",
            meal_window="LUNCH",
            max_capacity=-5,
        )

    # Test Guard 2 Assertion: Wrong chef ownership must raise AssertionError
    with pytest.raises(AssertionError):
        await chef_tools.update_daily_dish_capacity(
            db_session,
            chef_phone="9111111111",  # Wrong chef
            menu_item_id=ITEM_1,
            service_date="2026-08-01",
            meal_window="LUNCH",
            max_capacity=20,
        )


async def test_toggle_dish_availability_tool_success_and_assertions(db_session):
    await _seed_chef_and_menu(db_session)

    # Toggle Paneer Butter Masala to OUT OF STOCK
    item = await chef_tools.toggle_dish_availability(
        db_session,
        chef_phone=CHEF,
        menu_item_id=ITEM_1,
        is_available=False,
    )
    assert item.is_available is False

    # Test Guard 2 Assertion: Wrong chef trying to toggle availability must raise AssertionError
    with pytest.raises(AssertionError):
        await chef_tools.toggle_dish_availability(
            db_session,
            chef_phone="9111111111",  # Wrong chef
            menu_item_id=ITEM_1,
            is_available=True,
        )


async def test_get_chef_daily_batch_checklist_tool(db_session):
    await _seed_chef_and_menu(db_session)

    db_session.add(
        CustomerProfile(
            customer_phone=CUSTOMER,
            name="Dinesh",
            delivery_address="Flat 301, My Home Bhooja",
            is_registered=True,
        )
    )
    await db_session.flush()

    order = CustomerOrder(
        order_id="ord_chk_001",
        customer_phone=CUSTOMER,
        chef_phone=CHEF,
        kitchen_name="Ramesh Kitchen",
        meal_window="LUNCH",
        service_date=date(2026, 7, 31),
        status="CONFIRMED",
    )
    db_session.add(order)
    await db_session.flush()

    db_session.add(
        CustomerOrderItem(
            item_id="ori_chk_001",
            order_id="ord_chk_001",
            menu_item_id=ITEM_1,
            chef_phone=CHEF,
            dish_name="Paneer Butter Masala",
            quantity=3,
            unit_price=Decimal("180.00"),
            item_subtotal=Decimal("540.00"),
            service_date=date(2026, 7, 31),
        )
    )
    await db_session.flush()

    data = await chef_tools.get_chef_daily_batch_checklist(
        db_session,
        chef_phone=CHEF,
        service_date="2026-07-31",
        meal_window="LUNCH",
    )
    assert data["total_orders"] == 1
    assert data["portions_to_cook"]["Paneer Butter Masala"] == 3


async def test_mark_orders_packed_ready_tool_with_optional_box_count(db_session):
    await _seed_chef_and_menu(db_session)

    db_session.add(
        CustomerProfile(
            customer_phone=CUSTOMER,
            name="Dinesh",
            delivery_address="Flat 301, My Home Bhooja",
            is_registered=True,
        )
    )
    await db_session.flush()

    order = CustomerOrder(
        order_id="ord_chk_002",
        customer_phone=CUSTOMER,
        chef_phone=CHEF,
        kitchen_name="Ramesh Kitchen",
        meal_window="LUNCH",
        service_date=date(2026, 7, 31),
        status="COOKING",
    )
    db_session.add(order)
    await db_session.flush()

    # 1. Mark packed without specifying box count (box_count=None)
    readiness1 = await chef_tools.mark_orders_packed_ready(
        db_session,
        chef_phone=CHEF,
        order_id="ord_chk_002",
        box_count=None,
    )
    assert readiness1.status == "PACKED_READY"
    assert readiness1.box_count is None

    # 2. Test Guard 2 Assertion: Wrong chef ownership must raise AssertionError
    with pytest.raises(AssertionError):
        await chef_tools.mark_orders_packed_ready(
            db_session,
            chef_phone="9111111111",  # Wrong chef
            order_id="ord_chk_002",
        )


async def test_get_chef_earnings_summary_tool(db_session):
    await _seed_chef_and_menu(db_session)

    db_session.add(
        CustomerProfile(
            customer_phone=CUSTOMER,
            name="Dinesh",
            delivery_address="Flat 301, My Home Bhooja",
            is_registered=True,
        )
    )
    await db_session.flush()

    # Seed order with cart_subtotal = 540.00
    order = CustomerOrder(
        order_id="ord_chk_earn_001",
        customer_phone=CUSTOMER,
        chef_phone=CHEF,
        kitchen_name="Ramesh Kitchen",
        meal_window="LUNCH",
        service_date=date(2026, 7, 31),
        status="DELIVERED",
        cart_subtotal=Decimal("540.00"),
        total_amount=Decimal("570.00"),
    )
    db_session.add(order)
    await db_session.flush()

    summary = await chef_tools.get_chef_earnings_summary(
        db_session,
        chef_phone=CHEF,
        start_date="2026-07-01",
        end_date="2026-07-31",
    )
    assert summary["total_orders"] == 1
    assert summary["delivered_orders"] == 1
    assert summary["total_revenue"] == 540.0

    # Test Guard 2 Assertion: start_date > end_date raises AssertionError
    with pytest.raises(AssertionError):
        await chef_tools.get_chef_earnings_summary(
            db_session,
            chef_phone=CHEF,
            start_date="2026-08-01",
            end_date="2026-07-01",
        )
