"""Category 4 & Shared Runtime — Master / System executor integration tests (runs against PostgreSQL)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import pytest

from app.executors import master as master_exec
from app.models.chef import ChefProfile
from app.models.customer import CustomerOrder, CustomerProfile
from app.models.driver import DriverProfile
from app.models.system import SystemDeliveryRoute, SystemHitlSession, SystemMealWindow

CUSTOMER = "9111111111"
CHEF = "9876543210"
DRIVER = "9988776655"
SERVICE_DATE = date(2026, 7, 31)


async def _seed_entities(s) -> tuple[str, str]:
    # 1. Chef
    s.add(
        ChefProfile(
            chef_phone=CHEF,
            kitchen_name="Ramesh Kitchen",
            chef_name="Ramesh",
            address="Flat 402, Hitech City",
            latitude=Decimal("17.44829380"),
            longitude=Decimal("78.38148410"),
        )
    )
    # 2. Customer
    s.add(
        CustomerProfile(
            customer_phone=CUSTOMER,
            name="Dinesh",
            delivery_address="Flat 301, My Home Bhooja",
            apartment_name="My Home Bhooja",
            is_registered=True,
        )
    )
    # 3. Driver
    s.add(
        DriverProfile(
            driver_phone=DRIVER,
            driver_name="Raju Driver",
            vehicle_type="BIKE",
            vehicle_number="TS 09 EQ 1234",
            is_on_shift=True,
            active_status=True,
        )
    )
    await s.flush()

    # 4. Order
    order = CustomerOrder(
        order_id="ord_batch_001",
        customer_phone=CUSTOMER,
        chef_phone=CHEF,
        kitchen_name="Ramesh Kitchen",
        meal_window="LUNCH",
        service_date=SERVICE_DATE,
        status="CONFIRMED",
    )
    s.add(order)
    await s.flush()

    return (order.order_id, CHEF)


async def test_meal_window_lock_and_creation(db_session):
    now = datetime.now()
    window = await master_exec.execute_meal_window_lock_and_creation(
        db_session,
        service_date=SERVICE_DATE,
        meal_type="LUNCH",
        cutoff_at=now,
        status="OPEN",
    )
    assert window.window_id.startswith("win_")
    assert window.status == "OPEN"

    # Lock window
    locked_win = await master_exec.execute_meal_window_lock_and_creation(
        db_session,
        service_date=SERVICE_DATE,
        meal_type="LUNCH",
        cutoff_at=now,
        status="LOCKED_PROCESSING",
    )
    assert locked_win.status == "LOCKED_PROCESSING"
    assert locked_win.locked_at is not None


async def test_cutoff_batch_lock_and_routes_creation(db_session):
    order_id, _ = await _seed_entities(db_session)
    now = datetime.now()

    # 1. Create window
    win = await master_exec.execute_meal_window_lock_and_creation(
        db_session,
        service_date=SERVICE_DATE,
        meal_type="LUNCH",
        cutoff_at=now,
    )

    # 2. Create GCP delivery route & stops
    stops_data = [
        {
            "stop_type": "PICKUP_KITCHEN",
            "target_ref_id": CHEF,
            "location_name": "Ramesh Kitchen",
            "address": "Flat 402, Hitech City",
            "latitude": 17.4482,
            "longitude": 78.3814,
            "estimated_arrival": now,
            "order_ids": [order_id],
        },
        {
            "stop_type": "DROPOFF_GATE",
            "target_ref_id": "My Home Bhooja",
            "location_name": "My Home Bhooja Gate 2",
            "address": "Gate 2, Hitech City",
            "latitude": 17.4450,
            "longitude": 78.3800,
            "estimated_arrival": now,
            "order_ids": [order_id],
        },
    ]

    route = await master_exec.execute_cutoff_batch_lock_and_routes_creation(
        db_session,
        window_id=win.window_id,
        driver_phone=DRIVER,
        service_date=SERVICE_DATE,
        meal_window="LUNCH",
        total_stops=2,
        total_orders=1,
        total_distance_km=Decimal("3.20"),
        estimated_duration_mins=20,
        stops_data=stops_data,
    )

    assert route.route_id.startswith("rt_")
    assert route.status == "ASSIGNED"

    # Verify order was transitioned to BATCHED via Customer DW1!
    order = await db_session.get(CustomerOrder, order_id)
    assert order.status == "BATCHED"


async def test_hitl_session_create_or_resume(db_session):
    hitl = await master_exec.execute_hitl_session_create_or_resume(
        db_session,
        thread_id="9111111111",
        interrupt_type="AWAIT_LOCATION_PIN",
        waiting_on_role="CUSTOMER",
        waiting_on_phone=CUSTOMER,
        payload={"message": "Please send your WhatsApp location pin"},
        expires_in_mins=15,
    )
    assert hitl.session_id.startswith("hitl_")
    assert hitl.status == "WAITING"
    assert hitl.expires_at > datetime.now()


async def test_payment_webhook_idempotency_log(db_session):
    event_id = "evt_rzp_unique_001"

    # First event log -> new
    evt1, is_new1 = await master_exec.execute_payment_webhook_idempotency_log(
        db_session,
        gateway_event_id=event_id,
        event_type="payment.captured",
        raw_payload={"status": "captured"},
    )
    assert is_new1 is True
    assert evt1.gateway_event_id == event_id

    # Second event log with same event_id -> duplicate
    evt2, is_new2 = await master_exec.execute_payment_webhook_idempotency_log(
        db_session,
        gateway_event_id=event_id,
        event_type="payment.captured",
        raw_payload={"status": "captured"},
    )
    assert is_new2 is False
    assert evt2.event_id == evt1.event_id


async def test_conversation_message_insert(db_session):
    msg = await master_exec.execute_conversation_message_insert(
        db_session,
        phone=CUSTOMER,
        actor_role="CUSTOMER",
        direction="INBOUND",
        source="USER",
        message_text="Hi, what is for lunch today?",
        wa_message_id="wamid_123456789",
    )
    assert msg.message_id.startswith("msg_")
    assert msg.message_text == "Hi, what is for lunch today?"
    assert msg.direction == "INBOUND"
