"""Integration test suite for Customer Tool 8: generate_payment_link_tool."""

import pytest
from datetime import date
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chef import ChefProfile
from app.models.customer import CustomerOrder, CustomerPayment, CustomerProfile
from app.tools.customer_tools import generate_payment_link


@pytest.mark.asyncio
async def test_generate_payment_link_mock_success(db_session: AsyncSession):
    session = db_session

    # 1. Seed Chef, Customer and CustomerOrder
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
    session.add_all([chef, cust])
    await session.flush()

    order = CustomerOrder(
        order_id="ord_pay_link_01",
        customer_phone=cust.customer_phone,
        chef_phone=chef.chef_phone,
        kitchen_name=chef.kitchen_name,
        meal_window="LUNCH",
        service_date=date(2026, 8, 2),
        status="PENDING_PAYMENT",
        cart_subtotal=Decimal("250.00"),
        delivery_fee=Decimal("30.00"),
        total_amount=Decimal("280.00"),
    )
    session.add(order)
    await session.flush()

    # 2. Generate Mock Payment Link
    payment = await generate_payment_link(
        session,
        order_id=order.order_id,
        customer_phone=cust.customer_phone,
        amount_due=Decimal("280.00"),
        payment_type="INITIAL",
        is_mock=True,
    )

    # 3. Verify Payment Record Attributes
    assert payment is not None
    assert payment.order_id == order.order_id
    assert payment.customer_phone == cust.customer_phone
    assert "ord_pay_link_01" in payment.payment_link_url
    assert payment.gateway == "MOCK_GATEWAY"
    assert payment.status == "PENDING"
    assert payment.amount_due == Decimal("280.00")
