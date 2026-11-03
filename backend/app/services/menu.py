"""Category-based menu helpers shared by chef/admin/customer surfaces + checkout.

One source of truth for how categories and dishes serialise and how the
pick-any-two combo price (Bhookh Buddies / Pocket Friendly Feast) is validated
and computed — the backend always re-derives the discount, the client only
displays it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.chef import (
    ChefCategoryAssignment,
    ChefMenuCategory,
    ChefMenuItem,
    ChefProfile,
)

STANDARD = "STANDARD"
PAIR_COMBO = "PAIR_COMBO"


def serialize_category(cat: ChefMenuCategory, *, chef_phone: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "categoryId": cat.category_id,
        "category_id": cat.category_id,
        "name": cat.name,
        "description": cat.description,
        "imageUrl": cat.image_url,
        "displayOrder": cat.display_order,
        "isActive": cat.is_active,
        "pricingType": cat.pricing_type,
        "comboDiscount": float(cat.combo_discount) if cat.combo_discount is not None else None,
        "comboServes": cat.combo_serves,
    }
    if chef_phone:
        data["chefPhone"] = chef_phone
    return data


async def assigned_categories(
    session: AsyncSession,
    chef_phone: str,
    *,
    include_inactive: bool = False,
) -> list[ChefMenuCategory]:
    """Categories available on a chef's menu, in display order (assignment order wins)."""
    stmt = (
        select(ChefMenuCategory)
        .join(
            ChefCategoryAssignment,
            ChefCategoryAssignment.category_id == ChefMenuCategory.category_id,
        )
        .where(ChefCategoryAssignment.chef_phone == chef_phone)
        .order_by(
            ChefCategoryAssignment.display_order,
            ChefMenuCategory.display_order,
            ChefMenuCategory.name,
        )
    )
    if not include_inactive:
        stmt = stmt.where(
            ChefCategoryAssignment.is_active.is_(True),
            ChefMenuCategory.is_active.is_(True),
        )
    return list((await session.execute(stmt)).scalars().all())


async def category_dish_counts(session: AsyncSession, chef_phone: str) -> dict[str, int]:
    """Live (non-archived) dish count per category for one chef."""
    rows = (
        await session.execute(
            select(ChefMenuItem.category_id, func.count())
            .where(
                ChefMenuItem.chef_phone == chef_phone,
                ChefMenuItem.is_archived.is_(False),
                ChefMenuItem.category_id.is_not(None),
            )
            .group_by(ChefMenuItem.category_id)
        )
    ).all()
    return {cid: int(n) for cid, n in rows}


async def require_assigned_category(
    session: AsyncSession, chef_phone: str, category_id: str
) -> ChefMenuCategory:
    """Load a category and assert it is assigned+active for this chef (ownership guard)."""
    cat = await session.get(ChefMenuCategory, category_id)
    if cat is None or not cat.is_active:
        raise ValueError("Category not found or inactive.")
    assignment = (
        await session.execute(
            select(ChefCategoryAssignment).where(
                ChefCategoryAssignment.chef_phone == chef_phone,
                ChefCategoryAssignment.category_id == category_id,
            )
        )
    ).scalar_one_or_none()
    if assignment is None or not assignment.is_active:
        raise ValueError("This category is not on your menu.")
    return cat


async def validate_combo_lines(
    session: AsyncSession,
    chef_phone: str,
    lines: list[dict[str, Any]],
    dishes_by_id: dict[str, ChefMenuItem],
) -> tuple[Decimal, dict[str, list[str]]]:
    """Validate combo-tagged cart lines and compute the total discount.

    `lines` = checkout items, each optionally carrying `combo_category_id`.
    Rules (Bhookh Buddies / Pocket Friendly Feast):
      - a combo group = all lines tagged with the same PAIR_COMBO category
      - each group must be exactly TWO meals (two distinct dishes, qty 1 each)
      - the two dishes must come from that chef's STANDARD categories (never the
        combo categories themselves)
      - discount = combo_discount per group, summed across groups

    Returns (total_discount, {combo_category_id: [menu_item_id, menu_item_id]}).
    Raises ValueError with a customer-readable message on any violation.
    """
    categories = {
        c.category_id: c
        for c in await assigned_categories(session, chef_phone, include_inactive=False)
    }
    groups: dict[str, list[dict[str, Any]]] = {}
    for line in lines:
        combo_cid = line.get("combo_category_id")
        if not combo_cid:
            continue
        cat = categories.get(combo_cid)
        if cat is None or cat.pricing_type != PAIR_COMBO:
            raise ValueError("That combo is not available at this kitchen.")
        dish = dishes_by_id.get(line["menu_item_id"])
        if dish is None:
            raise ValueError("Combo selection is not on the live menu.")
        dish_cat = categories.get(dish.category_id) if dish.category_id else None
        if dish_cat is None or dish_cat.pricing_type != STANDARD:
            raise ValueError("Combo meals must be regular menu dishes — pick two everyday dishes.")
        groups.setdefault(combo_cid, []).append(line)

    discount = Decimal("0.00")
    grouping: dict[str, list[str]] = {}
    for combo_cid, group in groups.items():
        cat = categories[combo_cid]
        total_qty = sum(int(g["quantity"]) for g in group)
        if len(group) != 2 or total_qty != 2 or any(int(g["quantity"]) != 1 for g in group):
            raise ValueError(
                f"{cat.name}: pick exactly two meals (one portion each) for the combo price."
            )
        ids = [g["menu_item_id"] for g in group]
        if ids[0] == ids[1]:
            raise ValueError(f"{cat.name}: pick two different meals for the combo price.")
        if cat.combo_discount is None:
            raise ValueError(f"{cat.name} has no combo price configured.")
        discount += Decimal(str(cat.combo_discount)).quantize(Decimal("0.01"))
        grouping[combo_cid] = ids
    return discount, grouping
