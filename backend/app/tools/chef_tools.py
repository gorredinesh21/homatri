"""Chef-domain tools (Flow 6, Part A — standalone chef ops).

Same guard-then-guide pattern as the customer tools: a Pydantic input schema, an
inner `_fn(session, ...)` (unit-testable), and a `@tool` wrapper that opens a
session. Cross-domain writes (order status) go through `delegate_write`.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.session import SessionFactory, transaction
from backend.app.executors.chef import (
    execute_daily_capacity_upsert,
    execute_dish_stock_toggle,
    execute_order_readiness_record,
)
from backend.app.executors.master import execute_outbound_whatsapp_enqueue
from backend.app.models.chef import ChefMenuItem, ChefProfile
from backend.app.models.customer import CustomerOrder, CustomerOrderItem, CustomerProfile
from backend.app.models.system import SystemDeliveryRoute, SystemDeliveryStop, SystemDeliveryStopOrder
from backend.app.tools.customer_tools import _fuzzy_match
from backend.app.tools.delegate import delegate_write

ACTIVE_COOK_STATUSES = ("BATCHED", "COOKING")


async def _resolve_chef_dish(
    session: AsyncSession, *, chef_phone: str, ref: str
) -> tuple[ChefMenuItem | None, str | None]:
    """Typo-tolerant match of a dish name against THIS chef's menu (any window / availability)."""
    rows = (
        await session.execute(select(ChefMenuItem).where(ChefMenuItem.chef_phone == chef_phone))
    ).scalars().all()
    return _fuzzy_match(ref, list(rows), [lambda d: d.dish_name])


async def _driver_for_order(session: AsyncSession, order_id: str) -> str | None:
    """The driver assigned to an order's batch (via its delivery stop -> route)."""
    so = (
        await session.execute(
            select(SystemDeliveryStopOrder).where(SystemDeliveryStopOrder.order_id == order_id)
        )
    ).scalars().first()
    if so is None:
        return None
    stop = await session.get(SystemDeliveryStop, so.stop_id)
    if stop is None:
        return None
    route = await session.get(SystemDeliveryRoute, stop.route_id)
    return route.driver_phone if route is not None else None


# =============================================================================
# TOOL: get_chef_profile  (same-domain · READ)
# =============================================================================
class GetChefProfileInput(BaseModel):
    chef_phone: str = Field(..., description="Normalized 10-digit chef phone.")


async def _get_chef_profile(session: AsyncSession, *, chef_phone: str) -> dict[str, Any]:
    """Identify a chef by phone. Guards: no row -> NOT_FOUND."""
    chef = await session.get(ChefProfile, chef_phone)
    if chef is None:
        return {"status": "NOT_FOUND",
                "message": f"No kitchen registered for {chef_phone}. Chefs are onboarded by admin."}
    return {
        "status": "FOUND",
        "profile": {"chef_phone": chef.chef_phone, "kitchen_name": chef.kitchen_name,
                    "chef_name": chef.chef_name, "dietary_type": chef.dietary_type,
                    "active": chef.active_status},
        "message": (f"Kitchen {chef.kitchen_name} — chef {chef.chef_name} "
                    f"({'active' if chef.active_status else 'inactive'})."),
    }


@tool("get_chef_profile", args_schema=GetChefProfileInput)
async def get_chef_profile(chef_phone: str) -> str:
    """Identify a chef by phone on an inbound message."""
    async with SessionFactory() as session:
        return (await _get_chef_profile(session, chef_phone=chef_phone))["message"]


# =============================================================================
# TOOL: get_chef_batch  (same-domain · READ)
# =============================================================================
class GetChefBatchInput(BaseModel):
    chef_phone: str = Field(..., description="Normalized 10-digit chef phone.")
    window: str | None = Field(default=None, description="Optional 'LUNCH'/'DINNER' filter.")
    service_date: str | None = Field(default=None, description="Optional ISO date filter.")


