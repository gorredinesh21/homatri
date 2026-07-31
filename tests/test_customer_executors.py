"""Category 2 & Delegated Executors — Customer executor integration tests (runs against PostgreSQL)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import pytest

from app.executors import customer as cust_exec
from app.models.chef import ChefMenuItem, ChefProfile

from app.models.customer import CustomerOrder, CustomerPayment, CustomerProfile, CustomerReview

CUSTOMER = "9111111111"
CHEF = "9876543210"
SERVICE_DATE = date(2026, 7, 31)


async def _seed_customer_and_chef(s) -> None:
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
            menu_item_id="itm_paneer01",
            chef_phone=CHEF,
            dish_name="Paneer Butter Masala",
            unit_price=Decimal("180.00"),
            meal_type="LUNCH",
        )
    )
    s.add(
        CustomerProfile(
            customer_phone=CUSTOMER,
            name="Dinesh",
            delivery_address="Flat 301, My Home Bhooja",
            apartment_name="My Home Bhooja",
            is_registered=True,
        )
    )
    await s.flush()



async def test_customer_registration_and_location_upsert(db_session):
    profile = await cust_exec.execute_customer_registration_and_location(
        db_session,
        customer_phone="9222222222",
        name="Anish",
        delivery_address="Tower A, Flat 101",
        apartment_name="Jayabheri Silicon County",
        latitude=17.4455,
        longitude=78.3810,
    )
    assert profile.is_registered is True
    assert profile.apartment_name == "Jayabheri Silicon County"
    assert profile.latitude == Decimal("17.4455")


async def test_order_initialization_and_item_addition(db_session):
    await _seed_customer_and_chef(db_session)

    # 1. Initialize order header
    order = await cust_exec.execute_customer_order_initialization(
        db_session,
        customer_phone=CUSTOMER,
        chef_phone=CHEF,
        kitchen_name="Ramesh Kitchen",
        service_date=SERVICE_DATE,
        meal_window="LUNCH",
    )
    assert order.order_id.startswith("ord_")
    assert order.status == "PENDING_PAYMENT"

    # 2. Add line item to order
    item = await cust_exec.execute_add_item_to_order(
        db_session,
        order_id=order.order_id,
        menu_item_id="itm_paneer01",
        dish_name="Paneer Butter Masala",
        quantity=2,
        unit_price=Decimal("180.00"),
    )
    assert item.item_subtotal == Decimal("360.00")
    assert order.cart_subtotal == Decimal("360.00")
    assert order.total_amount == Decimal("390.00")  # 360 + 30 delivery fee


async def test_dw1_order_status_transitions_and_assertions(db_session):
    await _seed_customer_and_chef(db_session)
    order = await cust_exec.execute_customer_order_initialization(
        db_session,
        customer_phone=CUSTOMER,
        chef_phone=CHEF,
        kitchen_name="Ramesh Kitchen",
        service_date=SERVICE_DATE,
    )

    # Valid step-by-step lifecycle transitions
    o1 = await cust_exec.execute_order_status_transition(db_session, order_id=order.order_id, target_status="CONFIRMED")
    assert o1.status == "CONFIRMED"

    o2 = await cust_exec.execute_order_status_transition(db_session, order_id=order.order_id, target_status="BATCHED")
    assert o2.status == "BATCHED"

    o3 = await cust_exec.execute_order_status_transition(db_session, order_id=order.order_id, target_status="COOKING")
    assert o3.status == "COOKING"

    o4 = await cust_exec.execute_order_status_transition(db_session, order_id=order.order_id, target_status="PACKED")
    assert o4.status == "PACKED"

    o5 = await cust_exec.execute_order_status_transition(db_session, order_id=order.order_id, target_status="PICKED_UP")
    assert o5.status == "PICKED_UP"

    o6 = await cust_exec.execute_order_status_transition(db_session, order_id=order.order_id, target_status="DELIVERED")
    assert o6.status == "DELIVERED"

    # Invalid illegal transition attempt (DELIVERED -> COOKING) must raise AssertionError
    with pytest.raises(AssertionError):
        await cust_exec.execute_order_status_transition(db_session, order_id=order.order_id, target_status="COOKING")


async def test_dw2_payment_status_update_cascades_to_order_confirmed(db_session):
    await _seed_customer_and_chef(db_session)
    order = await cust_exec.execute_customer_order_initialization(
        db_session,
        customer_phone=CUSTOMER,
        chef_phone=CHEF,
        kitchen_name="Ramesh Kitchen",
        service_date=SERVICE_DATE,
    )

    payment = await cust_exec.execute_payment_record_creation(
        db_session,
        order_id=order.order_id,
        amount_due=Decimal("210.00"),
        payment_method="UPI",
        gateway_order_id="order_rzp_999",
    )
    assert payment.payment_id.startswith("pay_")
    assert payment.status == "PENDING"

    # Execute DW2 Payment Update to PAID -> Must automatically trigger DW1 to confirm order!
    updated_payment = await cust_exec.execute_payment_status_update(
        db_session,
        payment_id=payment.payment_id,
        target_status="PAID",
        gateway_transaction_id="txn_rzp_12345",
    )
    assert updated_payment.status == "PAID"
    assert updated_payment.amount_paid == Decimal("210.00")
    assert order.status == "CONFIRMED"  # Cascaded via DW1!


async def test_order_review_submission(db_session):
    await _seed_customer_and_chef(db_session)
    order = await cust_exec.execute_customer_order_initialization(
        db_session,
        customer_phone=CUSTOMER,
        chef_phone=CHEF,
        kitchen_name="Ramesh Kitchen",
        service_date=SERVICE_DATE,
    )
    # Fast forward order to DELIVERED
    await cust_exec.execute_order_status_transition(db_session, order_id=order.order_id, target_status="CONFIRMED")
    await cust_exec.execute_order_status_transition(db_session, order_id=order.order_id, target_status="BATCHED")
    await cust_exec.execute_order_status_transition(db_session, order_id=order.order_id, target_status="COOKING")
    await cust_exec.execute_order_status_transition(db_session, order_id=order.order_id, target_status="PACKED")
    await cust_exec.execute_order_status_transition(db_session, order_id=order.order_id, target_status="PICKED_UP")
    await cust_exec.execute_order_status_transition(db_session, order_id=order.order_id, target_status="DELIVERED")

    review = await cust_exec.execute_submit_order_review(
        db_session,
        order_id=order.order_id,
        customer_phone=CUSTOMER,
        chef_phone=CHEF,
        chef_rating=5,
        review_text="Delicious home-cooked paneer!",
    )
    assert review.review_id.startswith("rev_")
    assert review.chef_rating == 5
