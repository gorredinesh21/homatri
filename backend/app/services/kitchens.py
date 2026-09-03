"""Serialize live kitchens for customer discovery (website + apps)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.chef import ChefMenuItem, ChefProfile
from backend.app.models.customer import CustomerOrder, CustomerOrderItem
from backend.app.models.social import ChefReel
from backend.app.services.meal_clock import active_window

COOKING_STATUSES = ("CONFIRMED", "BATCHED", "COOKING", "PACKED", "PICKED_UP")


def _float(value) -> float | None:
    if value is None:
        return None
    return float(value)


def _dietary_tags(chef: ChefProfile, items: list[ChefMenuItem]) -> list[str]:
    tags: set[str] = set()
    raw = (chef.dietary_type or "").upper()
    if "JAIN" in raw:
        tags.add("Jain")
    if "NON" in raw:
        tags.add("Non-Veg")
    if "VEG" in raw and "NON" not in raw:
        tags.add("100% Veg")
    for item in items:
        tag = (item.dietary_tag or "").upper()
        if tag == "JAIN":
            tags.add("Jain")
        elif tag in {"NON_VEG", "NONVEG", "EGG"}:
            tags.add("Non-Veg")
        elif tag in {"VEG", "PURE_VEG"}:
            tags.add("100% Veg")
    if not tags:
        tags.add("100% Veg")
    return sorted(tags)


def menu_card(item: ChefMenuItem) -> dict[str, Any]:
    return {
        "menuItemId": item.menu_item_id,
        "menu_item_id": item.menu_item_id,
        "itemName": item.dish_name,
        "dish_name": item.dish_name,
        "description": item.description,
        "price": float(item.unit_price),
        "unit_price": float(item.unit_price),
        "availability": "IN_STOCK" if item.is_available else "SOLD_OUT",
        "is_available": item.is_available,
        "mealWindow": item.meal_type,
        "meal_type": item.meal_type,
        "dietaryTag": item.dietary_tag,
        "imageUrl": item.image_url,
        "supportsCustomization": True,
    }


async def committed_meals(
    session: AsyncSession, chef_phone: str, service_date, meal_window: str | None = None
) -> int:
    stmt = (
        select(func.coalesce(func.sum(CustomerOrderItem.quantity), 0))
        .join(CustomerOrder, CustomerOrder.order_id == CustomerOrderItem.order_id)
        .where(
            CustomerOrder.chef_phone == chef_phone,
            CustomerOrder.service_date == service_date,
            CustomerOrder.status.in_(COOKING_STATUSES),
        )
    )
    if meal_window:
        stmt = stmt.where(CustomerOrder.meal_window == meal_window)
    return int((await session.execute(stmt)).scalar_one() or 0)


async def serialize_kitchen(
    session: AsyncSession,
    chef: ChefProfile,
    *,
    meal_window: str | None = None,
) -> dict[str, Any]:
    window = active_window()
    service_date = window["service_date"]
    from datetime import date as date_cls

    sd = date_cls.fromisoformat(service_date)
    items = (
        (
            await session.execute(
                select(ChefMenuItem)
                .where(ChefMenuItem.chef_phone == chef.chef_phone)
                .order_by(ChefMenuItem.meal_type, ChefMenuItem.dish_name)
            )
        )
        .scalars()
        .all()
    )
    if meal_window:
        visible = [i for i in items if i.meal_type in {meal_window, "BOTH"}]
    else:
        visible = items
    lunch = [menu_card(i) for i in visible if i.meal_type in {"LUNCH", "BOTH"}]
    dinner = [menu_card(i) for i in visible if i.meal_type in {"DINNER", "BOTH"}]
    prices = [float(i.unit_price) for i in visible if i.is_available]
    used = await committed_meals(session, chef.chef_phone, sd, meal_window or window["meal_window"])
    remaining = max(0, int(chef.daily_capacity) - used)
    serving = bool(chef.active_status and chef.accepting_orders and remaining > 0)
    reels = (
        (
            await session.execute(
                select(ChefReel)
                .where(
                    ChefReel.chef_phone == chef.chef_phone,
                    ChefReel.deleted_at.is_(None),
                    ChefReel.published.is_(True),
                )
                .order_by(ChefReel.created_at.desc())
                .limit(12)
            )
        )
        .scalars()
        .all()
    )
    locality = chef.apartment_or_locality or chef.city or "Ghansoli"
    fssai = chef.fssai_license_number or ""
    hygiene = []
    if fssai and fssai != "PENDING":
        hygiene.append(f"FSSAI registered {fssai}")
    if chef.is_verified:
        hygiene.append("Kitchen inspection verified")
    hygiene.append("Sealed tiffin packaging")
    return {
        "chefId": chef.chef_phone,
        "chef_phone": chef.chef_phone,
        "kitchenName": chef.kitchen_name,
        "kitchen_name": chef.kitchen_name,
        "chefName": chef.chef_name,
        "chef_name": chef.chef_name,
        "photoUrl": chef.profile_image_url or chef.avatar_url,
        "profileImageUrl": chef.profile_image_url or chef.avatar_url,
        "regionalIdentity": chef.hometown_region,
        "hometownRegion": chef.hometown_region,
        "cuisineSummary": chef.hometown_region,
        "rating": _float(chef.rating_average) or 0,
        "ratingCount": 0,
        "signatureDish": (visible[0].dish_name if visible else chef.kitchen_name),
        "pricePreview": min(prices) if prices else 0,
        "isCurrentlyServing": serving,
        "isVerified": bool(chef.is_verified),
        "dietaryTags": _dietary_tags(chef, items),
        "locality": locality,
        "serviceArea": locality,
        "acceptingOrders": bool(chef.accepting_orders and chef.active_status),
        "bio": chef.kitchen_bio or chef.bio,
        "hygieneBadges": hygiene,
        "fssaiLicenseNumber": fssai,
        "dailyCapacity": chef.daily_capacity,
        "committedMeals": used,
        "remainingCapacity": remaining,
        "address": chef.address,
        "latitude": _float(chef.latitude),
        "longitude": _float(chef.longitude),
        "lunchMenu": lunch,
        "dinnerMenu": dinner,
        "menuItems": [menu_card(i) for i in visible],
        "reels": [
            {
                "reelId": str(r.reel_id),
                "videoUrl": r.hls_playlist_url or r.video_url,
                "thumbnailUrl": r.thumbnail_url or r.video_url,
                "caption": r.title or r.dish_tag_name,
                "dishName": r.dish_tag_name,
                "dishPrice": float(r.dish_tag_price) if r.dish_tag_price is not None else None,
                "chefId": chef.chef_phone,
                "chefName": chef.chef_name,
                "kitchenName": chef.kitchen_name,
            }
            for r in reels
        ],
    }


async def list_kitchens(
    session: AsyncSession, *, cluster: str | None = None, meal_window: str | None = None
) -> list[dict[str, Any]]:
    q = select(ChefProfile).where(ChefProfile.active_status.is_(True), ChefProfile.deleted_at.is_(None))
    chefs = (await session.execute(q.order_by(ChefProfile.kitchen_name))).scalars().all()
    cluster_l = (cluster or "").strip().lower()
    out = []
    for chef in chefs:
        blob = " ".join(
            filter(
                None,
                [
                    chef.apartment_or_locality,
                    chef.city,
                    chef.address,
                    chef.address_line1,
                ],
            )
        ).lower()
        if cluster_l and cluster_l not in blob and cluster_l not in (chef.city or "").lower():
            # Ghansoli launch: include kitchens with no locality yet.
            if chef.apartment_or_locality:
                continue
        out.append(await serialize_kitchen(session, chef, meal_window=meal_window))
    return out