async def _get_chef_batch(
    session: AsyncSession, *, chef_phone: str, window: str | None = None, service_date: date | None = None
) -> dict[str, Any]:
    """The chef's active cook queue (BATCHED/COOKING), order-wise + cook summary.

    Guard: nothing to cook -> NO_BATCH.
    """
    q = select(CustomerOrder).where(
        CustomerOrder.chef_phone == chef_phone,
        CustomerOrder.status.in_(ACTIVE_COOK_STATUSES),
    )
    if window:
        q = q.where(CustomerOrder.meal_window == window)
    if service_date:
        q = q.where(CustomerOrder.service_date == service_date)
    orders = (await session.execute(q.order_by(CustomerOrder.order_id))).scalars().all()
    if not orders:
        return {"status": "NO_BATCH", "message": "No locked batch to cook yet."}

    items = (
        await session.execute(
            select(CustomerOrderItem).where(
                CustomerOrderItem.order_id.in_([o.order_id for o in orders])
            )
        )
    ).scalars().all()
    by_order: dict[str, list] = defaultdict(list)
    summary: dict[str, int] = defaultdict(int)
    for it in items:
        by_order[it.order_id].append(it)
        summary[it.dish_name] += it.quantity

    orders_out, blocks = [], []
    for n, o in enumerate(orders, 1):
        cust = await session.get(CustomerProfile, o.customer_phone)
        name = cust.name if cust else o.customer_phone
        addr = cust.delivery_address if cust else ""
        its = [{"dish": it.dish_name, "qty": it.quantity, "notes": it.special_instructions}
               for it in by_order.get(o.order_id, [])]
        orders_out.append({"order_id": o.order_id, "customer_name": name, "address": addr, "items": its})
        lines = "\n".join(
            f"      • {it['qty']}× {it['dish']}" + (f"  — {it['notes']}" if it["notes"] else "")
            for it in its
        )
        blocks.append(f"  {n}. {name} · {o.order_id} [{o.status}]\n     📍 {addr}\n{lines}")

    summary_lines = "\n".join(f"  • {qty}× {name}" for name, qty in summary.items())
    msg = (
        f"🍳 Your batch — {len(orders)} order(s):\n\nORDERS:\n" + "\n".join(blocks) +
        f"\n\nCOOK SUMMARY:\n{summary_lines}"
    )
    return {"status": "OK", "orders": orders_out,
            "summary": [{"dish": d, "total_qty": q} for d, q in summary.items()], "message": msg}


@tool("get_chef_batch", args_schema=GetChefBatchInput)
async def get_chef_batch(chef_phone: str, window: str | None = None, service_date: str | None = None) -> str:
    """Show the chef's locked batch — each order with items + address, plus a cook summary."""
    async with SessionFactory() as session:
        res = await _get_chef_batch(
            session, chef_phone=chef_phone, window=window,
            service_date=date.fromisoformat(service_date) if service_date else None,
        )
        return res["message"]


# =============================================================================
# TOOL: toggle_dish_stock  (same-domain · WRITE)
# =============================================================================
class ToggleDishStockInput(BaseModel):
    chef_phone: str = Field(..., description="Normalized 10-digit chef phone.")
    dish: str = Field(..., description="Dish name (typo-tolerant) from the chef's own menu.")
    is_available: bool = Field(..., description="True = back in stock, False = out of stock.")


async def _toggle_dish_stock(
    session: AsyncSession, *, chef_phone: str, dish: str, is_available: bool
) -> dict[str, Any]:
    """Flip one of the chef's dishes in/out of stock. Guards: NOT_FOUND / AMBIGUOUS."""
    item, err = await _resolve_chef_dish(session, chef_phone=chef_phone, ref=dish)
    if err == "AMBIGUOUS":
        return {"status": "AMBIGUOUS", "message": f"Several of your dishes match '{dish}'. Which one?"}
    if item is None:
        return {"status": "NOT_FOUND", "message": f"You don't have a dish matching '{dish}'."}
    await execute_dish_stock_toggle(session, menu_item_id=item.menu_item_id, is_available=is_available)
    state = "back in stock ✅" if is_available else "out of stock ⛔"
    return {"status": "UPDATED", "message": f"'{item.dish_name}' marked {state}."}


@tool("toggle_dish_stock", args_schema=ToggleDishStockInput)
async def toggle_dish_stock(chef_phone: str, dish: str, is_available: bool) -> str:
    """Mark one of the chef's dishes in or out of stock (by dish name)."""
    async with transaction() as session:
        res = await _toggle_dish_stock(session, chef_phone=chef_phone, dish=dish, is_available=is_available)
        return res["message"]


# =============================================================================
# TOOL: set_daily_capacity  (same-domain · WRITE)
# =============================================================================
class SetDailyCapacityInput(BaseModel):
    chef_phone: str = Field(..., description="Normalized 10-digit chef phone.")
    dish: str = Field(..., description="Dish name (typo-tolerant) from the chef's own menu.")
    service_date: str = Field(..., description="ISO date, e.g. '2026-08-05'.")
    window: str = Field(..., description="'LUNCH' or 'DINNER'.")
    max_capacity: int = Field(..., description="Max portions for that dish/date/window.")
    is_unlimited: bool = Field(default=False, description="True to ignore the cap.")


