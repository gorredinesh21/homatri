"""Integration test suite for Chef Tool 8: respond_to_custom_request_tool."""

import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chef import ChefProfile
from app.models.customer import CustomerProfile
from app.models.system import SystemAgentLog, SystemHitlSession, SystemOutboundQueue
from app.tools.chef_tools import respond_to_custom_request


@pytest.mark.asyncio
async def test_respond_to_custom_request_accepted(db_session: AsyncSession):
    session = db_session

    # 1. Seed Chef, Customer, and WAITING HITL Session
    chef = ChefProfile(
        chef_phone="9876543210",
        kitchen_name="Indravati Tiffins",
        chef_name="Chef Sunita",
        address="Sector 4, Ghansoli",
        latitude=Decimal("19.1190086"),
        longitude=Decimal("72.9934054"),
    )
    cust = CustomerProfile(
        customer_phone="9123456789",
        name="Ramesh Test",
        delivery_address="Indravati CHS, Ghansoli",
        latitude=Decimal("19.1214684"),
        longitude=Decimal("73.0036295"),
    )
    session.add_all([chef, cust])
    await session.flush()

    hitl = SystemHitlSession(
        session_id="hitl_custom_test_01",
        thread_id="thread_custom_req_9123456789",
        interrupt_type="CUSTOM_DISH_REQUEST",
        waiting_on_role="CHEF",
        waiting_on_phone="9876543210",
        payload={"customer_phone": "9123456789", "requested_dish": "Jain Paneer Tikka"},
        status="WAITING",
        expires_at=datetime.now() + timedelta(minutes=15),
    )
    session.add(hitl)
    await session.flush()

    # 2. Respond with ACCEPTED Decision
    res = await respond_to_custom_request(
        session,
        hitl_session_id=hitl.session_id,
        chef_phone=chef.chef_phone,
        decision="ACCEPTED",
        custom_dish_name="Jain Paneer Tikka",
        custom_dish_price=Decimal("240.00"),
        chef_message="Will prepare special fresh Jain gravy with no garlic/onion",
    )

    # 3. Verify HITL Session RESOLVED
    assert res["status"] == "RESOLVED"
    assert res["decision"] == "ACCEPTED"
    assert res["custom_dish_price"] == Decimal("240.00")

    await session.refresh(hitl)
    assert hitl.status == "RESOLVED"
    assert hitl.resolved_at is not None

    # 4. Verify Customer WhatsApp Notification Enqueued
    stmt_out = select(SystemOutboundQueue).where(SystemOutboundQueue.recipient_phone == cust.customer_phone)
    outbound = (await session.execute(stmt_out)).scalar_one_or_none()
    assert outbound is not None
    assert "CUSTOM DISH ACCEPTED" in outbound.message_text
    assert "₹240.00" in outbound.message_text
