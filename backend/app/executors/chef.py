"""Chef domain write executors (Category 1)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.chef import ChefDailyInventory, ChefMenuItem, ChefOrderReadiness


async def execute_daily_capacity_upsert(
    session: AsyncSession,
    *,
    chef_phone: str,
    menu_item_id: str,
    service_date: date,
    meal_window: str,
    max_capacity: int,
    is_unlimited: bool = False,
    notes: str | None = None,
) -> ChefDailyInventory:
    """Executor #1 — set/override a dish's daily prep cap (idempotent upsert).

    Unique per (chef_phone, menu_item_id, service_date, meal_window).
    """
    existing = (
        await session.execute(
            select(ChefDailyInventory).where(
                ChefDailyInventory.chef_phone == chef_phone,
                ChefDailyInventory.menu_item_id == menu_item_id,
                ChefDailyInventory.service_date == service_date,
                ChefDailyInventory.meal_window == meal_window,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = ChefDailyInventory(
            chef_phone=chef_phone,
            menu_item_id=menu_item_id,
            service_date=service_date,
            meal_window=meal_window,
            max_capacity=max_capacity,
            is_unlimited=is_unlimited,
            notes=notes,
        )
        session.add(existing)
    else:
        existing.max_capacity = max_capacity
        existing.is_unlimited = is_unlimited
        existing.notes = notes

    await session.flush()
    return existing


async def execute_dish_stock_toggle(
    session: AsyncSession,
    *,
    menu_item_id: str,
    is_available: bool,
) -> ChefMenuItem:
    """Executor #2 — flip a dish IN/OUT of stock."""
    item = await session.get(ChefMenuItem, menu_item_id)
    if item is None:
        raise ValueError(f"menu_item_id not found: {menu_item_id}")
    item.is_available = is_available
    await session.flush()
    return item


async def execute_order_readiness_record(
    session: AsyncSession,
    *,
    order_id: str,
    chef_phone: str,
    box_count: int | None = None,
    special_packing_notes: str | None = None,
) -> ChefOrderReadiness:
    """Executor #3 — record food packed & ready (PACKED_READY + packed_at)."""
    readiness = ChefOrderReadiness(
        order_id=order_id,
        chef_phone=chef_phone,
        status="PACKED_READY",
        box_count=box_count,
        special_packing_notes=special_packing_notes,
    )
    session.add(readiness)
    await session.flush()
    return readiness
