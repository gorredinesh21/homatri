"""Tests for Master-domain tools (Flow 4 gateway): mint_payment_link + process_payment_webhook."""

import pytest
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chef import ChefMenuItem, ChefProfile
from app.models.customer import CustomerOrder, CustomerPayment, CustomerProfile
from app.models.driver import DriverProfile, DriverTripStatus
from app.models.system import SystemDeliveryRoute, SystemMealWindow, SystemOutboundQueue
from app.tools.customer_tools import _create_order
from app.tools.master_tools import (
    _allocate_driver,
    _mint_payment_link,
    _process_payment_webhook,
    _run_cutoff_batch,
)

BEFORE_LUNCH = datetime(2026, 8, 4, 9, 0)
SERVICE_DATE = date(2026, 8, 4)


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


# ---- allocate_driver ----

async def _seed_driver(session, phone, name, on_shift=True, active=True):
    session.add(DriverProfile(driver_phone=phone, driver_name=name, vehicle_type="BIKE",
                              vehicle_number="MH43 XX 0000", vehicle_model="Activa",
                              is_on_shift=on_shift, active_status=active))
    await session.flush()


@pytest.mark.asyncio
async def test_allocate_driver_assigns_available(db_session: AsyncSession):
    await _seed_driver(db_session, "9111000001", "Vikram")
    res = await _allocate_driver(db_session, window="LUNCH", service_date=SERVICE_DATE)
    assert res["status"] == "ASSIGNED"
    assert res["driver_phone"] == "9111000001"


@pytest.mark.asyncio
async def test_allocate_driver_none_when_off_shift(db_session: AsyncSession):
    await _seed_driver(db_session, "9111000002", "Resting", on_shift=False)
    res = await _allocate_driver(db_session, window="LUNCH", service_date=SERVICE_DATE)
    assert res["status"] == "NO_DRIVER"


@pytest.mark.asyncio
async def test_allocate_driver_skips_already_assigned(db_session: AsyncSession):
    # Only one driver, already on a trip this window -> NO_DRIVER
    await _seed_driver(db_session, "9111000003", "Busy")
    db_session.add(DriverTripStatus(driver_phone="9111000003", route_id="rt_dummy",
                                    service_date=SERVICE_DATE, meal_window="LUNCH", total_stops=3))
    await db_session.flush()
    res = await _allocate_driver(db_session, window="LUNCH", service_date=SERVICE_DATE)
    assert res["status"] == "NO_DRIVER"


@pytest.mark.asyncio
async def test_allocate_driver_picks_the_free_one(db_session: AsyncSession):
    await _seed_driver(db_session, "9111000004", "Taken")
    await _seed_driver(db_session, "9111000005", "Free")
    db_session.add(DriverTripStatus(driver_phone="9111000004", route_id="rt_dummy",
                                    service_date=SERVICE_DATE, meal_window="LUNCH", total_stops=2))
    await db_session.flush()
    res = await _allocate_driver(db_session, window="LUNCH", service_date=SERVICE_DATE)
    assert res["status"] == "ASSIGNED"
    assert res["driver_phone"] == "9111000005"   # the un-assigned one


# ---- run_cutoff_batch ----

async def _seed_confirmed_order(session, cust, chef):
    """Order -> pay -> CONFIRMED (the real path), so run_cutoff_batch can pick it up."""
    order_id = await _seed_pending_order(session, cust, chef)
    mint = await _mint_payment_link(session, order_id=order_id)
    await _process_payment_webhook(session, payment_id=mint["payment_id"])
    return order_id


@pytest.mark.asyncio
async def test_run_cutoff_batch_no_orders(db_session: AsyncSession):
    res = await _run_cutoff_batch(db_session, window="LUNCH", service_date=SERVICE_DATE)
    assert res["status"] == "NO_ORDERS"


@pytest.mark.asyncio
async def test_run_cutoff_batch_batches_and_dispatches(db_session: AsyncSession):
    order_id = await _seed_confirmed_order(db_session, "7000000080", "9876500080")
    await _seed_driver(db_session, "9111000080", "Vikram")

    res = await _run_cutoff_batch(db_session, window="LUNCH", service_date=SERVICE_DATE)
    assert res["status"] == "BATCHED"
    assert res["total_orders"] == 1

    # order flipped CONFIRMED -> BATCHED
    order = await db_session.get(CustomerOrder, order_id)
    assert order.status == "BATCHED"

    # route + driver trip created
    route = (await db_session.execute(select(SystemDeliveryRoute))).scalars().first()
    assert route is not None and route.driver_phone == "9111000080"
    trip = (await db_session.execute(select(DriverTripStatus))).scalars().first()
    assert trip is not None and trip.route_id == route.route_id

    # window locked
    win = (await db_session.execute(
        select(SystemMealWindow).where(SystemMealWindow.service_date == SERVICE_DATE))).scalars().first()
    assert win.status == "LOCKED_PROCESSING"

    # chef + driver both notified
    outs = (await db_session.execute(select(SystemOutboundQueue))).scalars().all()
    roles = {o.recipient_role for o in outs}
    assert {"CHEF", "DRIVER"} <= roles


@pytest.mark.asyncio
async def test_run_cutoff_batch_idempotent(db_session: AsyncSession):
    await _seed_confirmed_order(db_session, "7000000081", "9876500081")
    await _seed_driver(db_session, "9111000081", "Vikram")
    await _run_cutoff_batch(db_session, window="LUNCH", service_date=SERVICE_DATE)
    again = await _run_cutoff_batch(db_session, window="LUNCH", service_date=SERVICE_DATE)
    assert again["status"] == "ALREADY_BATCHED"


@pytest.mark.asyncio
async def test_run_cutoff_batch_no_driver(db_session: AsyncSession):
    await _seed_confirmed_order(db_session, "7000000082", "9876500082")   # no driver seeded
    res = await _run_cutoff_batch(db_session, window="LUNCH", service_date=SERVICE_DATE)
    assert res["status"] == "NO_DRIVER"
    # nothing batched -> order stays CONFIRMED, window not locked
    win = (await db_session.execute(
        select(SystemMealWindow).where(SystemMealWindow.service_date == SERVICE_DATE))).scalars().first()
    assert win.status == "OPEN"
