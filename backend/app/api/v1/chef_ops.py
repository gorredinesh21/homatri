"""Chef kitchen REST: dashboard, menu, packed, cutoff lock."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.deps import require_role
from backend.app.core.ids import generate_id
from backend.app.db.session import get_db
from backend.app.models.chef import ChefDietaryRequest, ChefMenuItem, ChefProfile
from backend.app.models.customer import CustomerOrder, CustomerOrderItem, CustomerProfile
from backend.app.models.driver import DriverProfile
from backend.app.models.social import ChefContentProgress, ChefReel
from backend.app.models.system import SystemDeliveryRoute, SystemDeliveryStop, SystemDeliveryStopOrder
from backend.app.services.fulfillment import chef_earnings, mark_kitchen_packed
from backend.app.services.kitchens import committed_meals, menu_card, serialize_kitchen
from backend.app.services.meal_clock import active_window
from backend.app.tools.master_tools import _run_cutoff_batch

router = APIRouter(prefix="/chef", tags=["Chef"])


class MenuIn(BaseModel):
    dish_name: str
    description: Optional[str] = None
    unit_price: float = Field(gt=0)
    meal_type: str = "LUNCH"
    dietary_tag: Optional[str] = "VEG"
    is_available: bool = True
    image_url: Optional[str] = None


class MenuPatch(BaseModel):
    dish_name: Optional[str] = None
    description: Optional[str] = None
    unit_price: Optional[float] = None
    meal_type: Optional[str] = None
    dietary_tag: Optional[str] = None
    is_available: Optional[bool] = None
    image_url: Optional[str] = None


class KitchenPatch(BaseModel):
    kitchen_name: Optional[str] = None
    chef_name: Optional[str] = None
    address: Optional[str] = None
    hometown_region: Optional[str] = None
    daily_capacity: Optional[int] = Field(default=None, ge=1, le=200)
    bio: Optional[str] = None


class AcceptingIn(BaseModel):
    accepting: bool


def _chef_phone(payload: dict) -> str:
    return payload["sub"]


async def _require_chef(db: AsyncSession, phone: str) -> ChefProfile:
    chef = await db.get(ChefProfile, phone)
    if chef is None:
        raise HTTPException(status_code=404, detail="Finish homemaker onboarding first.")
    return chef


async def _batch_orders(db, chef_phone: str, meal_window: str, service_date: date):
    return (
        (
            await db.execute(
                select(CustomerOrder)
                .where(
                    CustomerOrder.chef_phone == chef_phone,
                    CustomerOrder.meal_window == meal_window,
                    CustomerOrder.service_date == service_date,
                    CustomerOrder.status.in_(
                        ("CONFIRMED", "BATCHED", "COOKING", "PACKED", "PICKED_UP", "DELIVERED")
                    ),
                )
                .order_by(CustomerOrder.created_at)
            )
        )
        .scalars()
        .all()
    )


@router.get("/me")
async def chef_dashboard(
    payload: dict = Depends(require_role("CHEF")),
    db: AsyncSession = Depends(get_db),
):
    phone = _chef_phone(payload)
    chef = await _require_chef(db, phone)
    window = active_window()
    sd = date.fromisoformat(window["service_date"])
    kitchen = await serialize_kitchen(db, chef)
    orders = await _batch_orders(db, phone, window["meal_window"], sd)
    serialized = []
    cook_map: dict[str, int] = {}
    rider = None
    packed = True if orders else False
    for order in orders:
        items = (
            (
                await db.execute(
                    select(CustomerOrderItem).where(CustomerOrderItem.order_id == order.order_id)
                )
            )
            .scalars()
            .all()
        )
        for it in items:
            cook_map[it.dish_name] = cook_map.get(it.dish_name, 0) + it.quantity
        serialized.append(
            {
                "orderId": order.order_id,
                "customerName": (await db.get(CustomerProfile, order.customer_phone)).name
                if await db.get(CustomerProfile, order.customer_phone)
                else order.customer_phone,
                "status": order.status,
                "notes": order.special_instructions,
                "items": [
                    {"label": it.dish_name, "quantity": it.quantity, "menuItemId": it.menu_item_id}
                    for it in items
                ],
            }
        )
        if order.status not in {"PACKED", "PICKED_UP", "DELIVERED"}:
            packed = False
        if rider is None:
            so = (
                await db.execute(
                    select(SystemDeliveryStopOrder).where(SystemDeliveryStopOrder.order_id == order.order_id)
                )
            ).scalars().first()
            if so:
                stop = await db.get(SystemDeliveryStop, so.stop_id)
                if stop:
                    route = await db.get(SystemDeliveryRoute, stop.route_id)
                    if route:
                        driver = await db.get(DriverProfile, route.driver_phone)
                        if driver:
                            rider = {
                                "riderName": driver.driver_name,
                                "vehicleNumber": driver.vehicle_number,
                                "phone": driver.driver_phone,
                            }
    menu_items = (
        (
            await db.execute(
                select(ChefMenuItem)
                .where(ChefMenuItem.chef_phone == phone)
                .order_by(ChefMenuItem.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    reels = (
        (
            await db.execute(
                select(ChefReel)
                .where(ChefReel.chef_phone == phone, ChefReel.deleted_at.is_(None))
                .order_by(ChefReel.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    used = await committed_meals(db, phone, sd, window["meal_window"])
    remaining = max(0, chef.daily_capacity - used)
    state = (
        "KITCHEN_PAUSED"
        if not chef.accepting_orders
        else ("CAPACITY_REACHED" if remaining <= 0 else "ACCEPTING_ORDERS")
    )
    return {
        "kitchen": {
            "kitchenName": chef.kitchen_name,
            "chefName": chef.chef_name,
            "address": chef.address,
            "hometownRegion": chef.hometown_region,
            "dailyCapacity": chef.daily_capacity,
            "fssaiLicenseNumber": chef.fssai_license_number,
            "acceptingOrders": chef.accepting_orders,
        },
        "publicKitchen": kitchen,
        "windowInfo": {
            "mealWindow": window["meal_window"],
            "cutoffTime": window["cutoff_time"],
            "label": window["label"],
        },
        "kitchenState": state,
        "committedMeals": used,
        "remainingCapacity": remaining,
        "isPackedReady": packed,
        "cook": {
            "totalMeals": sum(cook_map.values()),
            "summary": [{"label": k, "quantity": v} for k, v in cook_map.items()],
            "orders": serialized,
        },
        "orders": serialized,
        "menuItems": [
            {
                "menuItemId": m.menu_item_id,
                "itemName": m.dish_name,
                "description": m.description,
                "unitPrice": float(m.unit_price),
                "mealWindow": m.meal_type,
                "availability": "IN_STOCK" if m.is_available else "SOLD_OUT",
            }
            for m in menu_items
        ],
        "rider": rider,
        "earnings": await chef_earnings(db, phone),
        "reels": [
            {
                "reelId": str(r.reel_id),
                "caption": r.title,
                "likeCount": r.likes_count,
                "viewCount": r.view_count,
                "commentCount": r.comments_count,
                "videoUrl": r.video_url,
                "pendingSync": not r.published,
            }
            for r in reels
        ],
        "dietaryRequests": [
            {
                "requestId": r.request_id,
                "orderId": r.order_id,
                "customerPhone": r.customer_phone,
                "note": r.note,
                "status": r.status,
                "counterOffer": r.counter_offer,
                "counterTurnCount": r.turn,
                "maxCounterTurns": 2,
                "createdAt": r.created_at.isoformat() if r.created_at else None,
            }
            for r in (
                await db.execute(
                    select(ChefDietaryRequest)
                    .where(ChefDietaryRequest.chef_phone == phone)
                    .order_by(ChefDietaryRequest.created_at.desc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        ],
        "notice": None,
    }


class DietaryRespondIn(BaseModel):
    action: str = Field(pattern="^(accept|reject|counter)$")
    counter_offer: Optional[str] = None


@router.post("/me/dietary/{request_id}/respond")
async def respond_dietary(
    request_id: str,
    body: DietaryRespondIn,
    payload: dict = Depends(require_role("CHEF")),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime

    req = await db.get(ChefDietaryRequest, request_id)
    if req is None or req.chef_phone != payload["sub"]:
        raise HTTPException(status_code=404, detail="Dietary request not found.")
    if req.status not in {"WAITING_CHEF", "COUNTERED"}:
        raise HTTPException(status_code=409, detail=f"Request already {req.status}.")
    if body.action == "counter":
        if not body.counter_offer:
            raise HTTPException(status_code=400, detail="counter_offer required for a counter.")
        if req.turn >= 2:
            raise HTTPException(status_code=409, detail="Two counter rounds already used; accept or reject.")
        req.status = "COUNTERED"
        req.counter_offer = body.counter_offer
        req.turn += 1
    elif body.action == "accept":
        req.status = "ACCEPTED"
        req.resolved_at = datetime.now()
    else:
        req.status = "REJECTED"
        req.resolved_at = datetime.now()
    await db.commit()
    return {"requestId": request_id, "status": req.status, "counterOffer": req.counter_offer, "turn": req.turn}


@router.post("/me/accepting")
async def set_accepting(
    body: AcceptingIn,
    payload: dict = Depends(require_role("CHEF")),
    db: AsyncSession = Depends(get_db),
):
    chef = await _require_chef(db, payload["sub"])
    chef.accepting_orders = body.accepting
    await db.commit()
    return {"accepting_orders": chef.accepting_orders}


@router.post("/me/pause")
async def pause_kitchen(
    payload: dict = Depends(require_role("CHEF")),
    db: AsyncSession = Depends(get_db),
):
    chef = await _require_chef(db, payload["sub"])
    chef.accepting_orders = False
    await db.commit()
    return {"accepting_orders": False, "kitchenState": "KITCHEN_PAUSED"}


@router.post("/me/lock-batch")
async def lock_batch(
    payload: dict = Depends(require_role("CHEF")),
    db: AsyncSession = Depends(get_db),
):
    window = active_window()
    sd = date.fromisoformat(window["service_date"])
    result = await _run_cutoff_batch(db, window=window["meal_window"], service_date=sd)
    await db.commit()
    return result


@router.post("/me/packed")
async def packed(
    payload: dict = Depends(require_role("CHEF")),
    db: AsyncSession = Depends(get_db),
):
    window = active_window()
    sd = date.fromisoformat(window["service_date"])
    result = await mark_kitchen_packed(db, payload["sub"], window["meal_window"], sd)
    await db.commit()
    return result


@router.post("/me/menu")
async def create_menu(
    body: MenuIn,
    payload: dict = Depends(require_role("CHEF")),
    db: AsyncSession = Depends(get_db),
):
    await _require_chef(db, payload["sub"])
    meal = body.meal_type.upper()
    if meal not in {"LUNCH", "DINNER", "BOTH"}:
        raise HTTPException(status_code=400, detail="meal_type must be LUNCH, DINNER, or BOTH")
    item = ChefMenuItem(
        menu_item_id=generate_id("itm"),
        chef_phone=payload["sub"],
        dish_name=body.dish_name.strip(),
        description=body.description,
        unit_price=Decimal(str(body.unit_price)),
        meal_type=meal,
        dietary_tag=body.dietary_tag,
        is_available=body.is_available,
        image_url=body.image_url,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return menu_card(item)


@router.patch("/me/menu/{menu_item_id}")
async def patch_menu(
    menu_item_id: str,
    body: MenuPatch,
    payload: dict = Depends(require_role("CHEF")),
    db: AsyncSession = Depends(get_db),
):
    item = await db.get(ChefMenuItem, menu_item_id)
    if item is None or item.chef_phone != payload["sub"]:
        raise HTTPException(status_code=404, detail="Menu item not found")
    data = body.model_dump(exclude_unset=True)
    if "dish_name" in data:
        item.dish_name = data["dish_name"]
    if "description" in data:
        item.description = data["description"]
    if "unit_price" in data:
        item.unit_price = Decimal(str(data["unit_price"]))
    if "meal_type" in data:
        item.meal_type = data["meal_type"].upper()
    if "dietary_tag" in data:
        item.dietary_tag = data["dietary_tag"]
    if "is_available" in data:
        item.is_available = data["is_available"]
    if "image_url" in data:
        item.image_url = data["image_url"]
    await db.commit()
    return menu_card(item)


@router.patch("/me/menu/{menu_item_id}/stock")
async def toggle_stock(
    menu_item_id: str,
    payload: dict = Depends(require_role("CHEF")),
    db: AsyncSession = Depends(get_db),
):
    item = await db.get(ChefMenuItem, menu_item_id)
    if item is None or item.chef_phone != payload["sub"]:
        raise HTTPException(status_code=404, detail="Menu item not found")
    item.is_available = not item.is_available
    await db.commit()
    return menu_card(item)


@router.patch("/me/kitchen")
async def patch_kitchen(
    body: KitchenPatch,
    payload: dict = Depends(require_role("CHEF")),
    db: AsyncSession = Depends(get_db),
):
    chef = await _require_chef(db, payload["sub"])
    data = body.model_dump(exclude_unset=True)
    if "kitchen_name" in data:
        chef.kitchen_name = data["kitchen_name"]
    if "chef_name" in data:
        chef.chef_name = data["chef_name"]
    if "address" in data:
        chef.address = data["address"]
        chef.address_line1 = data["address"]
    if "hometown_region" in data:
        chef.hometown_region = data["hometown_region"]
    if "daily_capacity" in data:
        chef.daily_capacity = data["daily_capacity"]
    if "bio" in data:
        chef.bio = data["bio"]
        chef.kitchen_bio = data["bio"]
    await db.commit()
    return {"status": "ok"}


# ---------------- 30-day content challenge ----------------

class ContentDayPatch(BaseModel):
    completed: Optional[bool] = None
    scenes: Optional[list[bool]] = None


@router.get("/me/content-plan")
async def get_content_plan(
    payload: dict = Depends(require_role("CHEF")),
    db: AsyncSession = Depends(get_db),
):
    """Shot-list progress for the 30-day content challenge (one row per day touched)."""
    phone = _chef_phone(payload)
    rows = (
        await db.execute(
            select(ChefContentProgress).where(ChefContentProgress.chef_phone == phone)
        )
    ).scalars().all()
    return {
        "progress": [
            {
                "day": row.day,
                "completed": row.completed,
                "scenes": row.scene_states or {},
            }
            for row in rows
        ]
    }


@router.put("/me/content-plan/{day}")
async def put_content_day(
    day: int,
    body: ContentDayPatch,
    payload: dict = Depends(require_role("CHEF")),
    db: AsyncSession = Depends(get_db),
):
    phone = _chef_phone(payload)
    if not 1 <= day <= 31:
        raise HTTPException(status_code=400, detail="day must be 1..30")
    row = (
        await db.execute(
            select(ChefContentProgress).where(
                ChefContentProgress.chef_phone == phone, ChefContentProgress.day == day
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ChefContentProgress(chef_phone=phone, day=day)
        db.add(row)
    if body.completed is not None:
        row.completed = body.completed
    if body.scenes is not None:
        row.scene_states = {str(i): bool(done) for i, done in enumerate(body.scenes)}
    await db.commit()
    return {"day": day, "completed": row.completed, "scenes": row.scene_states or {}}
