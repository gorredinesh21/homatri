"""Tests for the post-payment top-up flow (add extra items to a paid order)."""

import pytest
from datetime import date
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chef import ChefMenuItem, ChefProfile
from app.models.customer import CustomerOrder, CustomerOrderItem, CustomerPayment, CustomerProfile
from app.models.system import SystemOutboundQueue
from app.tools.pause import clear_pending, get_pending
from app.tools.topup import (
    _apply_topup_payment,
    _mint_and_arm_payment,
    _request_order_topup,
    _respond_to_topup_request,
    _topups,
    resolve_topup_counter,
)

SD = date(2026, 8, 4)


async def _seed(session, cust, chef, order_id, status="CONFIRMED", dish="Paneer", price="120.00"):
    """A paid order (status past payment) with one existing 1× dish line + the dish on the menu."""
    session.add(CustomerProfile(customer_phone=cust, name="C", delivery_address="X",
                                latitude=Decimal("19.12"), longitude=Decimal("73.00"), is_registered=True))
    session.add(ChefProfile(chef_phone=chef, kitchen_name="Test Kitchen", chef_name="Chef T", address="G",
                            latitude=Decimal("19.12"), longitude=Decimal("73.00"), dietary_type="VEG"))
    mi = ChefMenuItem(chef_phone=chef, dish_name=dish, unit_price=Decimal(price), meal_type="LUNCH",
                      dietary_tag="VEG", spice_level="MEDIUM", is_available=True)
    session.add(mi)
    await session.flush()
    session.add(CustomerOrder(order_id=order_id, customer_phone=cust, chef_phone=chef, kitchen_name="Test Kitchen",
                              service_date=SD, meal_window="LUNCH", status=status,
                              cart_subtotal=Decimal(price), delivery_fee=Decimal("20.00"),
                              total_amount=Decimal(price) + Decimal("20.00")))
    session.add(CustomerOrderItem(order_id=order_id, menu_item_id=mi.menu_item_id, chef_phone=chef,
                                  dish_name=dish, quantity=1, unit_price=Decimal(price),
                                  item_subtotal=Decimal(price), service_date=SD))
    await session.flush()
    return order_id


async def _out(session, role):
    return (await session.execute(
        select(SystemOutboundQueue).where(SystemOutboundQueue.recipient_role == role))).scalars().all()


# ---- request guards ----

@pytest.mark.asyncio
async def test_request_no_order(db_session: AsyncSession):
    _topups.clear()
    res = await _request_order_topup(db_session, customer_phone="7000000700",
                                     items=[{"dish_name": "Paneer", "quantity": 2}])
    assert res["status"] == "NO_ORDER"


@pytest.mark.asyncio
async def test_request_unpaid_order(db_session: AsyncSession):
    _topups.clear()
    await _seed(db_session, "7000000701", "9876500701", "ord_t1", status="PENDING_PAYMENT")
    res = await _request_order_topup(db_session, customer_phone="7000000701",
                                     items=[{"dish_name": "Paneer", "quantity": 2}])
    assert res["status"] == "UNPAID"


@pytest.mark.asyncio
async def test_request_too_late(db_session: AsyncSession):
    _topups.clear()
    await _seed(db_session, "7000000702", "9876500702", "ord_t2", status="PACKED")
    res = await _request_order_topup(db_session, customer_phone="7000000702",
                                     items=[{"dish_name": "Paneer", "quantity": 2}])
    assert res["status"] == "TOO_LATE"


@pytest.mark.asyncio
async def test_request_awaiting_chef(db_session: AsyncSession):
    _topups.clear()
    await _seed(db_session, "7000000703", "9876500703", "ord_t3")
    res = await _request_order_topup(db_session, customer_phone="7000000703",
                                     items=[{"dish_name": "Paneer", "quantity": 2}])
    assert res["status"] == "AWAITING_CHEF"
    assert _topups["7000000703"]["amount"] == 240.0            # 2 × 120
    assert any("Paneer" in o.message_text for o in await _out(db_session, "CHEF"))


@pytest.mark.asyncio
async def test_request_bad_dish(db_session: AsyncSession):
    _topups.clear()
    await _seed(db_session, "7000000704", "9876500704", "ord_t4")
    res = await _request_order_topup(db_session, customer_phone="7000000704",
                                     items=[{"dish_name": "Sushi", "quantity": 1}])
    assert res["status"] == "INVALID_ITEM"


# ---- accept -> pay -> apply ----

@pytest.mark.asyncio
async def test_accept_mints_link_and_arms_payment(db_session: AsyncSession):
    _topups.clear()
    clear_pending("7000000705")
    await _seed(db_session, "7000000705", "9876500705", "ord_t5")
    await _request_order_topup(db_session, customer_phone="7000000705",
                               items=[{"dish_name": "Paneer", "quantity": 2}])
    res = await _respond_to_topup_request(db_session, chef_phone="9876500705", decision="accept")
    assert res["status"] == "ACCEPTED"
    # customer armed for the delta payment (out-of-band PAYMENT_CONFIRM)
    note = get_pending("7000000705")
    assert note is not None and note["await_type"] == "PAYMENT_CONFIRM"
    assert note["resume"] == "confirm_topup_payment"
    # a PENDING TOPUP payment exists
    pay = (await db_session.execute(
        select(CustomerPayment).where(CustomerPayment.order_id == "ord_t5",
                                      CustomerPayment.payment_type == "TOPUP"))).scalars().first()
    assert pay is not None and pay.status == "PENDING" and float(pay.amount_due) == 240.0
    clear_pending("7000000705")


