"""Category 4 — Master & Shared LLM tools integration tests (runs against PostgreSQL)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import pytest
from sqlalchemy import select

from app.models.chef import ChefProfile
from app.models.customer import CustomerOrder, CustomerProfile
from app.models.shared import ConversationMessage
from app.models.system import SystemOutboundQueue
from app.tools import master_tools


async def test_dispatch_whatsapp_outbound_message_tool_success_and_assertions(db_session):
    # 1. Enqueue outbound message via master tool
    outbound = await master_tools.dispatch_whatsapp_outbound_message(
        db_session,
        recipient_phone="9111111111",
        recipient_role="CUSTOMER",
        message_text="Hi Dinesh, your order ord_123 has been confirmed!",
        related_order_id="ord_123",
    )
    assert outbound.status == "QUEUED"
    assert outbound.recipient_phone == "9111111111"

    # 2. Verify entry in conversation_messages (Unified Chat Ledger)
    msg = (
        await db_session.execute(
            select(ConversationMessage).where(
                ConversationMessage.phone == "9111111111",
                ConversationMessage.direction == "OUTBOUND",
            )
        )
    ).scalar_one_or_none()
    assert msg is not None
    assert msg.message_text == "Hi Dinesh, your order ord_123 has been confirmed!"

    # 3. Guard 2 Assertion: Empty message text raises AssertionError
    with pytest.raises(AssertionError):
        await master_tools.dispatch_whatsapp_outbound_message(
            db_session,
            recipient_phone="9111111111",
            recipient_role="CUSTOMER",
            message_text="",  # Empty
        )


async def test_get_master_kitchen_availability_summary(db_session):
    # Seed 1 active chef and 1 inactive chef
    db_session.add(
        ChefProfile(
            chef_phone="9876543210",
            kitchen_name="Ramesh Kitchen",
            chef_name="Ramesh",
            address="Flat 402, Hitech City",
            latitude=Decimal("17.44800000"),
            longitude=Decimal("78.38100000"),
            active_status=True,
        )
    )
    db_session.add(
        ChefProfile(
            chef_phone="9876543211",
            kitchen_name="Inactive Kitchen",
            chef_name="Sita",
            address="Kukatpally",
            latitude=Decimal("17.50000000"),
            longitude=Decimal("78.40000000"),
            active_status=False,
        )
    )
    await db_session.flush()

    summary = await master_tools.get_master_kitchen_availability_summary(db_session)
    assert summary["total_kitchens"] == 2
    assert summary["active_kitchens"] == 1


async def test_get_master_order_pipeline_summary(db_session):
    db_session.add(
        CustomerProfile(
            customer_phone="9111111111",
            name="Dinesh",
            delivery_address="Flat 301, My Home Bhooja",
            is_registered=True,
        )
    )
    db_session.add(
        ChefProfile(
            chef_phone="9876543210",
            kitchen_name="Ramesh Kitchen",
            chef_name="Ramesh",
            address="Flat 402, Hitech City",
            latitude=Decimal("17.44800000"),
            longitude=Decimal("78.38100000"),
            active_status=True,
        )
    )
    await db_session.flush()

    db_session.add(
        CustomerOrder(
            order_id="ord_pipe_001",
            customer_phone="9111111111",
            chef_phone="9876543210",
            kitchen_name="Ramesh Kitchen",
            meal_window="LUNCH",
            service_date=date(2026, 8, 1),
            status="CONFIRMED",
            cart_subtotal=Decimal("360.00"),
            total_amount=Decimal("390.00"),
        )
    )
    await db_session.flush()

    pipeline = await master_tools.get_master_order_pipeline_summary(
        db_session,
        service_date="2026-08-01",
        meal_window="LUNCH",
    )
    assert pipeline["total_orders"] == 1
    assert pipeline["total_gmv"] == 390.0
    assert pipeline["by_status"]["CONFIRMED"] == 1
