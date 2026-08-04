"""Tests for customer-domain tools (Flow 1 onward). Runs on SQLite or Postgres (db_session fixture)."""

import pytest
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chef import ChefMenuItem, ChefProfile
from app.models.customer import CustomerOrder, CustomerPayment, CustomerProfile, CustomerReview
from app.tools.common import resolve_time_pool
from app.models.system import SystemSetting
from app.tools.customer_tools import (
    _add_item_to_order,
    _confirm_payment,
    _create_order,
    _find_nearby_kitchens,
    _finish_registration,
    _get_customer_profile,
    _register_customer,
    _request_payment,
    _view_cart,
    _view_chef_menu,
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
async def test_register_customer_already_registered_is_guarded(db_session: AsyncSession):
    db_session.add(CustomerProfile(
        customer_phone="7000000005", name="Done Already", delivery_address="Addr",
        latitude=Decimal("19.12"), longitude=Decimal("73.00"), is_registered=True))
    await db_session.flush()
    res = await _register_customer(db_session, customer_phone="7000000005",
                                   name="Done Already", delivery_address="Addr")
    assert res["status"] == "ALREADY_REGISTERED"
    prof = await db_session.get(CustomerProfile, "7000000005")
    assert prof.is_registered is True        # NOT flipped back to False


@pytest.mark.asyncio
async def test_finish_registration_saves_location(db_session: AsyncSession):
    await _register_customer(db_session, customer_phone="7000000004", name="Half", delivery_address="Addr")
    await _finish_registration(db_session, customer_phone="7000000004", name="Half",
                               delivery_address="Addr", latitude=19.12, longitude=73.00)
    prof = await db_session.get(CustomerProfile, "7000000004")
    assert prof.is_registered is True
    assert float(prof.latitude) == pytest.approx(19.12)


# ---- view_chef_menu ----

async def _seed_chef_with_dish(session, phone, dish, meal="LUNCH", available=True):
    if await session.get(ChefProfile, phone) is None:
        session.add(ChefProfile(chef_phone=phone, kitchen_name=f"{phone} Kitchen", chef_name="Chef",
                                address="Ghansoli", latitude=Decimal("19.12"), longitude=Decimal("73.00"),
                                dietary_type="VEG"))
    session.add(ChefMenuItem(chef_phone=phone, dish_name=dish, unit_price=Decimal("120.00"),
                             meal_type=meal, dietary_tag="VEG", spice_level="MEDIUM", is_available=available))
    await session.flush()


@pytest.mark.asyncio
async def test_view_chef_menu_not_found(db_session: AsyncSession):
    res = await _view_chef_menu(db_session, kitchen="9999999999", now=BEFORE_LUNCH)
    assert res["status"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_view_chef_menu_only_available(db_session: AsyncSession):
    await _seed_chef_with_dish(db_session, "9876500010", "Thali", meal="LUNCH", available=True)
    await _seed_chef_with_dish(db_session, "9876500010", "SoldOut Dish", meal="LUNCH", available=False)
    res = await _view_chef_menu(db_session, kitchen="9876500010", now=BEFORE_LUNCH)
    assert res["status"] == "OK"
    assert [d["name"] for d in res["dishes"]] == ["Thali"]   # only the available one


@pytest.mark.asyncio
async def test_view_chef_menu_not_serving(db_session: AsyncSession):
    await _seed_chef_with_dish(db_session, "9876500011", "Dinner Dish", meal="DINNER", available=True)
    res = await _view_chef_menu(db_session, kitchen="9876500011", now=BEFORE_LUNCH)  # clock -> LUNCH
    assert res["status"] == "NOT_SERVING"


@pytest.mark.asyncio
async def test_view_chef_menu_explicit_window(db_session: AsyncSession):
    await _seed_chef_with_dish(db_session, "9876500012", "Dinner Dish", meal="DINNER", available=True)
    res = await _view_chef_menu(db_session, kitchen="9876500012", window="DINNER", now=BEFORE_LUNCH)
    assert res["status"] == "OK"
    assert res["dishes"][0]["name"] == "Dinner Dish"


# ---- create_order ----

async def _seed_customer_and_chef_with_dish(session, cust, chef, dish_name="Thali", price="120.00"):
    session.add(CustomerProfile(customer_phone=cust, name="C", delivery_address="X",
                                latitude=Decimal("19.12"), longitude=Decimal("73.00"), is_registered=True))
    session.add(ChefProfile(chef_phone=chef, kitchen_name="K", chef_name="Chef",
                            address="Ghansoli", latitude=Decimal("19.12"), longitude=Decimal("73.00"),
                            dietary_type="VEG"))
    dish = ChefMenuItem(chef_phone=chef, dish_name=dish_name, unit_price=Decimal(price),
                        meal_type="LUNCH", dietary_tag="VEG", spice_level="MEDIUM", is_available=True)
    session.add(dish)
    await session.flush()
    return dish


@pytest.mark.asyncio
async def test_create_order_created(db_session: AsyncSession):
    await _seed_customer_and_chef_with_dish(db_session, "7000000020", "9876500020")
    db_session.add(SystemSetting(key="delivery_fee", value={"amount": 20}, category="BUSINESS"))
    await db_session.flush()

    res = await _create_order(db_session, customer_phone="7000000020", kitchen="9876500020",
                              items=[{"dish_name": "Thali", "quantity": 2}], now=BEFORE_LUNCH)
    assert res["status"] == "CREATED"
    assert res["subtotal"] == 240.0
    assert res["delivery_fee"] == 20.0        # read from system_settings
    assert res["total"] == 260.0
    order = await db_session.get(CustomerOrder, res["order_id"])
    assert order.status == "PENDING_PAYMENT"


@pytest.mark.asyncio
async def test_create_order_resolves_kitchen_by_name(db_session: AsyncSession):
    # seed a chef whose kitchen_name is "K"; order by that NAME, not the phone
    await _seed_customer_and_chef_with_dish(db_session, "7000000025", "9876500025")
    res = await _create_order(db_session, customer_phone="7000000025", kitchen="K",
                              items=[{"dish_name": "Thali", "quantity": 1}], now=BEFORE_LUNCH)
    assert res["status"] == "CREATED"
    assert res["subtotal"] == 120.0


@pytest.mark.asyncio
async def test_create_order_falls_back_to_config_fee(db_session: AsyncSession):
    await _seed_customer_and_chef_with_dish(db_session, "7000000024", "9876500024")
    # no system_settings row -> fallback to config default (30)
    res = await _create_order(db_session, customer_phone="7000000024", kitchen="9876500024",
                              items=[{"dish_name": "Thali", "quantity": 1}], now=BEFORE_LUNCH)
    assert res["status"] == "CREATED"
    assert res["delivery_fee"] == 30.0


@pytest.mark.asyncio
async def test_create_order_order_exists(db_session: AsyncSession):
    await _seed_customer_and_chef_with_dish(db_session, "7000000021", "9876500021")
    db_session.add(CustomerOrder(order_id="ord_existing", customer_phone="7000000021", chef_phone="9876500021",
                                 kitchen_name="K", service_date=date(2026, 8, 4), status="PENDING_PAYMENT"))
    await db_session.flush()
    res = await _create_order(db_session, customer_phone="7000000021", kitchen="9876500021",
                              items=[{"dish_name": "Thali", "quantity": 1}], now=BEFORE_LUNCH)
    assert res["status"] == "ORDER_EXISTS"
    assert res["order_id"] == "ord_existing"


@pytest.mark.asyncio
async def test_create_order_invalid_item(db_session: AsyncSession):
    await _seed_customer_and_chef_with_dish(db_session, "7000000022", "9876500022")
    res = await _create_order(db_session, customer_phone="7000000022", kitchen="9876500022",
                              items=[{"dish_name": "Nonexistent Dish", "quantity": 1}], now=BEFORE_LUNCH)
    assert res["status"] == "INVALID_ITEM"


@pytest.mark.asyncio
async def test_create_order_chef_not_found(db_session: AsyncSession):
    db_session.add(CustomerProfile(customer_phone="7000000023", name="C", delivery_address="X", is_registered=True))
    await db_session.flush()
    res = await _create_order(db_session, customer_phone="7000000023", kitchen="No Such Kitchen",
                              items=[{"dish_name": "Thali", "quantity": 1}], now=BEFORE_LUNCH)
    assert res["status"] == "NOT_FOUND"


# ---- add_item_to_order ----

async def _seed_extra_dish(session, chef, dish_name, price="80.00"):
    dish = ChefMenuItem(chef_phone=chef, dish_name=dish_name, unit_price=Decimal(price),
                        meal_type="LUNCH", dietary_tag="VEG", spice_level="MEDIUM", is_available=True)
    session.add(dish)
    await session.flush()
    return dish


@pytest.mark.asyncio
async def test_add_item_adds_new_dish(db_session: AsyncSession):
    await _seed_customer_and_chef_with_dish(db_session, "7000000030", "9876500030")
    await _seed_extra_dish(db_session, "9876500030", "Raita")
    await _create_order(db_session, customer_phone="7000000030", kitchen="9876500030",
                        items=[{"dish_name": "Thali", "quantity": 1}], now=BEFORE_LUNCH)
    res = await _add_item_to_order(db_session, customer_phone="7000000030",
                                   items=[{"dish_name": "Raita", "quantity": 2}])
    assert res["status"] == "UPDATED"
    assert res["subtotal"] == 120.0 + 160.0     # Thali 120 + 2x Raita 160


@pytest.mark.asyncio
async def test_add_item_sets_quantity_not_increment(db_session: AsyncSession):
    await _seed_customer_and_chef_with_dish(db_session, "7000000031", "9876500031")
    await _create_order(db_session, customer_phone="7000000031", kitchen="9876500031",
                        items=[{"dish_name": "Thali", "quantity": 2}], now=BEFORE_LUNCH)
    # SET semantics: passing 3 makes it 3 total, not 2+3
    res = await _add_item_to_order(db_session, customer_phone="7000000031",
                                   items=[{"dish_name": "Thali", "quantity": 3}])
    assert res["status"] == "UPDATED"
    assert res["subtotal"] == 360.0             # 3 x 120, NOT 5 x 120


@pytest.mark.asyncio
async def test_add_item_no_active_order(db_session: AsyncSession):
    db_session.add(CustomerProfile(customer_phone="7000000032", name="C", delivery_address="X", is_registered=True))
    await db_session.flush()
    res = await _add_item_to_order(db_session, customer_phone="7000000032",
                                   items=[{"dish_name": "Thali", "quantity": 1}])
    assert res["status"] == "NO_ACTIVE_ORDER"
    assert "create_order" in res["message"]


@pytest.mark.asyncio
async def test_add_item_invalid_item(db_session: AsyncSession):
    await _seed_customer_and_chef_with_dish(db_session, "7000000033", "9876500033")
    await _create_order(db_session, customer_phone="7000000033", kitchen="9876500033",
                        items=[{"dish_name": "Thali", "quantity": 1}], now=BEFORE_LUNCH)
    res = await _add_item_to_order(db_session, customer_phone="7000000033",
                                   items=[{"dish_name": "Nonexistent Dish", "quantity": 1}])
    assert res["status"] == "INVALID_ITEM"


# ---- view_cart ----

@pytest.mark.asyncio
async def test_view_cart_empty(db_session: AsyncSession):
    db_session.add(CustomerProfile(customer_phone="7000000040", name="C", delivery_address="X", is_registered=True))
    await db_session.flush()
    res = await _view_cart(db_session, customer_phone="7000000040")
    assert res["status"] == "EMPTY"
    assert "create_order" in res["message"]


@pytest.mark.asyncio
async def test_view_cart_shows_items_and_totals(db_session: AsyncSession):
    await _seed_customer_and_chef_with_dish(db_session, "7000000041", "9876500041")
    await _create_order(db_session, customer_phone="7000000041", kitchen="9876500041",
                        items=[{"dish_name": "Thali", "quantity": 2}], now=BEFORE_LUNCH)
    res = await _view_cart(db_session, customer_phone="7000000041")
    assert res["status"] == "OK"
    assert res["items"][0]["quantity"] == 2
    assert res["subtotal"] == 240.0
    assert res["total"] == 240.0 + res["delivery_fee"]


# ---- request_payment + confirm_payment (Flow 4) ----

async def _seed_order_pending_payment(session, cust, chef):
    """Register customer + chef + one lunch dish, then create a PENDING_PAYMENT order."""
    await _seed_customer_and_chef_with_dish(session, cust, chef)
    res = await _create_order(session, customer_phone=cust, kitchen=chef,
                              items=[{"dish_name": "Thali", "quantity": 2}], now=BEFORE_LUNCH)
    return res["order_id"]


@pytest.mark.asyncio
async def test_request_payment_creates_pending_and_link(db_session: AsyncSession):
    order_id = await _seed_order_pending_payment(db_session, "7000000050", "9876500050")
    res = await _request_payment(db_session, customer_phone="7000000050")
    assert res["status"] == "AWAITING_PAYMENT"
    assert res["order_id"] == order_id
    assert res["link"]                                   # a payment link was minted
    pay = await db_session.get(CustomerPayment, res["payment_id"])
    assert pay is not None and pay.status == "PENDING"
    assert float(pay.amount_due) == res["amount"]


@pytest.mark.asyncio
async def test_request_payment_no_active_order(db_session: AsyncSession):
    db_session.add(CustomerProfile(customer_phone="7000000051", name="C", delivery_address="X", is_registered=True))
    await db_session.flush()
    res = await _request_payment(db_session, customer_phone="7000000051")
    assert res["status"] == "NO_ACTIVE_ORDER"


@pytest.mark.asyncio
async def test_confirm_payment_marks_paid_and_confirms_order(db_session: AsyncSession):
    order_id = await _seed_order_pending_payment(db_session, "7000000052", "9876500052")
    req = await _request_payment(db_session, customer_phone="7000000052")
    res = await _confirm_payment(db_session, payment_id=req["payment_id"], transaction_id="txn_test_1")
    assert res["status"] == "PAID"
    pay = await db_session.get(CustomerPayment, req["payment_id"])
    assert pay.status == "PAID"
    order = await db_session.get(CustomerOrder, order_id)
    assert order.status == "CONFIRMED"                   # DW2 -> DW1 cascade


@pytest.mark.asyncio
async def test_request_payment_not_payable_after_confirmed(db_session: AsyncSession):
    await _seed_order_pending_payment(db_session, "7000000053", "9876500053")
    req = await _request_payment(db_session, customer_phone="7000000053")
    await _confirm_payment(db_session, payment_id=req["payment_id"])
    # order is now CONFIRMED -> no active order to pay for
    res = await _request_payment(db_session, customer_phone="7000000053")
    assert res["status"] == "NO_ACTIVE_ORDER"
