"""Homaatri Model Context Protocol (MCP) Server.

Exposes Homaatri kitchen tools via standard MCP (FastMCP) so LLMs and external clients
can inspect menus, manage orders, update addresses, and execute payments.
"""
from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.session import SessionLocal
from app.models.entities import Chef, MenuItem, User
from app.models.enums import UserRole
from app.payments.factory import get_payment_provider
from app.services import order_lifecycle as lc
from app.services.conversation import _new_payment_row, _pay_link
from app.services.order_parsing import OrderDraft, ResolvedItem

mcp = FastMCP("Homaatri Kitchens")


@mcp.tool()
async def list_menu() -> list[dict[str, Any]]:
    """List all available food items on the Homaatri menu with prices and descriptions."""
    async with SessionLocal() as session:
        items = (await session.execute(select(MenuItem))).scalars().all()
        return [
            {
                "id": str(item.id),
                "name": item.name,
                "price": item.price,
                "description": item.description,
            }
            for item in items
        ]


@mcp.tool()
async def get_order_status(order_code: str) -> dict[str, Any]:
    """Get the current status, items, total, and payment link for an order by order code."""
    async with SessionLocal() as session:
        order = await lc.get_order_by_code(session, order_code)
        if not order:
            return {"error": f"Order {order_code} not found."}
        return {
            "order_code": order.code,
            "customer_name": order.customer_name,
            "status": order.status.value,
            "total": order.total,
            "items": [
                {"name": item.name, "quantity": item.quantity, "line_total": item.line_total}
                for item in order.items
            ],
            "delivery_address": order.delivery_address,
            "payment_link": _pay_link(order),
        }


@mcp.tool()
async def create_order(
    customer_phone: str,
    items: list[dict[str, Any]],
    delivery_time: str = "ASAP",
) -> dict[str, Any]:
    """Create a new food order.
    
    items parameter format: [{"name": "Butter Roti", "quantity": 2}, {"name": "Dal Fry", "quantity": 1}]
    """
    async with SessionLocal() as session:
        user = (
            await session.execute(
                select(User).where(User.phone == customer_phone)
            )
        ).scalar_one_or_none()
        if not user:
            user = User(phone=customer_phone, name="Customer", role=UserRole.CUSTOMER)
            session.add(user)
            await session.flush()

        chef = (await session.execute(select(Chef))).scalars().first()
        if not chef:
            return {"error": "No chef available."}

        menu_items = (await session.execute(select(MenuItem))).scalars().all()
        menu_map = {mi.name.lower(): mi for mi in menu_items}

        resolved = []
        for req in items:
            name = str(req.get("name", "")).strip().lower()
            qty = int(req.get("quantity", 1))
            mi = menu_map.get(name)
            if mi:
                resolved.append(ResolvedItem(menu_item=mi, quantity=qty))

        if not resolved:
            return {"error": "None of the requested items were found on the menu."}

        draft = OrderDraft(
            customer_name=user.name,
            items=resolved,
            delivery_time=delivery_time,
        )
        order = await lc.create_order(session, user, chef, draft)

        pay = get_payment_provider()
        intent = await pay.create_payment(
            order_code=order.code, amount=order.total, currency="INR"
        )
        order.payment = _new_payment_row(order, intent)
        await session.commit()

        return {
            "status": "created",
            "order_code": order.code,
            "total": order.total,
            "payment_link": _pay_link(order),
        }


@mcp.tool()
async def update_order_address(order_code: str, address: str) -> dict[str, Any]:
    """Update the delivery address for an order."""
    async with SessionLocal() as session:
        order = await lc.get_order_by_code(session, order_code)
        if not order:
            return {"error": f"Order {order_code} not found."}
        order.delivery_address = address
        await session.commit()
        return {"status": "updated", "order_code": order.code, "address": address}


@mcp.tool()
async def simulate_payment(order_code: str) -> dict[str, Any]:
    """Simulate successful payment for an order by order code."""
    from app.api.simulator import sim_pay
    try:
        res = await sim_pay(order_code)
        return {"status": "success", "response": res}
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    mcp.run()
