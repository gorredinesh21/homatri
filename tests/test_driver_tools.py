"""Tests for the Driver core loop (Flow 7): profile, duty, route, pickup, delivery."""

import pytest
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import CustomerOrder, CustomerOrderItem, CustomerProfile
from app.models.driver import DriverProfile, DriverTripStatus
from app.models.system import (
    SystemDeliveryRoute,
    SystemDeliveryStop,
    SystemDeliveryStopOrder,
    SystemMealWindow,
    SystemOutboundQueue,
)
from app.tools.driver_tools import (
    _ask_chef_status,
    _confirm_delivery,
    _confirm_pickup,
    _driver_queries,
    _get_driver_route,
    _respond_to_driver_query,
    _update_duty_status,
)

SD = date(2026, 8, 4)
DRV = "9500000001"


async def _seed_route(session, *, driver=DRV, n_drops=2, order_status="PACKED"):
    """A driver + trip + route: 1 pickup + n_drops dropoffs, each with one order."""
    session.add(DriverProfile(driver_phone=driver, driver_name="Vik", vehicle_type="BIKE",
                              vehicle_number="MH01", is_on_shift=True, active_status=True))
    win = SystemMealWindow(window_id="win_d", service_date=SD, meal_type="DINNER",
                           cutoff_at=datetime(2026, 8, 4, 18, 30), status="OPEN")
    session.add(win)
    route = SystemDeliveryRoute(route_id="rt_d", window_id="win_d", driver_phone=driver, service_date=SD,
                                meal_window="DINNER", total_stops=1 + n_drops, total_orders=n_drops, status="ASSIGNED")
    session.add(route)
    session.add(DriverTripStatus(trip_id="trp_d", driver_phone=driver, route_id="rt_d", service_date=SD,
                                 meal_window="DINNER", status="ASSIGNED", current_stop_index=1, total_stops=1 + n_drops))
    # pickup stop
    session.add(SystemDeliveryStop(stop_id="stp_pick", route_id="rt_d", stop_index=1, stop_type="PICKUP",
                                   target_ref_id="9900000001", location_name="Test Kitchen", address="Ghansoli",
                                   latitude=Decimal("19.124"), longitude=Decimal("73.001"),
                                   estimated_arrival=datetime(2026, 8, 4, 19, 15), status="PENDING"))
    for i in range(n_drops):
        oid = f"ord_d{i+1}"
        cust = f"700000090{i}"
        session.add(CustomerProfile(customer_phone=cust, name=f"Cust{i}", delivery_address=f"Flat {i}",
                                    latitude=Decimal("19.12"), longitude=Decimal("73.00"), is_registered=True))
        session.add(CustomerOrder(order_id=oid, customer_phone=cust, chef_phone="9900000001", kitchen_name="Test Kitchen",
                                  service_date=SD, meal_window="DINNER", status=order_status,
                                  cart_subtotal=Decimal("100"), delivery_fee=Decimal("20"), total_amount=Decimal("120")))
        session.add(CustomerOrderItem(order_id=oid, menu_item_id="itm", chef_phone="9900000001", dish_name="Thali",
                                      quantity=1, unit_price=Decimal("100"), item_subtotal=Decimal("100"), service_date=SD))
        stop_id = f"stp_drop{i+1}"
        session.add(SystemDeliveryStop(stop_id=stop_id, route_id="rt_d", stop_index=2 + i, stop_type="DROPOFF_GATE",
                                       target_ref_id=cust, location_name=f"Gate {i+1}", address=f"Tower {i+1} Ghansoli",
                                       latitude=Decimal("19.12"), longitude=Decimal("73.00"),
                                       estimated_arrival=datetime(2026, 8, 4, 19, 25), status="PENDING"))
        session.add(SystemDeliveryStopOrder(stop_id=stop_id, order_id=oid))
    await session.flush()


@pytest.mark.asyncio
async def test_update_duty(db_session: AsyncSession):
    db_session.add(DriverProfile(driver_phone=DRV, driver_name="V", vehicle_type="BIKE",
                                 vehicle_number="MH", is_on_shift=True, active_status=True))
    await db_session.flush()
    assert (await _update_duty_status(db_session, driver_phone=DRV, on_duty=False))["status"] == "OFF_DUTY"
    assert (await db_session.get(DriverProfile, DRV)).is_on_shift is False


