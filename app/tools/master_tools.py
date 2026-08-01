"""Master Domain & System Shared LLM Tools (Category 4).

Encapsulates Master Orchestrator & System Shared Tools with Guard 2 Pre-Condition Assertions.
Tool 1: dispatch_whatsapp_outbound_message_tool (Write Executor #20, System Shared Outbound Messaging Tool).
Tool 2: get_master_kitchen_availability_summary_tool (Read-only, Same Domain).
Tool 3: get_master_order_pipeline_summary_tool (Read-only, Same Domain).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.executors.master import execute_conversation_message_insert, execute_outbound_whatsapp_enqueue
from app.models.chef import ChefDailyInventory, ChefMenuItem, ChefProfile
from app.models.customer import CustomerOrder
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
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
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


# =============================================================================
# TOOL 2: get_master_kitchen_availability_summary_tool
# =============================================================================
class GetMasterKitchenAvailabilityInput(BaseModel):
    service_date: Optional[str] = Field(
        default=None,
        description="Optional service date in ISO format YYYY-MM-DD (e.g. '2026-08-01')",
    )
    meal_window: Optional[str] = Field(
        default=None,
        description="Optional meal window: 'LUNCH' or 'DINNER'",
    )


async def get_master_kitchen_availability_summary(
    session: AsyncSession,
    *,
    service_date: str | None = None,
    meal_window: str | None = None,
) -> dict[str, Any]:
    """Retrieve platform-wide active kitchen, menu item, and inventory metrics with Guard 2 Assertions."""
    if meal_window:
        assert meal_window in {"LUNCH", "DINNER"}, f"Invalid meal window: '{meal_window}'. Must be LUNCH or DINNER"

    # 1. Query total and active kitchen counts
    stmt_total_chefs = select(func.count(ChefProfile.chef_phone))
    total_chefs = (await session.execute(stmt_total_chefs)).scalar_one() or 0

    stmt_active_chefs = select(func.count(ChefProfile.chef_phone)).where(ChefProfile.active_status.is_(True))
    active_chefs = (await session.execute(stmt_active_chefs)).scalar_one() or 0

    # 2. Query menu item count
    stmt_items = select(func.count(ChefMenuItem.menu_item_id)).where(ChefMenuItem.is_available.is_(True))
    if meal_window:
        stmt_items = stmt_items.where(ChefMenuItem.meal_type.in_([meal_window, "BOTH"]))
    active_menu_items = (await session.execute(stmt_items)).scalar_one() or 0

    # 3. Query inventory totals if service_date provided
    total_portions_capacity = 0
    remaining_portions_capacity = 0
    if service_date:
        date_obj = date.fromisoformat(service_date)
        stmt_inv = select(
            func.sum(ChefDailyInventory.allocated_quantity),
            func.sum(ChefDailyInventory.remaining_quantity),
        ).where(ChefDailyInventory.service_date == date_obj)
        if meal_window:
            stmt_inv = stmt_inv.where(ChefDailyInventory.meal_window == meal_window)

        res_inv = (await session.execute(stmt_inv)).first()
        if res_inv and res_inv[0] is not None:
            total_portions_capacity = int(res_inv[0])
            remaining_portions_capacity = int(res_inv[1])

    return {
        "total_kitchens": total_chefs,
        "active_kitchens": active_chefs,
        "active_menu_items": active_menu_items,
        "service_date": service_date,
        "meal_window": meal_window,
        "total_portions_capacity": total_portions_capacity,
        "remaining_portions_capacity": remaining_portions_capacity,
    }


@tool("get_master_kitchen_availability_summary_tool", args_schema=GetMasterKitchenAvailabilityInput)
async def get_master_kitchen_availability_summary_tool(
    service_date: Optional[str] = None,
    meal_window: Optional[str] = None,
) -> str:
    """Retrieve platform-wide active home kitchen metrics, dish counts, and daily portion capacity summaries."""
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        data = await get_master_kitchen_availability_summary(
            session,
            service_date=service_date,
            meal_window=meal_window,
        )

        filter_str = f" for {data['service_date'] or 'Today'} ({data['meal_window'] or 'All Windows'})"
        inv_str = (
            f"Portion Capacity: {data['remaining_portions_capacity']} / {data['total_portions_capacity']} portions remaining\n"
            if data["service_date"]
            else ""
        )

        return (
            f"Platform Kitchen Availability Summary{filter_str}:\n"
            f"Active Kitchens: {data['active_kitchens']} / {data['total_kitchens']} registered\n"
            f"Active Dishes Available: {data['active_menu_items']} items\n"
            f"{inv_str}"
        )


# =============================================================================
# TOOL 3: get_master_order_pipeline_summary_tool
# =============================================================================
class GetMasterOrderPipelineSummaryInput(BaseModel):
    service_date: Optional[str] = Field(
        default=None,
        description="Optional service date in ISO format YYYY-MM-DD (e.g. '2026-08-01')",
    )
    meal_window: Optional[str] = Field(
        default=None,
        description="Optional meal window: 'LUNCH' or 'DINNER'",
    )


async def get_master_order_pipeline_summary(
    session: AsyncSession,
    *,
    service_date: str | None = None,
    meal_window: str | None = None,
) -> dict[str, Any]:
    """Retrieve order volume breakdown and GMV revenue pipeline metrics with Guard 2 Assertions."""
    if meal_window:
        assert meal_window in {"LUNCH", "DINNER"}, f"Invalid meal window: '{meal_window}'. Must be LUNCH or DINNER"

    stmt = select(
        CustomerOrder.status,
        func.count(CustomerOrder.order_id),
        func.sum(CustomerOrder.total_amount),
    )

    if service_date:
        date_obj = date.fromisoformat(service_date)
        stmt = stmt.where(CustomerOrder.service_date == date_obj)
    if meal_window:
        stmt = stmt.where(CustomerOrder.meal_window == meal_window)

    stmt = stmt.group_by(CustomerOrder.status)
    rows = (await session.execute(stmt)).all()

    status_counts = {}
    total_pipeline_orders = 0
    total_pipeline_gmv = 0.0

    for status_name, count_val, gmv_val in rows:
        status_counts[status_name] = count_val
        total_pipeline_orders += count_val
        if gmv_val is not None:
            total_pipeline_gmv += float(gmv_val)

    return {
        "service_date": service_date,
        "meal_window": meal_window,
        "total_orders": total_pipeline_orders,
        "total_gmv": round(total_pipeline_gmv, 2),
        "by_status": status_counts,
    }


@tool("get_master_order_pipeline_summary_tool", args_schema=GetMasterOrderPipelineSummaryInput)
async def get_master_order_pipeline_summary_tool(
    service_date: Optional[str] = None,
    meal_window: Optional[str] = None,
) -> str:
    """Retrieve platform-wide order volume breakdown by status and gross merchandise value (GMV) revenue."""
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        data = await get_master_order_pipeline_summary(
            session,
            service_date=service_date,
            meal_window=meal_window,
        )

        filter_str = f" for {data['service_date'] or 'All Dates'} ({data['meal_window'] or 'All Windows'})"
        breakdown_text = "\n".join(f"  - {status}: {count} orders" for status, count in data["by_status"].items())

        return (
            f"Platform Order Pipeline Summary{filter_str}:\n"
            f"Total Orders: {data['total_orders']}\n"
            f"Total Pipeline GMV: ₹{data['total_gmv']:.2f}\n"
            f"Status Breakdown:\n{breakdown_text or '  - No orders in pipeline'}"
        )
