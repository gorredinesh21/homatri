"""Tests for delegate_write — the cross-domain write choke-point (permission + audit)."""

import pytest
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import CustomerOrder
from app.models.system import SystemDeliveryStop
from app.tools.delegate import delegate_write

SERVICE_DATE = date(2026, 8, 4)


async def _seed_batched_order(session, order_id="ord_del_1"):
    session.add(CustomerOrder(
        order_id=order_id, customer_phone="7000000090", chef_phone="9876500090",
        kitchen_name="K", service_date=SERVICE_DATE, meal_window="LUNCH", status="BATCHED",
        cart_subtotal=Decimal("100.00"), delivery_fee=Decimal("20.00"), total_amount=Decimal("120.00")))
    await session.flush()
    return order_id


@pytest.mark.asyncio
async def test_chef_can_advance_order_status(db_session: AsyncSession):
    order_id = await _seed_batched_order(db_session)
    res = await delegate_write(db_session, requesting_role="CHEF", capability="ORDER_STATUS",
                               order_id=order_id, target_status="COOKING", actor_role="CHEF")
    assert res["status"] == "WRITTEN"
    order = await db_session.get(CustomerOrder, order_id)
    assert order.status == "COOKING"


@pytest.mark.asyncio
async def test_customer_role_is_denied_order_status(db_session: AsyncSession):
    order_id = await _seed_batched_order(db_session, "ord_del_2")
    res = await delegate_write(db_session, requesting_role="CUSTOMER", capability="ORDER_STATUS",
                               order_id=order_id, target_status="COOKING", actor_role="CUSTOMER")
    assert res["status"] == "DENIED"
    order = await db_session.get(CustomerOrder, order_id)
    assert order.status == "BATCHED"            # unchanged


@pytest.mark.asyncio
async def test_unknown_capability_denied(db_session: AsyncSession):
    res = await delegate_write(db_session, requesting_role="MASTER", capability="NUKE_DB")
    assert res["status"] == "DENIED"
    assert "Unknown capability" in res["message"]


@pytest.mark.asyncio
async def test_driver_can_update_stop_status(db_session: AsyncSession):
    db_session.add(SystemDeliveryStop(
        stop_id="stp_del_1", route_id="rt_dummy", stop_index=1, stop_type="DROPOFF_GATE",
        target_ref_id="7000000090", location_name="Gate A", address="Ghansoli",
        latitude=Decimal("19.12"), longitude=Decimal("73.00"),
        estimated_arrival=datetime(2026, 8, 4, 12, 30), status="PENDING"))
    await db_session.flush()
    res = await delegate_write(db_session, requesting_role="DRIVER", capability="STOP_STATUS",
                               stop_id="stp_del_1", target_status="ARRIVED")
    assert res["status"] == "WRITTEN"
    stop = await db_session.get(SystemDeliveryStop, "stp_del_1")
    assert stop.status == "ARRIVED"