@pytest.mark.asyncio
async def test_route_shows_pickup_first(db_session: AsyncSession):
    await _seed_route(db_session)
    res = await _get_driver_route(db_session, driver_phone=DRV)
    assert res["status"] == "OK"
    assert "PICKUP" in res["message"]


@pytest.mark.asyncio
async def test_pickup_blocked_until_packed(db_session: AsyncSession):
    await _seed_route(db_session, order_status="BATCHED")   # not packed yet
    res = await _confirm_pickup(db_session, driver_phone=DRV)
    assert res["status"] == "NOT_READY"


@pytest.mark.asyncio
async def test_pickup_then_deliver_full_route(db_session: AsyncSession):
    await _seed_route(db_session, n_drops=2, order_status="PACKED")
    # pickup
    pk = await _confirm_pickup(db_session, driver_phone=DRV)
    assert pk["status"] == "PICKED_UP"
    assert (await db_session.get(CustomerOrder, "ord_d1")).status == "PICKED_UP"
    assert "DROP" in pk["message"]                 # next leg revealed
    # deliver stop 1 (current)
    d1 = await _confirm_delivery(db_session, driver_phone=DRV)
    assert d1["status"] == "DELIVERED"
    assert (await db_session.get(CustomerOrder, "ord_d1")).status == "DELIVERED"
    # deliver stop 2 -> route complete
    d2 = await _confirm_delivery(db_session, driver_phone=DRV)
    assert d2["status"] == "DELIVERED"
    assert "complete" in d2["message"].lower()
    assert (await db_session.get(CustomerOrder, "ord_d2")).status == "DELIVERED"
    # each delivered customer got a notification
    outs = (await db_session.execute(select(SystemOutboundQueue).where(
        SystemOutboundQueue.recipient_role == "CUSTOMER"))).scalars().all()
    assert len(outs) == 2


@pytest.mark.asyncio
async def test_deliver_out_of_order_by_location(db_session: AsyncSession):
    await _seed_route(db_session, n_drops=3, order_status="PACKED")
    await _confirm_pickup(db_session, driver_phone=DRV)
    # deliver the THIRD gate first, by name
    res = await _confirm_delivery(db_session, driver_phone=DRV, location="Gate 3")
    assert res["status"] == "DELIVERED"
    assert (await db_session.get(CustomerOrder, "ord_d3")).status == "DELIVERED"
    assert (await db_session.get(CustomerOrder, "ord_d1")).status == "PICKED_UP"   # still pending


@pytest.mark.asyncio
async def test_deliver_partial_with_exception(db_session: AsyncSession):
    await _seed_route(db_session, n_drops=1, order_status="PACKED")
    await _confirm_pickup(db_session, driver_phone=DRV)
    res = await _confirm_delivery(db_session, driver_phone=DRV, undelivered_ids=["ord_d1"])
    assert res["status"] == "PARTIAL"
    assert (await db_session.get(CustomerOrder, "ord_d1")).status == "PICKED_UP"   # not delivered


@pytest.mark.asyncio
async def test_ask_chef_status_ready(db_session: AsyncSession):
    _driver_queries.clear()
    await _seed_route(db_session, order_status="PACKED")
    res = await _ask_chef_status(db_session, driver_phone=DRV)
    assert res["status"] == "READY"                       # instant, no chef needed


@pytest.mark.asyncio
async def test_ask_chef_status_asks_and_chef_replies(db_session: AsyncSession):
    _driver_queries.clear()
    await _seed_route(db_session, order_status="BATCHED")   # not packed
    res = await _ask_chef_status(db_session, driver_phone=DRV)
    assert res["status"] == "ASKED"
    assert _driver_queries[DRV]["status"] == "WAITING"
    assert any("waiting" in o.message_text for o in (await db_session.execute(
        select(SystemOutboundQueue).where(SystemOutboundQueue.recipient_role == "CHEF"))).scalars().all())
    # chef replies -> pushed to the driver
    r = await _respond_to_driver_query(db_session, chef_phone="9900000001", reply="5 more minutes")
    assert r["status"] == "SENT"
    drv_msgs = (await db_session.execute(select(SystemOutboundQueue).where(
        SystemOutboundQueue.recipient_role == "DRIVER"))).scalars().all()
    assert any("5 more minutes" in o.message_text for o in drv_msgs)
    clear = _driver_queries.clear()
