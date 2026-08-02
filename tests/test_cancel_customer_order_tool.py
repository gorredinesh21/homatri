"""Integration test suite for Customer Tool 9: cancel_customer_order_tool."""

import pytest
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chef import ChefProfile
from app.models.customer import CustomerOrder, CustomerPayment, CustomerProfile
from app.models.system import SystemAgentLog, SystemMealWindow, SystemOutboundQueue
from app.tools.customer_tools import cancel_customer_order


@pytest.mark.asyncio
async def test_cancel_customer_order_with_paid_refund(db_session: AsyncSession):
    session = db_session

    # 1. Seed Chef, Customer, MealWindow, Order, and PAID Payment
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

    win = SystemMealWindow(
        window_id="win_cancel_01",
        service_date=date(2026, 8, 2),
        meal_type="LUNCH",
        cutoff_at=datetime.now(),
        status="OPEN",
    )
    session.add(win)
    await session.flush()

    order = CustomerOrder(
        order_id="ord_cancel_test_01",
        customer_phone=cust.customer_phone,
        chef_phone=chef.chef_phone,
        kitchen_name=chef.kitchen_name,
        meal_window="LUNCH",
        service_date=date(2026, 8, 2),
        status="CONFIRMED",
        cart_subtotal=Decimal("250.00"),
        delivery_fee=Decimal("30.00"),
        total_amount=Decimal("280.00"),
    )
    session.add(order)
    await session.flush()

    payment = CustomerPayment(
        payment_id="pay_cancel_test_01",
        order_id=order.order_id,
        customer_phone=cust.customer_phone,
        payment_type="INITIAL",
        amount_due=Decimal("280.00"),
        amount_paid=Decimal("280.00"),
        gateway="MOCK_GATEWAY",
        status="PAID",
    )
    session.add(payment)
    await session.flush()

    # 2. Execute Cancel Order
    res = await cancel_customer_order(
        session,
        order_id=order.order_id,
        customer_phone=cust.customer_phone,
        reason="Emergency travel plans",
    )

    # 3. Verify Order & Payment Status Changes
    assert res["status"] == "CANCELLED"
    assert res["refund_status"] == "REFUNDED"
    assert res["refund_amount"] == Decimal("280.00")

    await session.refresh(order)
    assert order.status == "CANCELLED"
    assert order.cancellation_reason == "Emergency travel plans"

    await session.refresh(payment)
    assert payment.status == "REFUNDED"

    # 4. Verify System Audit Log & Outbound WhatsApp Notice
    stmt_log = select(SystemAgentLog).where(SystemAgentLog.event_type == "CUSTOMER_ORDER_CANCELLED")
    audit = (await session.execute(stmt_log)).scalar_one_or_none()
    assert audit is not None

    stmt_out = select(SystemOutboundQueue).where(SystemOutboundQueue.recipient_phone == cust.customer_phone)
    outbound = (await session.execute(stmt_out)).scalar_one_or_none()
    assert outbound is not None
    assert "ORDER CANCELLED" in outbound.message_text


@pytest.mark.asyncio
async def test_cancel_customer_order_locked_window_fails(db_session: AsyncSession):
    session = db_session

    chef = ChefProfile(
        chef_phone="9876543211",
        kitchen_name="Indravati Tiffins 2",
        chef_name="Chef Sunita 2",
        address="Sector 4, Ghansoli",
        latitude=Decimal("19.1190086"),
        longitude=Decimal("72.9934054"),
    )
    cust = CustomerProfile(
        customer_phone="9123456788",
        name="Ramesh Test 2",
        delivery_address="Indravati CHS, Ghansoli",
        latitude=Decimal("19.1214684"),
        longitude=Decimal("73.0036295"),
    )
    session.add_all([chef, cust])
    await session.flush()

    win = SystemMealWindow(
        window_id="win_cancel_locked_02",
        service_date=date(2026, 8, 3),
        meal_type="DINNER",
        cutoff_at=datetime.now(),
        status="LOCKED_PROCESSING",
    )
    session.add(win)
    await session.flush()

    order = CustomerOrder(
        order_id="ord_cancel_test_02",
        customer_phone=cust.customer_phone,
        chef_phone=chef.chef_phone,
        kitchen_name=chef.kitchen_name,
        meal_window="DINNER",
        service_date=date(2026, 8, 3),
        status="CONFIRMED",
        cart_subtotal=Decimal("200.00"),
        delivery_fee=Decimal("30.00"),
        total_amount=Decimal("230.00"),
    )
    session.add(order)
    await session.flush()

    with pytest.raises(AssertionError, match="Cannot cancel order after meal window cutoff lock"):
        await cancel_customer_order(
            session,
            order_id=order.order_id,
            customer_phone=cust.customer_phone,
            reason="Too late cancellation",
        )
