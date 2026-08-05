"""Tests for cancel_order + respond_to_cancellation (direct + chef-approved)."""

import pytest
from datetime import date
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chef import ChefProfile
from app.models.customer import CustomerOrder, CustomerPayment, CustomerProfile
from app.models.system import SystemOutboundQueue
from app.tools.cancellation import (
    _cancel_order,
    _cancellations,
    _respond_to_cancellation,
)

SD = date(2026, 8, 4)


async def _seed(session, cust, chef, order_id, status, paid=False):
    session.add(CustomerProfile(customer_phone=cust, name="C", delivery_address="X",
                                latitude=Decimal("19.12"), longitude=Decimal("73.00"), is_registered=True))
    session.add(ChefProfile(chef_phone=chef, kitchen_name="K", chef_name="Chef", address="G",
                            latitude=Decimal("19.12"), longitude=Decimal("73.00"), dietary_type="VEG"))
    session.add(CustomerOrder(order_id=order_id, customer_phone=cust, chef_phone=chef, kitchen_name="K",
                              service_date=SD, meal_window="LUNCH", status=status,
                              cart_subtotal=Decimal("100"), delivery_fee=Decimal("20"), total_amount=Decimal("120")))
    if paid:
        session.add(CustomerPayment(order_id=order_id, customer_phone=cust, payment_type="INITIAL",
                                    amount_due=Decimal("120"), amount_paid=Decimal("120"), gateway="RAZORPAY",
                                    status="PAID"))
    await session.flush()


@pytest.mark.asyncio
async def test_cancel_precook_paid_flags_refund(db_session: AsyncSession):
    _cancellations.clear()
    await _seed(db_session, "7000000060", "9876500060", "ord_c1", "CONFIRMED", paid=True)
    res = await _cancel_order(db_session, customer_phone="7000000060", reason="changed mind")
    assert res["status"] == "CANCELLED"
    assert res["refund_due"] == 120.0
    assert (await db_session.get(CustomerOrder, "ord_c1")).status == "CANCELLED"


@pytest.mark.asyncio
async def test_cancel_precook_unpaid_no_refund(db_session: AsyncSession):
    _cancellations.clear()
    await _seed(db_session, "7000000061", "9876500061", "ord_c2", "PENDING_PAYMENT", paid=False)
    res = await _cancel_order(db_session, customer_phone="7000000061")
    assert res["status"] == "CANCELLED"
    assert res["refund_due"] == 0.0


@pytest.mark.asyncio
async def test_cancel_too_late_when_packed(db_session: AsyncSession):
    _cancellations.clear()
    await _seed(db_session, "7000000062", "9876500062", "ord_c3", "PACKED")
    res = await _cancel_order(db_session, customer_phone="7000000062")
    assert res["status"] == "TOO_LATE"
    assert (await db_session.get(CustomerOrder, "ord_c3")).status == "PACKED"   # unchanged


@pytest.mark.asyncio
async def test_cancel_cooking_asks_chef(db_session: AsyncSession):
    _cancellations.clear()
    await _seed(db_session, "7000000063", "9876500063", "ord_c4", "COOKING")
    res = await _cancel_order(db_session, customer_phone="7000000063", reason="emergency")
    assert res["status"] == "AWAITING_CHEF"
    assert _cancellations["7000000063"]["status"] == "WAITING_CHEF"
    # chef got a request
    outs = (await db_session.execute(select(SystemOutboundQueue).where(
        SystemOutboundQueue.recipient_role == "CHEF"))).scalars().all()
    assert any("Cancellation request" in o.message_text for o in outs)


@pytest.mark.asyncio
async def test_cooking_chef_approves(db_session: AsyncSession):
    _cancellations.clear()
    await _seed(db_session, "7000000064", "9876500064", "ord_c5", "COOKING", paid=True)
    await _cancel_order(db_session, customer_phone="7000000064")
    res = await _respond_to_cancellation(db_session, chef_phone="9876500064", decision="approve")
    assert res["status"] == "APPROVED"
    assert (await db_session.get(CustomerOrder, "ord_c5")).status == "CANCELLED"   # COOKING -> CANCELLED allowed


@pytest.mark.asyncio
async def test_cooking_chef_denies(db_session: AsyncSession):
    _cancellations.clear()
    await _seed(db_session, "7000000065", "9876500065", "ord_c6", "COOKING")
    await _cancel_order(db_session, customer_phone="7000000065")
    res = await _respond_to_cancellation(db_session, chef_phone="9876500065", decision="deny")
    assert res["status"] == "DENIED"
    assert (await db_session.get(CustomerOrder, "ord_c6")).status == "COOKING"   # not cancelled


@pytest.mark.asyncio
async def test_cancel_no_order(db_session: AsyncSession):
    _cancellations.clear()
    res = await _cancel_order(db_session, customer_phone="7000009999")
    assert res["status"] == "NO_ORDER"
