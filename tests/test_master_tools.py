"""Category 4 — Master & Shared LLM tools integration tests (runs against PostgreSQL)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

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
