"""Category 1 — Chef executor tests (run against in-memory SQLite)."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select

from app.db.session import create_all, drop_all, transaction
from app.executors import chef as chef_exec
from app.models.chef import ChefDailyInventory, ChefMenuItem, ChefProfile
from app.models.customer import CustomerOrder, CustomerProfile

CHEF = "9876543210"
ITEM = "itm_test0001"
SERVICE_DATE = date(2026, 7, 31)


@pytest.fixture(autouse=True)
async def _fresh_db():
    await create_all()
    yield
    await drop_all()


async def _seed_chef_and_dish() -> None:
    async with transaction() as s:
        s.add(
            ChefProfile(
                chef_phone=CHEF,
                kitchen_name="Ramesh Kitchen",
                chef_name="Ramesh",
                address="Flat 402, Hitech City",
                latitude=17.44829380,
                longitude=78.38148410,
            )
        )
        s.add(
            ChefMenuItem(
                menu_item_id=ITEM,
                chef_phone=CHEF,
                dish_name="Special Paneer Thali",
                unit_price=180,
                meal_type="LUNCH",
            )
        )


async def test_daily_capacity_upsert_inserts_then_updates_in_place():
    await _seed_chef_and_dish()

    async with transaction() as s:
        row = await chef_exec.execute_daily_capacity_upsert(
            s, chef_phone=CHEF, menu_item_id=ITEM, service_date=SERVICE_DATE,
            meal_window="LUNCH", max_capacity=15,
        )
    assert row.max_capacity == 15

    # Upsert again for the same slot → must UPDATE, not create a duplicate.
    async with transaction() as s:
        row2 = await chef_exec.execute_daily_capacity_upsert(
            s, chef_phone=CHEF, menu_item_id=ITEM, service_date=SERVICE_DATE,
            meal_window="LUNCH", max_capacity=25,
        )
    assert row2.max_capacity == 25

    async with transaction() as s:
        count = (
            await s.execute(select(func.count()).select_from(ChefDailyInventory))
        ).scalar_one()
    assert count == 1  # still exactly one row for the slot


async def test_dish_stock_toggle():
    await _seed_chef_and_dish()

    async with transaction() as s:
        item = await chef_exec.execute_dish_stock_toggle(s, menu_item_id=ITEM, is_available=False)
    assert item.is_available is False

    async with transaction() as s:
        again = await s.get(ChefMenuItem, ITEM)
    assert again is not None and again.is_available is False


async def test_dish_stock_toggle_missing_item_raises():
    await _seed_chef_and_dish()
    with pytest.raises(ValueError):
        async with transaction() as s:
            await chef_exec.execute_dish_stock_toggle(s, menu_item_id="itm_missing", is_available=True)


async def test_order_readiness_record():
    await _seed_chef_and_dish()
    async with transaction() as s:
        s.add(CustomerProfile(customer_phone="9111111111", name="Dinesh", delivery_address="Flat 301"))
        s.add(
            CustomerOrder(
                order_id="ord_test0001",
                customer_phone="9111111111",
                chef_phone=CHEF,
                kitchen_name="Ramesh Kitchen",
                meal_window="LUNCH",
                service_date=SERVICE_DATE,
            )
        )

    async with transaction() as s:
        readiness = await chef_exec.execute_order_readiness_record(
            s, order_id="ord_test0001", chef_phone=CHEF, box_count=2,
            special_packing_notes="Sauce packed separately",
        )
    assert readiness.status == "PACKED_READY"
    assert readiness.box_count == 2
    assert readiness.packed_at is not None
