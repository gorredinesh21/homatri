"""Category 2 — Customer LLM tools integration tests (runs against PostgreSQL)."""

from __future__ import annotations

from decimal import Decimal
import pytest

from app.core.exceptions import LocationInterrupt
from app.models.customer import CustomerProfile
from app.tools import customer_tools

CUSTOMER = "9111111111"


async def test_get_customer_profile_registered_and_unregistered(db_session):
    # 1. Query unregistered phone -> returns None
    unreg = await customer_tools.get_customer_profile(db_session, customer_phone=CUSTOMER)
    assert unreg is None

    # 2. Seed registered customer
    db_session.add(
        CustomerProfile(
            customer_phone=CUSTOMER,
            name="Dinesh",
            delivery_address="Flat 301, My Home Bhooja",
            apartment_name="My Home Bhooja",
            latitude=Decimal("17.44500000"),
            longitude=Decimal("78.38000000"),
            is_registered=True,
        )
    )
    await db_session.flush()

    # 3. Query registered phone -> returns profile data
    reg = await customer_tools.get_customer_profile(db_session, customer_phone=CUSTOMER)
    assert reg is not None
    assert reg["name"] == "Dinesh"
    assert reg["is_registered"] is True
    assert reg["latitude"] == 17.445


async def test_register_customer_profile_with_direct_lat_lng(db_session):
    # Direct registration (name, address, lat, lng provided together)
    p_atomic = await customer_tools.register_customer_profile(
        db_session,
        customer_phone="9333333333",
        name="Atomic Customer",
        delivery_address="Flat 101, Golf Edge, Gachibowli",
        latitude=17.4400,
        longitude=78.3700,
    )
    assert p_atomic.is_registered is True
    assert float(p_atomic.latitude) == 17.4400


async def test_register_customer_profile_invokes_customer_agent(db_session):
    # When lat/lng are missing, register_customer_profile invokes Customer Agent inside function,
    # which enqueues WhatsApp prompt and raises LocationInterrupt to wait for location pin.
    with pytest.raises(LocationInterrupt) as exc_info:
        await customer_tools.register_customer_profile(
            db_session,
            customer_phone="9444444444",
            name="Interrupt Customer",
            delivery_address="Flat 202, My Home Krishe",
        )
    
    interrupt_payload = exc_info.value.payload
    assert interrupt_payload["interrupt_type"] == "AWAIT_LOCATION_PIN"
    assert interrupt_payload["customer_phone"] == "9444444444"
