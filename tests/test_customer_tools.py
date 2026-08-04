"""Tests for customer-domain tools (Flow 1 onward). Runs on SQLite or Postgres (db_session fixture)."""

import pytest
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chef import ChefMenuItem, ChefProfile
from app.models.customer import CustomerOrder, CustomerProfile, CustomerReview
from app.tools.common import resolve_time_pool
from app.tools.customer_tools import (
    _find_nearby_kitchens,
    _finish_registration,
    _get_customer_profile,
    _register_customer,
)
from app.tools.pause import Pause, clear_pending, get_pending, send_and_await_reply

BEFORE_LUNCH = datetime(2026, 8, 4, 9, 0)   # 9:00 AM -> LUNCH window


@pytest.mark.asyncio
async def test_get_customer_profile_not_found(db_session: AsyncSession):
    res = await _get_customer_profile(db_session, customer_phone="9999999999")
    assert res["status"] == "NOT_FOUND"
    assert "register_customer" in res["message"]


@pytest.mark.asyncio
async def test_get_customer_profile_incomplete(db_session: AsyncSession):
    db_session.add(
        CustomerProfile(
            customer_phone="9111111111",
            name="Partial User",
            delivery_address="Somewhere, Ghansoli",
            is_registered=False,
        )
    )
    await db_session.flush()

    res = await _get_customer_profile(db_session, customer_phone="9111111111")
    assert res["status"] == "INCOMPLETE"
    assert "register_customer" in res["message"]


@pytest.mark.asyncio
async def test_get_customer_profile_found(db_session: AsyncSession):
    db_session.add(
        CustomerProfile(
            customer_phone="9123456789",
            name="Ramesh",
            delivery_address="Indravati CHS, Ghansoli",
            latitude=Decimal("19.1214684"),
            longitude=Decimal("73.0036295"),
            is_registered=True,
        )
    )
    await db_session.flush()

    res = await _get_customer_profile(db_session, customer_phone="9123456789")
    assert res["status"] == "FOUND"
    assert res["profile"]["name"] == "Ramesh"
    assert res["profile"]["latitude"] == pytest.approx(19.1214684)


# ---- resolve_time_pool (pure) ----

def test_resolve_time_pool_brackets():
    assert resolve_time_pool(datetime(2026, 8, 4, 9, 0))["window"] == "LUNCH"
    assert resolve_time_pool(datetime(2026, 8, 4, 13, 0))["window"] == "DINNER"
    late = resolve_time_pool(datetime(2026, 8, 4, 20, 0))
    assert late["window"] == "LUNCH"
    assert late["service_date"] == date(2026, 8, 5)   # tomorrow


# ---- find_nearby_kitchens ----

async def _seed_chef(session, phone, name, lat, lon, meal="LUNCH", available=True):
    session.add(
        ChefProfile(
            chef_phone=phone, kitchen_name=f"{name} Kitchen", chef_name=name,
            address="Ghansoli", latitude=Decimal(str(lat)), longitude=Decimal(str(lon)),
            dietary_type="VEG",
        )
    )
    session.add(
        ChefMenuItem(
            chef_phone=phone, dish_name="Thali", unit_price=Decimal("120.00"),
            meal_type=meal, is_available=available,
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_find_nearby_none_open(db_session: AsyncSession):
    await _seed_chef(db_session, "9876500001", "OnlyDinner", 19.12, 73.00, meal="DINNER")
    res = await _find_nearby_kitchens(db_session, latitude=19.12, longitude=73.00, now=BEFORE_LUNCH)
    assert res["status"] == "NONE_OPEN"
    assert res["window"] == "LUNCH"


@pytest.mark.asyncio
async def test_find_nearby_ok_sorted_and_rated(db_session: AsyncSession):
    await _seed_chef(db_session, "9876500001", "Near", 19.1214, 73.0036)
    await _seed_chef(db_session, "9876500002", "Far", 19.2000, 73.1000)
    db_session.add(
        CustomerProfile(customer_phone="9123456789", name="C", delivery_address="X", is_registered=True)
    )
    db_session.add(
        CustomerOrder(order_id="ord_rev_1", customer_phone="9123456789", chef_phone="9876500001",
                      kitchen_name="Near Kitchen", service_date=date(2026, 8, 4))
    )
    await db_session.flush()
    db_session.add(
        CustomerReview(order_id="ord_rev_1", customer_phone="9123456789",
                       chef_phone="9876500001", chef_rating=5)
    )
    await db_session.flush()

    res = await _find_nearby_kitchens(db_session, latitude=19.1210, longitude=73.0030, now=BEFORE_LUNCH)
    assert res["status"] == "OK"
    assert len(res["kitchens"]) == 2
    assert res["kitchens"][0]["chef_phone"] == "9876500001"        # nearest first (by distance only)
    assert res["kitchens"][0]["distance_km"] < res["kitchens"][1]["distance_km"]


# ---- send_and_await_reply (pause primitive) ----

def test_send_and_await_reply_records_and_raises():
    clear_pending("7000000003")
    with pytest.raises(Pause) as ei:
        send_and_await_reply("7000000003", "share your location",
                             await_type="LOCATION_PIN", resume="finish_registration", ctx={"x": 1})
    assert ei.value.await_type == "LOCATION_PIN"
    note = get_pending("7000000003")
    assert note is not None
    assert note["resume"] == "finish_registration"
    assert note["ctx"]["x"] == 1
    clear_pending("7000000003")


# ---- register_customer + finish_registration ----

@pytest.mark.asyncio
async def test_register_customer_saves_half_then_awaits(db_session: AsyncSession):
    res = await _register_customer(db_session, customer_phone="7000000001",
                                   name="New Guy", delivery_address="Sector 6, Ghansoli")
    assert res["status"] == "AWAITING_LOCATION"
    assert res["ctx"]["name"] == "New Guy"
    prof = await db_session.get(CustomerProfile, "7000000001")
    assert prof is not None
    assert prof.is_registered is False        # half-registered
    assert prof.latitude is None              # no location yet


@pytest.mark.asyncio
async def test_register_customer_invalid(db_session: AsyncSession):
    res = await _register_customer(db_session, customer_phone="7000000002", name="", delivery_address="")
    assert res["status"] == "INVALID"


@pytest.mark.asyncio
async def test_finish_registration_saves_location(db_session: AsyncSession):
    await _register_customer(db_session, customer_phone="7000000004", name="Half", delivery_address="Addr")
    await _finish_registration(db_session, customer_phone="7000000004", name="Half",
                               delivery_address="Addr", latitude=19.12, longitude=73.00)
    prof = await db_session.get(CustomerProfile, "7000000004")
    assert prof.is_registered is True
    assert float(prof.latitude) == pytest.approx(19.12)