async def _set_daily_capacity(
    session: AsyncSession, *, chef_phone: str, dish: str, service_date: date,
    window: str, max_capacity: int, is_unlimited: bool = False,
) -> dict[str, Any]:
    """Set a dish's daily prep cap. Guards: negative cap -> INVALID; unknown dish -> NOT_FOUND."""
    if max_capacity < 0:
        return {"status": "INVALID", "message": "Capacity can't be negative."}
    item, err = await _resolve_chef_dish(session, chef_phone=chef_phone, ref=dish)
    if err == "AMBIGUOUS":
        return {"status": "AMBIGUOUS", "message": f"Several of your dishes match '{dish}'. Which one?"}
    if item is None:
        return {"status": "NOT_FOUND", "message": f"You don't have a dish matching '{dish}'."}
    await execute_daily_capacity_upsert(
        session, chef_phone=chef_phone, menu_item_id=item.menu_item_id,
        service_date=service_date, meal_window=window, max_capacity=max_capacity, is_unlimited=is_unlimited,
    )
    cap = "unlimited" if is_unlimited else str(max_capacity)
    return {"status": "SET",
            "message": f"Capacity for '{item.dish_name}' ({window.lower()} {service_date}) set to {cap}."}


@tool("set_daily_capacity", args_schema=SetDailyCapacityInput)
async def set_daily_capacity(chef_phone: str, dish: str, service_date: str, window: str,
                             max_capacity: int, is_unlimited: bool = False) -> str:
    """Set how many portions of a dish the chef can make for a given date/window."""
    async with transaction() as session:
        res = await _set_daily_capacity(
            session, chef_phone=chef_phone, dish=dish, service_date=date.fromisoformat(service_date),
            window=window, max_capacity=max_capacity, is_unlimited=is_unlimited,
        )
        return res["message"]


# =============================================================================
# TOOL: mark_order_ready  (cross-domain · WRITE via delegate)
# =============================================================================
class MarkOrderReadyInput(BaseModel):
    chef_phone: str = Field(..., description="Normalized 10-digit chef phone.")
    order_id: str = Field(..., description="The order that's packed, e.g. 'ord_...'.")
    box_count: int | None = Field(default=None, description="Number of boxes packed.")
    notes: str | None = Field(default=None, description="Special packing notes.")


async def _mark_order_ready(
    session: AsyncSession, *, chef_phone: str, order_id: str,
    box_count: int | None = None, notes: str | None = None,
) -> dict[str, Any]:
    """Mark food packed -> order PACKED (via delegate_write) + notify the driver.

    Guards:
      - order not found / not this chef  -> NOT_FOUND / NOT_YOURS
      - already packed                   -> ALREADY_READY (idempotent)
      - order not in {BATCHED, COOKING}  -> NOT_COOKING
    """
    order = await session.get(CustomerOrder, order_id)
    if order is None:
        return {"status": "NOT_FOUND", "message": f"Order {order_id} not found."}
    if order.chef_phone != chef_phone:
        return {"status": "NOT_YOURS", "message": f"Order {order_id} isn't from your kitchen."}
    if order.status == "PACKED":
        return {"status": "ALREADY_READY", "message": f"Order {order_id} is already packed."}
    if order.status not in ACTIVE_COOK_STATUSES:
        return {"status": "NOT_COOKING", "message": f"Order {order_id} is {order.status.lower()} — not ready to pack."}

    # own-table write
    await execute_order_readiness_record(
        session, order_id=order_id, chef_phone=chef_phone, box_count=box_count, special_packing_notes=notes,
    )
    # cross-domain: BATCHED -> COOKING -> PACKED, gated + audited via delegate_write
    if order.status == "BATCHED":
        await delegate_write(session, requesting_role="CHEF", capability="ORDER_STATUS",
                             order_id=order_id, target_status="COOKING", actor_role="CHEF")
    await delegate_write(session, requesting_role="CHEF", capability="ORDER_STATUS",
                         order_id=order_id, target_status="PACKED", actor_role="CHEF")

    # best-effort driver notification (the full relay is Master.relay_order_ready_to_driver, later)
    driver_phone = await _driver_for_order(session, order_id)
    if driver_phone:
        await execute_outbound_whatsapp_enqueue(
            session, recipient_phone=driver_phone, recipient_role="DRIVER",
            message_text=f"📦 Order {order_id} is packed & ready for pickup at {order.kitchen_name}.",
            related_order_id=order_id,
        )
    box = f" ({box_count} box{'es' if (box_count or 0) > 1 else ''})" if box_count else ""
    notified = " Driver notified." if driver_phone else ""
    return {"status": "READY", "message": f"✅ Order {order_id} packed{box}.{notified}"}


@tool("mark_order_ready", args_schema=MarkOrderReadyInput)
async def mark_order_ready(chef_phone: str, order_id: str, box_count: int | None = None,
                           notes: str | None = None) -> str:
    """Mark an order packed & ready; moves it to PACKED and notifies the driver."""
    async with transaction() as session:
        res = await _mark_order_ready(session, chef_phone=chef_phone, order_id=order_id,
                                      box_count=box_count, notes=notes)
        return res["message"]
