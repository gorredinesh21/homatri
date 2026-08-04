"""Tests for Master-domain tools (Flow 4 gateway): mint_payment_link + process_payment_webhook."""

import pytest
from datetime import datetime
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chef import ChefMenuItem, ChefProfile
from app.models.customer import CustomerOrder, CustomerPayment, CustomerProfile
from app.tools.customer_tools import _create_order
from app.tools.master_tools import _mint_payment_link, _process_payment_webhook

BEFORE_LUNCH = datetime(2026, 8, 4, 9, 0)


async def _seed_pending_order(session, cust="7000000070", chef="9876500070"):
    session.add(CustomerProfile(customer_phone=cust, name="C", delivery_address="X",
                                latitude=Decimal("19.12"), longitude=Decimal("73.00"), is_registered=True))
    session.add(ChefProfile(chef_phone=chef, kitchen_name="K", chef_name="Chef", address="Ghansoli",
                            latitude=Decimal("19.12"), longitude=Decimal("73.00"), dietary_type="VEG"))
    session.add(ChefMenuItem(chef_phone=chef, dish_name="Thali", unit_price=Decimal("120.00"),
                             meal_type="LUNCH", dietary_tag="VEG", spice_level="MEDIUM", is_available=True))
    await session.flush()
    res = await _create_order(session, customer_phone=cust, kitchen=chef,
                              items=[{"dish_name": "Thali", "quantity": 2}], now=BEFORE_LUNCH)
    return res["order_id"]


@pytest.mark.asyncio
async def test_mint_payment_link_creates_pending(db_session: AsyncSession):
    order_id = await _seed_pending_order(db_session)
    res = await _mint_payment_link(db_session, order_id=order_id)
    assert res["status"] == "MINTED"
    assert res["link"]
    pay = await db_session.get(CustomerPayment, res["payment_id"])
    assert pay.status == "PENDING"


@pytest.mark.asyncio
async def test_mint_payment_link_reuses_pending(db_session: AsyncSession):
    order_id = await _seed_pending_order(db_session)
    a = await _mint_payment_link(db_session, order_id=order_id)
    b = await _mint_payment_link(db_session, order_id=order_id)   # re-mint
    assert a["payment_id"] == b["payment_id"]                    # no duplicate payment


@pytest.mark.asyncio
async def test_mint_payment_link_order_not_found(db_session: AsyncSession):
    res = await _mint_payment_link(db_session, order_id="ord_nope")
    assert res["status"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_process_webhook_marks_paid_and_confirms(db_session: AsyncSession):
    order_id = await _seed_pending_order(db_session)
    mint = await _mint_payment_link(db_session, order_id=order_id)
    res = await _process_payment_webhook(db_session, payment_id=mint["payment_id"], transaction_id="txn_1")
    assert res["status"] == "PAID"
    order = await db_session.get(CustomerOrder, order_id)
    assert order.status == "CONFIRMED"


@pytest.mark.asyncio
async def test_process_webhook_idempotent(db_session: AsyncSession):
    order_id = await _seed_pending_order(db_session)
    mint = await _mint_payment_link(db_session, order_id=order_id)
    await _process_payment_webhook(db_session, payment_id=mint["payment_id"], transaction_id="txn_1")
    again = await _process_payment_webhook(db_session, payment_id=mint["payment_id"], transaction_id="txn_1")
    assert again["status"] == "ALREADY_PAID"     # repeat callback is a no-op
