"""Tests for Flow 6 Part B — dietary negotiation."""

import pytest
from datetime import date
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chef import ChefProfile
from app.models.customer import CustomerOrder, CustomerProfile
from app.models.system import SystemOutboundQueue
from app.tools.dietary import (
    _apply_counter_accept,
    _negotiations,
    _relay_dietary_request,
    _request_dietary_change,
    _respond_to_dietary_request,
)
from app.tools.pause import clear_pending, get_pending

SD = date(2026, 8, 4)


async def _seed(session, cust, chef, order_id, status="CONFIRMED"):
    session.add(CustomerProfile(customer_phone=cust, name="C", delivery_address="X",
                                latitude=Decimal("19.12"), longitude=Decimal("73.00"), is_registered=True))
    session.add(ChefProfile(chef_phone=chef, kitchen_name="Test Kitchen", chef_name="Chef T", address="G",
                            latitude=Decimal("19.12"), longitude=Decimal("73.00"), dietary_type="VEG"))
    session.add(CustomerOrder(order_id=order_id, customer_phone=cust, chef_phone=chef, kitchen_name="Test Kitchen",
                              service_date=SD, meal_window="LUNCH", status=status,
                              cart_subtotal=Decimal("100.00"), delivery_fee=Decimal("20.00"),
                              total_amount=Decimal("120.00")))
    await session.flush()


async def _out(session, role):
    return (await session.execute(
        select(SystemOutboundQueue).where(SystemOutboundQueue.recipient_role == role))).scalars().all()


@pytest.mark.asyncio
async def test_relay_sends_to_chef(db_session: AsyncSession):
    _negotiations.clear()
    await _seed(db_session, "7000000600", "9876500600", "ord_d1")
    res = await _relay_dietary_request(db_session, order_id="ord_d1", customer_phone="7000000600",
                                       chef_phone="9876500600", note="no garlic")
    assert res["status"] == "SENT_TO_CHEF"
    assert any("no garlic" in o.message_text for o in await _out(db_session, "CHEF"))


@pytest.mark.asyncio
async def test_relay_turn_cap(db_session: AsyncSession):
    res = await _relay_dietary_request(db_session, order_id="x", customer_phone="c",
                                       chef_phone="9876500600", note="n", turn=3)
    assert res["status"] == "KEPT_ORIGINAL"


@pytest.mark.asyncio
async def test_request_awaiting_chef(db_session: AsyncSession):
    _negotiations.clear()
    await _seed(db_session, "7000000601", "9876500601", "ord_d2")
    res = await _request_dietary_change(db_session, customer_phone="7000000601", note="no garlic")
    assert res["status"] == "AWAITING_CHEF"
    assert "7000000601" in _negotiations


@pytest.mark.asyncio
async def test_request_not_modifiable(db_session: AsyncSession):
    _negotiations.clear()
    await _seed(db_session, "7000000602", "9876500602", "ord_d3", status="PENDING_PAYMENT")
    res = await _request_dietary_change(db_session, customer_phone="7000000602", note="no garlic")
    assert res["status"] == "NOT_MODIFIABLE"


@pytest.mark.asyncio
async def test_respond_accept_saves_note(db_session: AsyncSession):
    _negotiations.clear()
    await _seed(db_session, "7000000603", "9876500603", "ord_d4")
    await _request_dietary_change(db_session, customer_phone="7000000603", note="no garlic")
    res = await _respond_to_dietary_request(db_session, chef_phone="9876500603", decision="accept")
    assert res["status"] == "RESOLVED"
    order = await db_session.get(CustomerOrder, "ord_d4")
    assert order.special_instructions == "no garlic"
    assert any("agreed" in o.message_text for o in await _out(db_session, "CUSTOMER"))


@pytest.mark.asyncio
async def test_respond_reject_keeps_original(db_session: AsyncSession):
    _negotiations.clear()
    await _seed(db_session, "7000000604", "9876500604", "ord_d5")
    await _request_dietary_change(db_session, customer_phone="7000000604", note="no garlic")
    res = await _respond_to_dietary_request(db_session, chef_phone="9876500604", decision="reject")
    assert res["status"] == "RESOLVED"
    assert (await db_session.get(CustomerOrder, "ord_d5")).special_instructions is None


@pytest.mark.asyncio
async def test_respond_no_open_request(db_session: AsyncSession):
    _negotiations.clear()
    res = await _respond_to_dietary_request(db_session, chef_phone="9999999999", decision="accept")
    assert res["status"] == "NO_REQUEST"


@pytest.mark.asyncio
async def test_counter_needs_note(db_session: AsyncSession):
    _negotiations.clear()
    await _seed(db_session, "7000000606", "9876500606", "ord_d7")
    await _request_dietary_change(db_session, customer_phone="7000000606", note="no garlic")
    res = await _respond_to_dietary_request(db_session, chef_phone="9876500606", decision="counter")
    assert res["status"] == "NEED_COUNTER"


@pytest.mark.asyncio
async def test_counter_then_customer_accepts(db_session: AsyncSession):
    _negotiations.clear()
    clear_pending("7000000605")
    await _seed(db_session, "7000000605", "9876500605", "ord_d6")
    await _request_dietary_change(db_session, customer_phone="7000000605", note="no garlic")
    res = await _respond_to_dietary_request(db_session, chef_phone="9876500605",
                                            decision="counter", counter_note="less garlic")
    assert res["status"] == "COUNTER_SENT"
    assert get_pending("7000000605") is not None              # customer armed for round 2
    assert _negotiations["7000000605"]["status"] == "WAITING_CUSTOMER"
    # the customer accepts the counter -> note saved
    await _apply_counter_accept(db_session, phone="7000000605")
    assert (await db_session.get(CustomerOrder, "ord_d6")).special_instructions == "less garlic"
    clear_pending("7000000605")
