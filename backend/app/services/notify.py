"""Queue WhatsApp (and live SSE) when an order moves."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.executors.master import execute_outbound_whatsapp_enqueue
from backend.app.models.customer import CustomerOrder
from backend.app.services.live_hub import hub


async def notify_order(
    session: AsyncSession,
    order: CustomerOrder,
    *,
    message: str,
    extra: dict | None = None,
) -> None:
    await execute_outbound_whatsapp_enqueue(
        session,
        recipient_phone=order.customer_phone,
        recipient_role="CUSTOMER",
        message_text=message,
        related_order_id=order.order_id,
    )
    await hub.publish_order(
        order.order_id,
        {
            "order_id": order.order_id,
            "order_status": order.status,
            "message": message,
            **(extra or {}),
        },
    )


async def notify_ops(session: AsyncSession, *, message: str, order_id: str | None = None) -> None:
    phone = (settings.ops_phone or "").strip()
    if not phone:
        return
    await execute_outbound_whatsapp_enqueue(
        session,
        recipient_phone=phone[-10:],
        recipient_role="ADMIN",
        message_text=message,
        related_order_id=order_id,
    )
