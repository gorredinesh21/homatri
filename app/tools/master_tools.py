"""Master Domain & System Shared LLM Tools (Category 4).

Encapsulates Master Orchestrator & System Shared Tools with Guard 2 Pre-Condition Assertions.
Tool 1: dispatch_whatsapp_outbound_message_tool (Write Executor #20, System Shared Outbound Messaging Tool).
"""

from __future__ import annotations

from typing import Any, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.executors.master import execute_conversation_message_insert, execute_outbound_whatsapp_enqueue
from app.models.system import SystemOutboundQueue



# =============================================================================
# TOOL 1: dispatch_whatsapp_outbound_message_tool
# =============================================================================
class DispatchWhatsAppOutboundMessageInput(BaseModel):
    recipient_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of recipient (e.g. '9111111111')",
    )
    recipient_role: str = Field(
        ...,
        description="Role of recipient: 'CUSTOMER', 'CHEF', 'DRIVER', or 'SYSTEM'",
    )
    message_text: str = Field(
        ...,
        description="Outbound WhatsApp message content to send to the recipient",
    )
    related_order_id: Optional[str] = Field(
        default=None,
        description="Optional associated order ID (e.g. 'ord_123456')",
    )


async def dispatch_whatsapp_outbound_message(
    session: AsyncSession,
    *,
    recipient_phone: str,
    recipient_role: str,
    message_text: str,
    related_order_id: str | None = None,
) -> SystemOutboundQueue:
    """Enqueue an outbound WhatsApp message and log it to conversation_messages with Guard 2 Assertions."""
    assert recipient_phone and len(recipient_phone) >= 10, f"Invalid recipient phone number: {recipient_phone}"
    assert recipient_role in {"CUSTOMER", "CHEF", "DRIVER", "SYSTEM"}, f"Invalid recipient role: {recipient_role}"
    assert message_text and len(message_text.strip()) >= 1, "Outbound message text cannot be empty"

    # 1. Enqueue to System Outbound Queue for WhatsApp Cloud API Gateway
    outbound = await execute_outbound_whatsapp_enqueue(
        session,
        recipient_phone=recipient_phone,
        recipient_role=recipient_role,
        message_text=message_text.strip(),
        message_type="TEXT",
        related_order_id=related_order_id,
    )

    # 2. Log in Unified Chat Ledger (conversation_messages) for Context Assembler
    await execute_conversation_message_insert(
        session,
        phone=recipient_phone,
        actor_role=recipient_role,
        direction="OUTBOUND",
        source="AGENT_TOOL",
        message_text=message_text.strip(),
        related_order_id=related_order_id,
    )

    return outbound


@tool("dispatch_whatsapp_outbound_message_tool", args_schema=DispatchWhatsAppOutboundMessageInput)
async def dispatch_whatsapp_outbound_message_tool(
    recipient_phone: str,
    recipient_role: str,
    message_text: str,
    related_order_id: Optional[str] = None,
) -> str:
    """Dispatch an outbound WhatsApp message to any customer, chef, or driver and log it in the chat ledger."""
    from app.db.session import transaction

    async with transaction() as session:
        outbound = await dispatch_whatsapp_outbound_message(
            session,
            recipient_phone=recipient_phone,
            recipient_role=recipient_role,
            message_text=message_text,
            related_order_id=related_order_id,
        )
        return (
            f"Successfully queued outbound WhatsApp message [{outbound.message_id}] for {recipient_role} ({recipient_phone}):\n"
            f"\"{outbound.message_text}\""
        )
