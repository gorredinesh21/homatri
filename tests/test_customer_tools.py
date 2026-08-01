"""Category 2 — Customer LLM tools integration tests (runs against PostgreSQL)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import pytest

from app.core.exceptions import LocationInterrupt
from app.models.chef import ChefMenuItem, ChefProfile
from app.models.customer import CustomerOrder, CustomerOrderItem, CustomerProfile
from app.tools import customer_tools

CUSTOMER = "9111111111"
CHEF_1 = "9876543210"
CHEF_2 = "9876543211"


async def test_get_customer_profile_registered_and_unregistered(db_session):
    # 1. Query unregistered phone -> returns None
    unreg = await customer_tools.get_customer_profile(db_session, customer_phone=CUSTOMER)
    assert unreg is None

    # 2. Seed registered customer
    db_session.add(
        CustomerProfile(
            customer_phone=CUSTOMER,
            name="Dinesh",
            delivery_address="Flat 301, My Home Bhooja",
            apartment_name="My Home Bhooja",
            latitude=Decimal("17.44500000"),
            longitude=Decimal("78.38000000"),
            is_registered=True,
        )
    )
    await db_session.flush()

    # 3. Query registered phone -> returns profile data
    reg = await customer_tools.get_customer_profile(db_session, customer_phone=CUSTOMER)
    assert reg is not None
    assert reg["name"] == "Dinesh"
    assert reg["is_registered"] is True
    assert reg["latitude"] == 17.445


async def test_register_customer_profile_with_direct_lat_lng(db_session):
    # Direct registration (name, address, lat, lng provided together)
    p_atomic = await customer_tools.register_customer_profile(
        db_session,
        customer_phone="9333333333",
        name="Atomic Customer",
        delivery_address="Flat 101, Golf Edge, Gachibowli",
        latitude=17.4400,
        longitude=78.3700,
    )
    assert p_atomic.is_registered is True
    assert float(p_atomic.latitude) == 17.4400


async def test_register_customer_profile_invokes_customer_agent(db_session):
    # When lat/lng are missing, register_customer_profile invokes Customer Agent inside function,
    # which enqueues WhatsApp prompt and raises LocationInterrupt to wait for location pin.
    with pytest.raises(LocationInterrupt) as exc_info:
        await customer_tools.register_customer_profile(
            db_session,
            customer_phone="9444444444",
            name="Interrupt Customer",
            delivery_address="Flat 202, My Home Krishe",
        )

    interrupt_payload = exc_info.value.payload
    assert interrupt_payload["interrupt_type"] == "AWAIT_LOCATION_PIN"
    assert interrupt_payload["customer_phone"] == "9444444444"


async def test_find_nearby_home_kitchens_tool(db_session):
    # 1. Seed Customer at (17.4450, 78.3800)
    db_session.add(
        CustomerProfile(
            customer_phone=CUSTOMER,
            name="Dinesh",
            delivery_address="Flat 301, My Home Bhooja",
            latitude=Decimal("17.44500000"),
            longitude=Decimal("78.38000000"),
            is_registered=True,
        )
    )
    await db_session.flush()

    # 2. Seed Chef 1 nearby at (17.4480, 78.3810) ~0.35 km
    db_session.add(
        ChefProfile(
            chef_phone=CHEF_1,
            kitchen_name="Ramesh Kitchen",
            chef_name="Ramesh",
            address="Flat 402, Hitech City",
            latitude=Decimal("17.44800000"),
            longitude=Decimal("78.38100000"),
            active_status=True,
        )
    )
    # Seed Chef 2 farther away at (17.5000, 78.4000) ~6.5 km
    db_session.add(
        ChefProfile(
            chef_phone=CHEF_2,
            kitchen_name="Sita Kitchen",
            chef_name="Sita",
            address="Kukatpally",
            latitude=Decimal("17.50000000"),
            longitude=Decimal("78.40000000"),
            active_status=True,
        )
    )
    await db_session.flush()

    # 3. Seed Menu Items for Chef 1 & Chef 2
    db_session.add(
        ChefMenuItem(
            menu_item_id="itm_p1",
            chef_phone=CHEF_1,
            dish_name="Paneer Butter Masala",
            unit_price=Decimal("180.00"),
            meal_type="LUNCH",
            is_available=True,
        )
    )
    db_session.add(
        ChefMenuItem(
            menu_item_id="itm_s1",
            chef_phone=CHEF_2,
            dish_name="Special Veg Thali",
            unit_price=Decimal("150.00"),
            meal_type="LUNCH",
            is_available=True,
        )
    )
    await db_session.flush()

    # 4. Query nearby kitchens for LUNCH
    kitchens = await customer_tools.find_nearby_home_kitchens(
        db_session,
        customer_phone=CUSTOMER,
        meal_window="LUNCH",
    )
    assert len(kitchens) == 2
    assert kitchens[0]["kitchen_name"] == "Ramesh Kitchen"  # Closest first
    assert kitchens[0]["distance_km"] < kitchens[1]["distance_km"]


async def test_view_chef_menu_tool(db_session):
    db_session.add(
        ChefProfile(
            chef_phone=CHEF_1,
            kitchen_name="Ramesh Kitchen",
            chef_name="Ramesh",
            address="Flat 402, Hitech City",
            latitude=Decimal("17.44800000"),
            longitude=Decimal("78.38100000"),
            active_status=True,
        )
    )
    await db_session.flush()

    db_session.add(
        ChefMenuItem(
            menu_item_id="itm_view_01",
            chef_phone=CHEF_1,
            dish_name="Paneer Butter Masala",
            description="Fresh cottage cheese curry",
            unit_price=Decimal("180.00"),
            meal_type="LUNCH",
            dietary_tag="VEG",
            spice_level="MEDIUM",
            is_available=True,
        )
    )
    await db_session.flush()

    menu_data = await customer_tools.view_chef_menu(
        db_session,
        chef_phone=CHEF_1,
        meal_window="LUNCH",
    )
    assert menu_data["kitchen_name"] == "Ramesh Kitchen"
    assert len(menu_data["dishes"]) == 1
    assert menu_data["dishes"][0]["dish_name"] == "Paneer Butter Masala"

    # Guard 2 Assertion: Invalid meal window raises AssertionError
    with pytest.raises(AssertionError):
        await customer_tools.view_chef_menu(
            db_session,
            chef_phone=CHEF_1,
            meal_window="BREAKFAST",
        )


async def test_add_item_to_order_tool_success_and_assertions(db_session):
    # Seed registered customer
    db_session.add(
        CustomerProfile(
            customer_phone=CUSTOMER,
            name="Dinesh",
            delivery_address="Flat 301, My Home Bhooja",
            is_registered=True,
        )
    )
    # Seed active chef & menu item
    db_session.add(
        ChefProfile(
            chef_phone=CHEF_1,
            kitchen_name="Ramesh Kitchen",
            chef_name="Ramesh",
            address="Flat 402, Hitech City",
            latitude=Decimal("17.44800000"),
            longitude=Decimal("78.38100000"),
            active_status=True,
        )
    )
    await db_session.flush()

    db_session.add(
        ChefMenuItem(
            menu_item_id="itm_add_01",
            chef_phone=CHEF_1,
            dish_name="Paneer Butter Masala",
            unit_price=Decimal("180.00"),
            meal_type="LUNCH",
            is_available=True,
        )
    )
    await db_session.flush()

    # 1. Add Paneer Butter Masala x 2 (Subtotal = 360.00, Total = 390.00)
    order = await customer_tools.add_item_to_order(
        db_session,
        customer_phone=CUSTOMER,
        chef_phone=CHEF_1,
        menu_item_id="itm_add_01",
        quantity=2,
        service_date="2026-08-01",
        meal_window="LUNCH",
    )
    assert order.cart_subtotal == Decimal("360.00")
    assert order.total_amount == Decimal("390.00")
    assert order.status == "PENDING_PAYMENT"

    # 2. Guard 2 Assertion: Out of stock item raises AssertionError
    db_session.add(
        ChefMenuItem(
            menu_item_id="itm_add_out_of_stock",
            chef_phone=CHEF_1,
            dish_name="Special Thali",
            unit_price=Decimal("200.00"),
            meal_type="LUNCH",
            is_available=False,  # Out of stock
        )
    )
    await db_session.flush()

    with pytest.raises(AssertionError):
        await customer_tools.add_item_to_order(
            db_session,
            customer_phone=CUSTOMER,
            chef_phone=CHEF_1,
            menu_item_id="itm_add_out_of_stock",
            quantity=1,
            service_date="2026-08-01",
            meal_window="LUNCH",
        )


async def test_get_order_history_tool(db_session):
    db_session.add(
        CustomerProfile(
            customer_phone=CUSTOMER,
            name="Dinesh",
            delivery_address="Flat 301, My Home Bhooja",
            is_registered=True,
        )
    )
    db_session.add(
        ChefProfile(
            chef_phone=CHEF_1,
            kitchen_name="Ramesh Kitchen",
            chef_name="Ramesh",
            address="Flat 402, Hitech City",
            latitude=Decimal("17.44800000"),
            longitude=Decimal("78.38100000"),
            active_status=True,
        )
    )
    await db_session.flush()

    db_session.add(
        ChefMenuItem(
            menu_item_id="itm_hist_01",
            chef_phone=CHEF_1,
            dish_name="Paneer Butter Masala",
            unit_price=Decimal("180.00"),
            meal_type="LUNCH",
            is_available=True,
        )
    )
    await db_session.flush()

    # Seed past DELIVERED order
    order = CustomerOrder(
        order_id="ord_hist_001",
        customer_phone=CUSTOMER,
        chef_phone=CHEF_1,
        kitchen_name="Ramesh Kitchen",
        meal_window="LUNCH",
        service_date=date(2026, 7, 30),
        status="DELIVERED",
        cart_subtotal=Decimal("360.00"),
        total_amount=Decimal("390.00"),
    )
    db_session.add(order)
    await db_session.flush()

    db_session.add(
        CustomerOrderItem(
            item_id="ori_hist_001",
            order_id="ord_hist_001",
            menu_item_id="itm_hist_01",
            chef_phone=CHEF_1,
            dish_name="Paneer Butter Masala",
            quantity=2,
            unit_price=Decimal("180.00"),
            item_subtotal=Decimal("360.00"),
            service_date=date(2026, 7, 30),
        )
    )
    await db_session.flush()

    history = await customer_tools.get_order_history(
        db_session,
        customer_phone=CUSTOMER,
        limit=5,
    )
    assert len(history) == 1
    assert history[0]["order_id"] == "ord_hist_001"
    assert history[0]["items"][0]["dish_name"] == "Paneer Butter Masala"

    # Guard 2 Assertion: limit > 20 raises AssertionError
    with pytest.raises(AssertionError):
        await customer_tools.get_order_history(
            db_session,
            customer_phone=CUSTOMER,
            limit=50,
        )


async def test_submit_order_review_tool_success_and_assertions(db_session):
    db_session.add(
        CustomerProfile(
            customer_phone=CUSTOMER,
            name="Dinesh",
            delivery_address="Flat 301, My Home Bhooja",
            is_registered=True,
        )
    )
    db_session.add(
        ChefProfile(
            chef_phone=CHEF_1,
            kitchen_name="Ramesh Kitchen",
            chef_name="Ramesh",
            address="Flat 402, Hitech City",
            latitude=Decimal("17.44800000"),
            longitude=Decimal("78.38100000"),
            active_status=True,
        )
    )
    await db_session.flush()

    # Seed DELIVERED order
    db_session.add(
        CustomerOrder(
            order_id="ord_rev_001",
            customer_phone=CUSTOMER,
            chef_phone=CHEF_1,
            kitchen_name="Ramesh Kitchen",
            meal_window="LUNCH",
            service_date=date(2026, 7, 30),
            status="DELIVERED",
            cart_subtotal=Decimal("360.00"),
            total_amount=Decimal("390.00"),
        )
    )
    # Seed CONFIRMED (non-delivered) order
    db_session.add(
        CustomerOrder(
            order_id="ord_rev_002",
            customer_phone=CUSTOMER,
            chef_phone=CHEF_1,
            kitchen_name="Ramesh Kitchen",
            meal_window="LUNCH",
            service_date=date(2026, 8, 1),
            status="CONFIRMED",  # Not delivered yet!
            cart_subtotal=Decimal("360.00"),
            total_amount=Decimal("390.00"),
        )
    )
    await db_session.flush()

    # 1. Submit review for delivered order -> Success!
    review = await customer_tools.submit_order_review(
        db_session,
        order_id="ord_rev_001",
        customer_phone=CUSTOMER,
        rating=5,
        review_text="Amazing paneer butter masala!",
    )
    assert review.chef_rating == 5
    assert review.review_text == "Amazing paneer butter masala!"

    # 2. Guard 2 Assertion: Reviewing non-delivered order raises AssertionError
    with pytest.raises(AssertionError):
        await customer_tools.submit_order_review(
            db_session,
            order_id="ord_rev_002",
            customer_phone=CUSTOMER,
            rating=4,
        )

    # 3. Guard 2 Assertion: Rating > 5 raises AssertionError
    with pytest.raises(AssertionError):
        await customer_tools.submit_order_review(
            db_session,
            order_id="ord_rev_001",
            customer_phone=CUSTOMER,
            rating=6,
        )
