"""Category 1 — Chef executor integration tests (runs against live PostgreSQL)."""

from __future__ import annotations

from datetime import date
import pytest
from sqlalchemy import func, select

from app.executors import chef as chef_exec
from app.models.chef import ChefDailyInventory, ChefMenuItem, ChefProfile
from app.models.customer import CustomerOrder, CustomerProfile

CHEF = "9876543210"
ITEM = "itm_test0001"
SERVICE_DATE = date(2026, 7, 31)


async def _seed_chef_and_dish(s) -> None:
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
    await s.flush()
    s.add(
        ChefMenuItem(
            menu_item_id=ITEM,
            chef_phone=CHEF,
            dish_name="Special Paneer Thali",
            unit_price=180,
            meal_type="LUNCH",
        )
    )
    await s.flush()


async def test_daily_capacity_upsert_inserts_then_updates_in_place(db_session):
    await _seed_chef_and_dish(db_session)

    row = await chef_exec.execute_daily_capacity_upsert(
        db_session, chef_phone=CHEF, menu_item_id=ITEM, service_date=SERVICE_DATE,
        meal_window="LUNCH", max_capacity=15,
    )
    assert row.max_capacity == 15

    # Upsert again for the same slot → must UPDATE, not create a duplicate.
    row2 = await chef_exec.execute_daily_capacity_upsert(
        db_session, chef_phone=CHEF, menu_item_id=ITEM, service_date=SERVICE_DATE,
        meal_window="LUNCH", max_capacity=25,
    )
    assert row2.max_capacity == 25

    count = (
        await db_session.execute(select(func.count()).select_from(ChefDailyInventory))
    ).scalar_one()
    assert count == 1  # still exactly one row for the slot


async def test_dish_stock_toggle(db_session):
    await _seed_chef_and_dish(db_session)

    item = await chef_exec.execute_dish_stock_toggle(db_session, menu_item_id=ITEM, is_available=False)
    assert item.is_available is False

    again = await db_session.get(ChefMenuItem, ITEM)
    assert again is not None and again.is_available is False


async def test_dish_stock_toggle_missing_item_raises(db_session):
    await _seed_chef_and_dish(db_session)
    with pytest.raises(ValueError):
        await chef_exec.execute_dish_stock_toggle(db_session, menu_item_id="itm_missing", is_available=True)


async def test_order_readiness_record(db_session):
    await _seed_chef_and_dish(db_session)
    db_session.add(CustomerProfile(customer_phone="9111111111", name="Dinesh", delivery_address="Flat 301"))
    await db_session.flush()
    db_session.add(
        CustomerOrder(
            order_id="ord_test0001",
            customer_phone="9111111111",
            chef_phone=CHEF,
            kitchen_name="Ramesh Kitchen",
            meal_window="LUNCH",
            service_date=SERVICE_DATE,
        )
    )
    await db_session.flush()

    readiness = await chef_exec.execute_order_readiness_record(
        db_session, order_id="ord_test0001", chef_phone=CHEF, box_count=2,
        special_packing_notes="Sauce packed separately",
    )
    assert readiness.status == "PACKED_READY"
    assert readiness.box_count == 2
    assert readiness.packed_at is not None
