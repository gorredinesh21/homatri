"""Integration test suite for trigger_hitl_escalation_tool."""

import pytest
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.shared import ConversationMessage
from app.models.system import SystemHitlSession, SystemOutboundQueue
from app.tools.master_tools import trigger_hitl_escalation


@pytest.mark.asyncio
async def test_trigger_hitl_escalation_success(db_session: AsyncSession):
    session = db_session

    # 1. Trigger HITL Escalation for Chef Dietary Approval
    hitl = await trigger_hitl_escalation(
        session,
        thread_id="thread_test_dietary_01",
        interrupt_type="DIETARY_APPROVAL",
        waiting_on_role="CHEF",
        waiting_on_phone="9876543210",
        prompt_message="Customer requested 3 extra rotis (No garlic) for ₹30. Reply YES to accept or send counter-offer.",
        related_order_id="ord_test_999",
        payload={"extra_rotis": 3, "no_garlic": True, "offered_price": 30},
    )

    # 2. Verify SystemHitlSession row
    assert hitl is not None
    assert hitl.thread_id == "thread_test_dietary_01"
    assert hitl.interrupt_type == "DIETARY_APPROVAL"
    assert hitl.waiting_on_role == "CHEF"
    assert hitl.waiting_on_phone == "9876543210"
    assert hitl.status == "WAITING"
    assert hitl.order_id == "ord_test_999"
    assert hitl.payload["extra_rotis"] == 3
    assert hitl.expires_at > datetime.now()

    # 3. Verify SystemOutboundQueue row (WhatsApp prompt enqueued)
    stmt_outbound = select(SystemOutboundQueue).where(
        SystemOutboundQueue.recipient_phone == "9876543210",
        SystemOutboundQueue.related_order_id == "ord_test_999",
    )
    outbound = (await session.execute(stmt_outbound)).scalar_one_or_none()
    assert outbound is not None
    assert outbound.recipient_role == "CHEF"
    assert "3 extra rotis" in outbound.message_text

    # 4. Verify ConversationMessage chat log row
    stmt_msg = select(ConversationMessage).where(
        ConversationMessage.phone == "9876543210",
        ConversationMessage.related_order_id == "ord_test_999",
    )
    msg = (await session.execute(stmt_msg)).scalar_one_or_none()
    assert msg is not None
    assert msg.direction == "OUTBOUND"
    assert msg.source == "HITL_SYSTEM"


@pytest.mark.asyncio
async def test_trigger_hitl_escalation_invalid_type_assertion(db_session: AsyncSession):
    session = db_session
    with pytest.raises(AssertionError, match="Invalid interrupt_type"):
        await trigger_hitl_escalation(
            session,
            thread_id="thread_fail",
            interrupt_type="INVALID_TYPE_FOO",
            waiting_on_role="CUSTOMER",
            waiting_on_phone="9123456789",
            prompt_message="Test prompt message",
        )
