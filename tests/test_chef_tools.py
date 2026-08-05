"""Tests for chef-domain tools (Flow 6, Part A)."""

import pytest
from datetime import date
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chef import ChefDailyInventory, ChefMenuItem, ChefOrderReadiness, ChefProfile
from app.models.customer import CustomerOrder, CustomerOrderItem, CustomerProfile
from app.tools.chef_tools import (
    _get_chef_batch,
    _get_chef_profile,
    _mark_order_ready,
    _set_daily_capacity,
    _toggle_dish_stock,
)

SERVICE_DATE = date(2026, 8, 4)
CHEF = "9876500100"


async def _seed_chef(session, phone=CHEF, dish="Paneer Tikka Tiffin", meal="LUNCH"):
    session.add(ChefProfile(chef_phone=phone, kitchen_name="Test Kitchen", chef_name="Chef T",
                            address="Ghansoli", latitude=Decimal("19.12"), longitude=Decimal("73.00"),
                            dietary_type="VEG"))
    item = ChefMenuItem(chef_phone=phone, dish_name=dish, unit_price=Decimal("180.00"), meal_type=meal,
                        dietary_tag="VEG", spice_level="MEDIUM", is_available=True)
    session.add(item)
    await session.flush()
    return item


async def _seed_batched_order(session, chef=CHEF, cust="7000000100", order_id="ord_chef_1",
                              dish="Paneer Tikka Tiffin"):
    session.add(CustomerProfile(customer_phone=cust, name="Ravi", delivery_address="Flat 1 Ghansoli",
                                latitude=Decimal("19.12"), longitude=Decimal("73.00"), is_registered=True))
    session.add(CustomerOrder(order_id=order_id, customer_phone=cust, chef_phone=chef, kitchen_name="Test Kitchen",
                              service_date=SERVICE_DATE, meal_window="LUNCH", status="BATCHED",
                              cart_subtotal=Decimal("360.00"), delivery_fee=Decimal("20.00"),
                              total_amount=Decimal("380.00")))
    session.add(CustomerOrderItem(order_id=order_id, menu_item_id="itm_x", chef_phone=chef, dish_name=dish,
                                  quantity=2, unit_price=Decimal("180.00"), item_subtotal=Decimal("360.00"),
                                  service_date=SERVICE_DATE))
    await session.flush()
    return order_id


@pytest.mark.asyncio
async def test_get_chef_profile(db_session: AsyncSession):
    await _seed_chef(db_session)
    assert (await _get_chef_profile(db_session, chef_phone=CHEF))["status"] == "FOUND"
    assert (await _get_chef_profile(db_session, chef_phone="9999999999"))["status"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_get_chef_batch(db_session: AsyncSession):
    await _seed_chef(db_session)
    await _seed_batched_order(db_session)
    res = await _get_chef_batch(db_session, chef_phone=CHEF)
    assert res["status"] == "OK"
    assert len(res["orders"]) == 1
    assert res["summary"][0] == {"dish": "Paneer Tikka Tiffin", "total_qty": 2}


@pytest.mark.asyncio
async def test_get_chef_batch_none(db_session: AsyncSession):
    await _seed_chef(db_session)
    assert (await _get_chef_batch(db_session, chef_phone=CHEF))["status"] == "NO_BATCH"


@pytest.mark.asyncio
async def test_toggle_dish_stock(db_session: AsyncSession):
    item = await _seed_chef(db_session)
    res = await _toggle_dish_stock(db_session, chef_phone=CHEF, dish="paneer tikka", is_available=False)
    assert res["status"] == "UPDATED"
    assert (await db_session.get(ChefMenuItem, item.menu_item_id)).is_available is False


@pytest.mark.asyncio
async def test_toggle_dish_stock_not_found(db_session: AsyncSession):
    await _seed_chef(db_session)
    res = await _toggle_dish_stock(db_session, chef_phone=CHEF, dish="pizza margherita", is_available=False)
    assert res["status"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_set_daily_capacity(db_session: AsyncSession):
    await _seed_chef(db_session)
    res = await _set_daily_capacity(db_session, chef_phone=CHEF, dish="Paneer Tikka Tiffin",
                                    service_date=SERVICE_DATE, window="LUNCH", max_capacity=15)
    assert res["status"] == "SET"
    row = (await db_session.execute(select(ChefDailyInventory))).scalars().first()
    assert row.max_capacity == 15


@pytest.mark.asyncio
async def test_set_daily_capacity_negative(db_session: AsyncSession):
    await _seed_chef(db_session)
    res = await _set_daily_capacity(db_session, chef_phone=CHEF, dish="Paneer Tikka Tiffin",
                                    service_date=SERVICE_DATE, window="LUNCH", max_capacity=-5)
    assert res["status"] == "INVALID"


@pytest.mark.asyncio
async def test_mark_order_ready(db_session: AsyncSession):
    await _seed_chef(db_session)
    order_id = await _seed_batched_order(db_session)
    res = await _mark_order_ready(db_session, chef_phone=CHEF, order_id=order_id, box_count=2)
    assert res["status"] == "READY"
    assert (await db_session.get(CustomerOrder, order_id)).status == "PACKED"     # BATCHED -> COOKING -> PACKED
    assert (await db_session.execute(select(ChefOrderReadiness))).scalars().first() is not None


@pytest.mark.asyncio
async def test_mark_order_ready_not_yours(db_session: AsyncSession):
    await _seed_chef(db_session)
    order_id = await _seed_batched_order(db_session)
    res = await _mark_order_ready(db_session, chef_phone="9999999999", order_id=order_id)
    assert res["status"] == "NOT_YOURS"


@pytest.mark.asyncio
async def test_mark_order_ready_idempotent(db_session: AsyncSession):
    await _seed_chef(db_session)
    order_id = await _seed_batched_order(db_session)
    await _mark_order_ready(db_session, chef_phone=CHEF, order_id=order_id)
    again = await _mark_order_ready(db_session, chef_phone=CHEF, order_id=order_id)
    assert again["status"] == "ALREADY_READY"
