"""Build and broadcast the full world snapshot for the simulator UI.

The UI is a thin view over this snapshot; every state-changing action publishes
a fresh snapshot over the SSE bus so all three phones update live.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.entities import Chef, Driver, Order, User
from app.services.events import bus


def _order_dict(o: Order) -> dict[str, Any]:
    return {
        "id": str(o.id),
        "code": o.code,
        "status": o.status.value,
        "customer_name": o.customer_name,
        "delivery_address": o.delivery_address,
        "delivery_gps": o.delivery_gps,
        "requested_delivery_time": o.requested_delivery_time,
        "subtotal": o.subtotal,
        "delivery_fee": o.delivery_fee,
        "total": o.total,
        "items": [
            {"name": i.name, "quantity": i.quantity, "unit_price": i.unit_price,
             "line_total": i.line_total}
            for i in o.items
        ],
        "payment": None if not o.payment else {
            "provider": o.payment.provider,
            "status": o.payment.status.value,
            "amount": o.payment.amount,
        },
        "delivery": None if not o.delivery else {
            "status": o.delivery.status.value,
            "driver_id": str(o.delivery.driver_id) if o.delivery.driver_id else None,
            "route_url": o.delivery.route_url,
            "dropoff_gps": o.delivery.dropoff_gps,
        },
        "change_requests": [
            {"type": c.change_type.value, "status": c.status.value,
             "description": c.description}
            for c in o.change_requests
        ],
    }


async def build_state(session: AsyncSession) -> dict[str, Any]:
    orders = (
        await session.execute(
            select(Order)
            .options(
                selectinload(Order.items),
                selectinload(Order.payment),
                selectinload(Order.delivery),
                selectinload(Order.change_requests),
            )
            .order_by(Order.created_at.desc())
        )
    ).scalars().all()

    users = (await session.execute(select(User))).scalars().all()
    chef = (
        await session.execute(select(Chef).options(selectinload(Chef.menu_items), selectinload(Chef.user)))
    ).scalars().first()
    driver = (
        await session.execute(select(Driver).options(selectinload(Driver.user)))
    ).scalars().first()

    return {
        "users": [
            {"phone": u.phone, "name": u.name, "role": u.role.value} for u in users
        ],
        "chef": None if not chef else {
            "kitchen_name": chef.kitchen_name,
            "phone": chef.user.phone,
            "gps": chef.gps_coordinates,
            "menu": [
                {"name": m.name, "price": m.price, "description": m.description,
                 "available": m.available}
                for m in chef.menu_items
            ],
        },
        "driver": None if not driver else {
            "name": driver.user.name,
            "phone": driver.user.phone,
            "vehicle": driver.vehicle_type,
            "available": driver.is_available,
        },
        "orders": [_order_dict(o) for o in orders],
    }


async def publish_state(session: AsyncSession) -> None:
    state = await build_state(session)
    await bus.publish({"kind": "state", "state": state})