@pytest.mark.asyncio
async def test_payment_applies_items_without_cascade(db_session: AsyncSession):
    _topups.clear()
    clear_pending("7000000706")
    await _seed(db_session, "7000000706", "9876500706", "ord_t6")
    await _request_order_topup(db_session, customer_phone="7000000706",
                               items=[{"dish_name": "Paneer", "quantity": 2}])
    await _respond_to_topup_request(db_session, chef_phone="9876500706", decision="accept")
    summary = await _apply_topup_payment(db_session, phone="7000000706", txn="txn_top")
    assert "Paneer" in summary

    order = await db_session.get(CustomerOrder, "ord_t6")
    assert order.status == "CONFIRMED"                          # NOT re-confirmed / cascaded
    assert float(order.total_amount) == 380.0                  # (1+2)×120 + 20 delivery
    item = (await db_session.execute(
        select(CustomerOrderItem).where(CustomerOrderItem.order_id == "ord_t6"))).scalars().first()
    assert item.quantity == 3                                  # 1 existing + 2 added
    pay = (await db_session.execute(
        select(CustomerPayment).where(CustomerPayment.order_id == "ord_t6",
                                      CustomerPayment.payment_type == "TOPUP"))).scalars().first()
    assert pay.status == "PAID" and float(pay.amount_paid) == 240.0
    assert any("updated" in o.message_text for o in await _out(db_session, "CHEF"))
    clear_pending("7000000706")


# ---- reject ----

@pytest.mark.asyncio
async def test_reject_leaves_order_unchanged(db_session: AsyncSession):
    _topups.clear()
    await _seed(db_session, "7000000707", "9876500707", "ord_t7")
    await _request_order_topup(db_session, customer_phone="7000000707",
                               items=[{"dish_name": "Paneer", "quantity": 2}])
    res = await _respond_to_topup_request(db_session, chef_phone="9876500707", decision="reject")
    assert res["status"] == "REJECTED"
    assert "7000000707" not in _topups
    # no top-up payment created
    pays = (await db_session.execute(
        select(CustomerPayment).where(CustomerPayment.order_id == "ord_t7"))).scalars().all()
    assert all(p.payment_type != "TOPUP" for p in pays)


@pytest.mark.asyncio
async def test_respond_no_open_request(db_session: AsyncSession):
    _topups.clear()
    res = await _respond_to_topup_request(db_session, chef_phone="9999999999", decision="accept")
    assert res["status"] == "NO_REQUEST"


# ---- counter ----

@pytest.mark.asyncio
async def test_counter_needs_note_and_items(db_session: AsyncSession):
    _topups.clear()
    await _seed(db_session, "7000000708", "9876500708", "ord_t8")
    await _request_order_topup(db_session, customer_phone="7000000708",
                               items=[{"dish_name": "Paneer", "quantity": 2}])
    r1 = await _respond_to_topup_request(db_session, chef_phone="9876500708", decision="counter")
    assert r1["status"] == "NEED_COUNTER"                       # no note
    r2 = await _respond_to_topup_request(db_session, chef_phone="9876500708",
                                         decision="counter", counter_note="only 1 left")
    assert r2["status"] == "NEED_COUNTER"                       # note but no items


@pytest.mark.asyncio
async def test_counter_then_customer_accepts(db_session: AsyncSession):
    _topups.clear()
    clear_pending("7000000709")
    await _seed(db_session, "7000000709", "9876500709", "ord_t9")
    await _request_order_topup(db_session, customer_phone="7000000709",
                               items=[{"dish_name": "Paneer", "quantity": 2}])
    res = await _respond_to_topup_request(db_session, chef_phone="9876500709", decision="counter",
                                          counter_note="only 1 paneer left",
                                          counter_items=[{"dish_name": "Paneer", "quantity": 1}])
    assert res["status"] == "COUNTER_SENT"
    note = get_pending("7000000709")
    assert note is not None and note["await_type"] == "CUSTOMER_TOPUP_DECISION"
    n = _topups["7000000709"]
    assert n["status"] == "WAITING_CUSTOMER" and n["counter_amount"] == 120.0

    # customer accepts the counter -> mint the delta link for the chef's qty, then pay
    mint = await _mint_and_arm_payment(db_session, n=n, items=n["counter_items"], amount=n["counter_amount"])
    assert mint["status"] == "MINTED" and n["final_items"] == n["counter_items"]
    await _apply_topup_payment(db_session, phone="7000000709", txn="txn_c")
    order = await db_session.get(CustomerOrder, "ord_t9")
    assert float(order.total_amount) == 260.0                  # (1+1)×120 + 20
    clear_pending("7000000709")


@pytest.mark.asyncio
async def test_counter_then_customer_declines(db_session: AsyncSession):
    _topups.clear()
    clear_pending("7000000710")
    await _seed(db_session, "7000000710", "9876500710", "ord_t10")
    await _request_order_topup(db_session, customer_phone="7000000710",
                               items=[{"dish_name": "Paneer", "quantity": 2}])
    await _respond_to_topup_request(db_session, chef_phone="9876500710", decision="counter",
                                    counter_note="only 1 left",
                                    counter_items=[{"dish_name": "Paneer", "quantity": 1}])
    reply = await resolve_topup_counter("7000000710", {"text": "no thanks"}, {})
    assert "as-is" in reply
    assert "7000000710" not in _topups
    # order untouched (still 1× Paneer)
    item = (await db_session.execute(
        select(CustomerOrderItem).where(CustomerOrderItem.order_id == "ord_t10"))).scalars().first()
    assert item.quantity == 1
