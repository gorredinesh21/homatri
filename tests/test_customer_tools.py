"""Category 2 — Customer LLM tools integration tests (runs against PostgreSQL)."""

from __future__ import annotations

from decimal import Decimal
import pytest

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


async def test_unified_register_customer_profile_atomic_and_two_phase(db_session):
    # Scenario A: Atomic 1-step registration (text address + location pin provided at once)
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

    # Scenario B: 2-Phase registration (Step 1: text address -> Step 2: location pin attachment)
    p_step1 = await customer_tools.register_customer_profile(
        db_session,
        customer_phone="9444444444",
        name="Phase Customer",
        delivery_address="Flat 202, My Home Krishe",
    )
    assert p_step1.is_registered is False
    assert p_step1.latitude is None

    # Step 2: Attach location pin via unified tool
    p_step2 = await customer_tools.register_customer_profile(
        db_session,
        customer_phone="9444444444",
        latitude=17.4480,
        longitude=78.3810,
    )
    assert p_step2.is_registered is True
    assert float(p_step2.latitude) == 17.4480
