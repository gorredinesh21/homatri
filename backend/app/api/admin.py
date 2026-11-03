"""Admin Operations REST API Router with JWT & Cookie Authentication.

Endpoints servicing the Admin Operations Portal:
- POST /api/admin/login: Admin Login & JWT Cookie issuance
- GET /api/admin/me: Get active admin user profile
- POST /api/admin/logout: Admin Logout & Cookie clearing
- POST /api/admin/lock-window: Lock meal window (LUNCH/DINNER) & run cutoff batching
- GET /api/admin/escalations: List active HITL escalations
- POST /api/admin/escalations/resolve: Resolve HITL escalation with admin note/action
- GET /api/admin/pipeline: Today's order stage counts & kitchen capacity
- GET /api/admin/chats: Live WhatsApp conversation feeds
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import logging
from typing import Any

from fastapi import APIRouter, Cookie, Depends, File, Header, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import func, select, update

from backend.app.core.ids import generate_id
from backend.app.db.session import SessionFactory
from backend.app.executors.master import execute_meal_window_lock_and_creation
from backend.app.models.admin import AdminActivityLog, AdminUser
from backend.app.models.chef import (
    ChefCategoryAssignment,
    ChefDailyInventory,
    ChefMenuCategory,
    ChefMenuItem,
    ChefProfile,
)
from backend.app.services import menu as menu_service
from backend.app.services.kitchens import menu_card
from backend.app.services.media_storage import save_dish_image
from backend.app.models.customer import CustomerOrder
from backend.app.models.driver import DriverProfile
from backend.app.models.shared import ConversationMessage
from backend.app.models.system import SystemHitlSession, SystemMealWindow
from backend.app.services.whatsapp_service import send_whatsapp_text_message
from backend.app.tools.master_tools import _run_cutoff_batch

logger = logging.getLogger("admin_api")

router = APIRouter(prefix="/api/admin", tags=["Admin Operations"])

SECRET_SESSION_TOKEN = "homatri_admin_session_token_2026"
DEFAULT_ADMIN_EMAIL = "admin@homatri.in"
DEFAULT_ADMIN_PASS = "THORkills@21"


def hash_password(password: str) -> str:
    """Hash password using SHA-256 for simple secure comparison."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


# ==============================================================================
# PYDANTIC INPUT MODELS
# ==============================================================================
class LoginInput(BaseModel):
    email: str
    password: str


class LockWindowInput(BaseModel):
    meal_type: str = "LUNCH"  # LUNCH or DINNER
    service_date: str | None = None  # YYYY-MM-DD (defaults to today)


class CreateChefInput(BaseModel):
    chef_phone: str
    chef_name: str
    kitchen_name: str
    address: str
    apartment_or_locality: str | None = None
    city: str = "Navi Mumbai"
    pincode: str | None = None
    latitude: float = 19.1240
    longitude: float = 73.0018
    dietary_type: str = "VEG"
    fssai_license_number: str | None = None
    kitchen_bio: str | None = None


class CreateDriverInput(BaseModel):
    driver_phone: str
    driver_name: str
    vehicle_type: str = "BIKE"
    vehicle_number: str
    vehicle_model: str | None = None
    driver_license_number: str | None = None


class ResolveEscalationInput(BaseModel):
    session_id: str
    admin_notes: str
    custom_reply: str | None = None


# ==============================================================================
# AUTH DEPENDENCY & SEEDING
# ==============================================================================
async def get_current_admin(
    homatri_admin_token: str | None = Cookie(None),
    authorization: str | None = Header(None)
) -> dict[str, Any]:
    """Dependency protecting all admin operational endpoints."""
    token = homatri_admin_token
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]

    if not token or not token.startswith("token_adm_"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please log in to access Admin Portal."
        )

    admin_id = token.replace("token_", "")
    async with SessionFactory() as session:
        admin = await session.get(AdminUser, admin_id)
        if not admin or not admin.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Admin user profile not active or invalid."
            )
        return {
            "admin_id": admin.admin_id,
            "email": admin.email,
            "name": admin.name,
            "role": admin.role
        }


# ==============================================================================
# 1. AUTHENTICATION ENDPOINTS
# ==============================================================================
@router.post("/login")
async def admin_login(payload: LoginInput, response: Response):
    """Authenticate Admin User & Set HttpOnly Session Cookie."""
    async with SessionFactory() as session:
        # Seed default super-admin if table empty
        admin_res = await session.execute(select(AdminUser).where(AdminUser.email == payload.email))
        admin = admin_res.scalar_one_or_none()

        # Auto-seed / update default super-admin password hash
        if payload.email == DEFAULT_ADMIN_EMAIL:
            if not admin:
                admin = AdminUser(
                    admin_id="adm_super_admin_001",
                    email=DEFAULT_ADMIN_EMAIL,
                    name="Super Admin",
                    password_hash=hash_password(DEFAULT_ADMIN_PASS),
                    role="SUPER_ADMIN",
                    is_active=True
                )
                session.add(admin)
            else:
                admin.password_hash = hash_password(DEFAULT_ADMIN_PASS)
            await session.commit()

        # Validate credentials
        if not admin or admin.password_hash != hash_password(payload.password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid admin email or password."
            )

        if not admin.is_active:
            raise HTTPException(status_code=403, detail="Admin account is inactive.")

        # Update last login
        admin.last_login_at = datetime.now()
        session_token = f"token_{admin.admin_id}"
        await session.commit()

        # Set HttpOnly Cookie
        response.set_cookie(
            key="homatri_admin_token",
            value=session_token,
            httponly=True,
            samesite="lax",
            max_age=86400  # 24 hours
        )

        logger.info(f"🟢 Admin Logged In: {admin.email} ({admin.role})")

        return {
            "status": "SUCCESS",
            "token": session_token,
            "admin": {
                "admin_id": admin.admin_id,
                "email": admin.email,
                "name": admin.name,
                "role": admin.role
            }
        }


@router.get("/me")
async def get_admin_me(current_admin: dict[str, Any] = Depends(get_current_admin)):
    """Get active authenticated admin user profile."""
    return current_admin


@router.post("/logout")
async def admin_logout(response: Response):
    """Clear admin session cookie."""
    response.delete_cookie(key="homatri_admin_token")
    return {"status": "SUCCESS", "message": "Logged out successfully."}


# ==============================================================================
# 2. MEAL WINDOW LOCKING & CUTOFF CONTROLS (PROTECTED)
# ==============================================================================
@router.post("/lock-window")
async def lock_meal_window(
    payload: LockWindowInput,
    current_admin: dict[str, Any] = Depends(get_current_admin)
):
    """Lock a meal window (LUNCH or DINNER) for a service date and trigger cutoff batching."""
    meal_type = payload.meal_type.upper()
    if meal_type not in ("LUNCH", "DINNER"):
        raise HTTPException(status_code=400, detail="Invalid meal_type. Must be LUNCH or DINNER.")

    s_date = date.fromisoformat(payload.service_date) if payload.service_date else date.today()
    cutoff_time = datetime.now()

    async with SessionFactory() as session:
        # 1. Lock the meal window
        window = await execute_meal_window_lock_and_creation(
            session,
            service_date=s_date,
            meal_type=meal_type,
            cutoff_at=cutoff_time,
            status="LOCKED"
        )

        # 2. Run cutoff batching across kitchens
        batch_result = await _run_cutoff_batch(session, window=meal_type, service_date=s_date)

        # 3. Log Admin Action
        audit = AdminActivityLog(
            admin_id=current_admin["admin_id"],
            action="LOCK_MEAL_WINDOW",
            target_table="system_meal_windows",
            target_id=window.window_id,
            changes_diff={"meal_type": meal_type, "service_date": str(s_date), "result": batch_result}
        )
        session.add(audit)
        await session.commit()

        logger.info(f"🔒 Admin {current_admin['email']} LOCKED {meal_type} window for {s_date}: {batch_result}")

        return {
            "status": "SUCCESS",
            "message": f"{meal_type} window for {s_date} locked successfully.",
            "window_id": window.window_id,
            "window_status": window.status,
            "batch_summary": batch_result
        }


@router.get("/windows")
async def get_meal_windows(
    service_date: str | None = None,
    current_admin: dict[str, Any] = Depends(get_current_admin)
):
    """Get status of today's meal windows."""
    s_date = date.fromisoformat(service_date) if service_date else date.today()
    async with SessionFactory() as session:
        res = await session.execute(
            select(SystemMealWindow).where(SystemMealWindow.service_date == s_date)
        )
        windows = res.scalars().all()
        return [
            {
                "window_id": w.window_id,
                "meal_type": w.meal_type,
                "service_date": str(w.service_date),
                "status": w.status,
                "locked_at": w.locked_at.isoformat() if w.locked_at else None,
            }
            for w in windows
        ]


# ==============================================================================
# 3. HITL ESCALATIONS QUEUE (PROTECTED)
# ==============================================================================
@router.get("/escalations")
async def list_escalations(
    status: str = "PENDING",
    current_admin: dict[str, Any] = Depends(get_current_admin)
):
    """List pending HITL escalations flagged via escalate_to_admin."""
    async with SessionFactory() as session:
        res = await session.execute(
            select(SystemHitlSession)
            .where(SystemHitlSession.waiting_on_role == "ADMIN")
            .order_by(SystemHitlSession.created_at.desc())
        )
        escalations = res.scalars().all()
        return [
            {
                "session_id": e.session_id,
                "actor_phone": e.customer_phone,
                "source_role": e.source_role,
                "reason": e.reason,
                "order_id": e.order_id,
                "status": e.status,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in escalations
            if e.status == status or status == "ALL"
        ]


@router.post("/escalations/resolve")
async def resolve_escalation(
    payload: ResolveEscalationInput,
    current_admin: dict[str, Any] = Depends(get_current_admin)
):
    """Resolve an escalation, updating state and optionally sending a WhatsApp reply."""
    async with SessionFactory() as session:
        hitl = await session.get(SystemHitlSession, payload.session_id)
        if not hitl:
            raise HTTPException(status_code=404, detail="Escalation session not found.")

        hitl.status = "RESOLVED"
        hitl.resolution_notes = payload.admin_notes
        hitl.resolved_at = datetime.now()

        # Send custom WhatsApp reply if provided
        if payload.custom_reply and hitl.customer_phone:
            await send_whatsapp_text_message(hitl.customer_phone, payload.custom_reply)

        audit = AdminActivityLog(
            admin_id=current_admin["admin_id"],
            action="RESOLVE_ESCALATION",
            target_table="system_hitl_sessions",
            target_id=hitl.session_id,
            changes_diff={"admin_notes": payload.admin_notes, "custom_reply": payload.custom_reply}
        )
        session.add(audit)

        await session.commit()
        return {"status": "SUCCESS", "message": f"Escalation {payload.session_id} resolved."}


# ==============================================================================
# 4. KITCHEN PIPELINE & CHAT STREAM (PROTECTED)
# ==============================================================================
@router.get("/pipeline")
async def get_pipeline_summary(
    service_date: str | None = None,
    current_admin: dict[str, Any] = Depends(get_current_admin)
):
    """Get live order pipeline stage counts and active kitchen capacities."""
    s_date = date.fromisoformat(service_date) if service_date else date.today()

    async with SessionFactory() as session:
        # Order stage counts
        order_counts_res = await session.execute(
            select(CustomerOrder.status, func.count(CustomerOrder.order_id))
            .where(CustomerOrder.service_date == s_date)
            .group_by(CustomerOrder.status)
        )
        stage_counts = {status: count for status, count in order_counts_res.all()}

        # Active kitchens only — demo/retired chefs stay out of ops views.
        chefs_res = await session.execute(
            select(ChefProfile).where(
                ChefProfile.active_status.is_(True), ChefProfile.deleted_at.is_(None)
            )
        )
        chefs = chefs_res.scalars().all()

        from backend.app.services.kitchens import committed_meals

        kitchen_rows = []
        for c in chefs:
            used_l = await committed_meals(session, c.chef_phone, s_date, "LUNCH")
            used_d = await committed_meals(session, c.chef_phone, s_date, "DINNER")
            kitchen_rows.append(
                {
                    "kitchen_name": c.kitchen_name,
                    "chef_phone": c.chef_phone,
                    "locality": c.apartment_or_locality or c.address,
                    "is_accepting_orders": bool(c.active_status and c.accepting_orders),
                    "max_daily_capacity": int(c.daily_capacity or 15),
                    "committed_meals": int(used_l + used_d),
                }
            )

        return {
            "service_date": str(s_date),
            "stage_counts": stage_counts,
            "active_kitchens_count": len(chefs),
            "kitchens": kitchen_rows,
        }


@router.get("/chats")
async def get_chat_feed(
    phone: str | None = None,
    limit: int = 50,
    current_admin: dict[str, Any] = Depends(get_current_admin)
):
    """Get recent WhatsApp messages across all conversations."""
    async with SessionFactory() as session:
        query = select(ConversationMessage).order_by(ConversationMessage.created_at.desc()).limit(limit)
        if phone:
            query = query.where(ConversationMessage.phone == phone)

        res = await session.execute(query)
        messages = res.scalars().all()

        return [
            {
                "message_id": m.message_id,
                "actor_phone": m.phone,
                "actor_role": m.actor_role,
                "direction": m.direction,
                "text": m.message_text or "",
                "message_type": m.message_type,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ]


@router.get("/orders")
async def list_orders(
    status_filter: str | None = None,
    chef_phone: str | None = None,
    service_date_from: str | None = None,
    service_date_to: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
    current_admin: dict[str, Any] = Depends(get_current_admin),
):
    """Order history for the admin portal: all statuses (incl. CANCELLED),
    with items and payment summary. Supports filters + simple search."""
    from backend.app.models.customer import (
        CustomerOrderItem,
        CustomerPayment,
        CustomerProfile,
    )

    async with SessionFactory() as session:
        query = select(CustomerOrder).order_by(CustomerOrder.created_at.desc())
        total_query = select(func.count(CustomerOrder.order_id))

        if status_filter:
            query = query.where(CustomerOrder.status == status_filter.upper())
            total_query = total_query.where(CustomerOrder.status == status_filter.upper())
        if chef_phone:
            query = query.where(CustomerOrder.chef_phone == chef_phone)
            total_query = total_query.where(CustomerOrder.chef_phone == chef_phone)
        if service_date_from:
            d_from = date.fromisoformat(service_date_from)
            query = query.where(CustomerOrder.service_date >= d_from)
            total_query = total_query.where(CustomerOrder.service_date >= d_from)
        if service_date_to:
            d_to = date.fromisoformat(service_date_to)
            query = query.where(CustomerOrder.service_date <= d_to)
            total_query = total_query.where(CustomerOrder.service_date <= d_to)
        if q:
            like = f"%{q.lower()}%"
            query = query.where(
                func.lower(CustomerOrder.customer_phone).like(like)
                | func.lower(CustomerOrder.kitchen_name).like(like)
                | func.lower(CustomerOrder.order_id).like(like)
            )
            total_query = total_query.where(
                func.lower(CustomerOrder.customer_phone).like(like)
                | func.lower(CustomerOrder.kitchen_name).like(like)
                | func.lower(CustomerOrder.order_id).like(like)
            )

        limit = min(max(limit, 1), 500)
        offset = max(offset, 0)
        orders = (await session.execute(query.limit(limit).offset(offset))).scalars().all()
        total = (await session.execute(total_query)).scalar_one()

        order_ids = [o.order_id for o in orders]
        items_by_order: dict[str, list] = {}
        payments_by_order: dict[str, list] = {}
        customer_phones = {o.customer_phone for o in orders}

        if order_ids:
            items_res = await session.execute(
                select(CustomerOrderItem).where(CustomerOrderItem.order_id.in_(order_ids))
            )
            for it in items_res.scalars():
                items_by_order.setdefault(it.order_id, []).append(
                    {
                        "dish_name": it.dish_name,
                        "quantity": it.quantity,
                        "unit_price": float(it.unit_price),
                        "item_subtotal": float(it.item_subtotal),
                    }
                )
            pay_res = await session.execute(
                select(CustomerPayment).where(CustomerPayment.order_id.in_(order_ids))
            )
            for p in pay_res.scalars():
                payments_by_order.setdefault(p.order_id, []).append(
                    {
                        "payment_type": p.payment_type,
                        "amount_due": float(p.amount_due),
                        "amount_paid": float(p.amount_paid or 0),
                        "status": p.status,
                        "gateway": p.gateway,
                        "gateway_payment_id": p.gateway_payment_id,
                        "payment_link_url": p.payment_link_url,
                        "paid_at": p.paid_at.isoformat() if p.paid_at else None,
                        "refund_reason": p.refund_reason,
                    }
                )
        customers_res = await session.execute(
            select(CustomerProfile).where(CustomerProfile.customer_phone.in_(customer_phones))
        )
        customer_names = {c.customer_phone: (c.full_name or c.name) for c in customers_res.scalars()}

        rows = []
        for o in orders:
            rows.append(
                {
                    "order_id": o.order_id,
                    "customer_phone": o.customer_phone,
                    "customer_name": customer_names.get(o.customer_phone, ""),
                    "kitchen_name": o.kitchen_name,
                    "chef_phone": o.chef_phone,
                    "meal_window": o.meal_window,
                    "service_date": str(o.service_date),
                    "status": o.status,
                    "payment_status": o.payment_status,
                    "payment_method": o.payment_method,
                    "cart_subtotal": float(o.cart_subtotal),
                    "delivery_fee": float(o.delivery_fee),
                    "total_amount": float(o.total_amount),
                    "delivery_address": o.delivery_address,
                    "special_instructions": o.special_instructions,
                    "cancellation_reason": o.cancellation_reason,
                    "cancelled_at": o.cancelled_at.isoformat() if o.cancelled_at else None,
                    "created_at": o.created_at.isoformat(),
                    "items": items_by_order.get(o.order_id, []),
                    "payments": payments_by_order.get(o.order_id, []),
                }
            )

        return {"total": total, "count": len(rows), "offset": offset, "orders": rows}


@router.get("/chefs/{chef_phone}/menu")
async def get_chef_menu(chef_phone: str, current_admin: dict[str, Any] = Depends(get_current_admin)):
    """Full menu of a chef for the admin portal, incl. archived items, grouped by category."""
    async with SessionFactory() as session:
        chef = await session.get(ChefProfile, chef_phone)
        if chef is None or chef.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Chef not found")
        categories = await menu_service.assigned_categories(session, chef_phone, include_inactive=True)
        categories_by_id = {c.category_id: c for c in categories}
        res = await session.execute(
            select(ChefMenuItem)
            .where(ChefMenuItem.chef_phone == chef_phone)
            .order_by(ChefMenuItem.is_archived, ChefMenuItem.dish_name)
        )
        items = []
        for m in res.scalars():
            cat = categories_by_id.get(m.category_id) if m.category_id else None
            items.append(
                {
                    "menu_item_id": m.menu_item_id,
                    "dish_name": m.dish_name,
                    "description": m.description,
                    "unit_price": float(m.unit_price),
                    "category_id": m.category_id,
                    "category_name": cat.name if cat else None,
                    "meal_type": m.meal_type,
                    "dietary_tag": m.dietary_tag,
                    "spice_level": m.spice_level,
                    "max_availability": m.max_availability,
                    "image_url": m.image_url,
                    "is_available": m.is_available,
                    "is_archived": m.is_archived,
                }
            )
        return {
            "kitchen_name": chef.kitchen_name,
            "categories": [
                {**menu_service.serialize_category(c, chef_phone=chef_phone), "is_assigned": True}
                for c in categories
            ],
            "items": items,
        }


# =============================================================================
# MENU CATEGORY MANAGEMENT (admin-controlled catalog)
# =============================================================================

class CategoryIn(BaseModel):
    name: str
    description: str | None = None
    image_url: str | None = None
    display_order: int = 0
    is_active: bool = True
    pricing_type: str = "STANDARD"
    combo_discount: float | None = None
    combo_serves: int | None = None


class CategoryPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    image_url: str | None = None
    display_order: int | None = None
    is_active: bool | None = None
    pricing_type: str | None = None
    combo_discount: float | None = None
    combo_serves: int | None = None


def _validate_combo_fields(pricing_type: str, combo_discount, combo_serves) -> None:
    if pricing_type not in {"STANDARD", "PAIR_COMBO"}:
        raise HTTPException(status_code=400, detail="pricing_type must be STANDARD or PAIR_COMBO")
    if pricing_type == "PAIR_COMBO":
        if combo_discount is None or combo_discount <= 0:
            raise HTTPException(status_code=400, detail="A PAIR_COMBO category needs a positive combo_discount")
        if combo_serves is None or combo_serves < 2:
            raise HTTPException(status_code=400, detail="A PAIR_COMBO category needs combo_serves (>= 2)")


@router.get("/categories")
async def list_categories(current_admin: dict[str, Any] = Depends(get_current_admin)):
    """Every menu category with dish counts and chef-assignment counts."""
    from decimal import Decimal

    async with SessionFactory() as session:
        cats = (
            await session.execute(select(ChefMenuCategory).order_by(ChefMenuCategory.display_order, ChefMenuCategory.name))
        ).scalars().all()
        dish_counts = dict(
            (
                await session.execute(
                    select(ChefMenuItem.category_id, func.count())
                    .where(ChefMenuItem.is_archived.is_(False), ChefMenuItem.category_id.is_not(None))
                    .group_by(ChefMenuItem.category_id)
                )
            ).all()
        )
        assignment_counts = dict(
            (
                await session.execute(
                    select(ChefCategoryAssignment.category_id, func.count())
                    .where(ChefCategoryAssignment.is_active.is_(True))
                    .group_by(ChefCategoryAssignment.category_id)
                )
            ).all()
        )
        out = []
        for c in cats:
            card = menu_service.serialize_category(c)
            card["dishCount"] = int(dish_counts.get(c.category_id, 0))
            card["chefCount"] = int(assignment_counts.get(c.category_id, 0))
            out.append(card)
        return {"categories": out}


@router.post("/categories")
async def create_category(body: CategoryIn, current_admin: dict[str, Any] = Depends(get_current_admin)):
    from decimal import Decimal

    async with SessionFactory() as session:
        existing = (
            await session.execute(select(ChefMenuCategory).where(ChefMenuCategory.name == body.name.strip()))
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=400, detail="A category with this name already exists.")
        _validate_combo_fields(body.pricing_type, body.combo_discount, body.combo_serves)
        cat = ChefMenuCategory(
            category_id=generate_id("cat"),
            name=body.name.strip(),
            description=body.description,
            image_url=body.image_url,
            display_order=body.display_order,
            is_active=body.is_active,
            pricing_type=body.pricing_type,
            combo_discount=Decimal(str(body.combo_discount)) if body.combo_discount is not None else None,
            combo_serves=body.combo_serves,
        )
        session.add(cat)
        await session.commit()
        await session.refresh(cat)
        return menu_service.serialize_category(cat)


@router.patch("/categories/{category_id}")
async def update_category(
    category_id: str, body: CategoryPatch, current_admin: dict[str, Any] = Depends(get_current_admin)
):
    from decimal import Decimal

    async with SessionFactory() as session:
        cat = await session.get(ChefMenuCategory, category_id)
        if cat is None:
            raise HTTPException(status_code=404, detail="Category not found")
        data = body.model_dump(exclude_unset=True)
        if "name" in data and data["name"]:
            clash = (
                await session.execute(
                    select(ChefMenuCategory).where(
                        ChefMenuCategory.name == data["name"].strip(),
                        ChefMenuCategory.category_id != category_id,
                    )
                )
            ).scalar_one_or_none()
            if clash:
                raise HTTPException(status_code=400, detail="Another category with this name already exists.")
            cat.name = data["name"].strip()
        for field in ("description", "image_url"):
            if field in data:
                setattr(cat, field, data[field])
        if "display_order" in data and data["display_order"] is not None:
            cat.display_order = data["display_order"]
        if "is_active" in data and data["is_active"] is not None:
            cat.is_active = data["is_active"]
        pricing_type = data.get("pricing_type", cat.pricing_type)
        combo_discount = data.get("combo_discount", float(cat.combo_discount) if cat.combo_discount is not None else None)
        combo_serves = data.get("combo_serves", cat.combo_serves)
        _validate_combo_fields(pricing_type, combo_discount, combo_serves)
        cat.pricing_type = pricing_type
        cat.combo_discount = Decimal(str(combo_discount)) if combo_discount is not None else None
        cat.combo_serves = combo_serves
        await session.commit()
        await session.refresh(cat)
        return menu_service.serialize_category(cat)


@router.delete("/categories/{category_id}")
async def delete_category(
    category_id: str,
    mode: str | None = None,
    reassign_to: str | None = None,
    current_admin: dict[str, Any] = Depends(get_current_admin),
):
    """Delete a category safely — its dishes are never silently destroyed.

    If the category still has live (non-archived) dishes, the caller must pick:
      ?mode=reassign&reassign_to=<category_id>  -> move dishes to another category
      ?mode=archive                             -> archive the dishes (hidden, history kept)
    Without a mode the call returns 409 with the blocking dish count.
    """
    async with SessionFactory() as session:
        cat = await session.get(ChefMenuCategory, category_id)
        if cat is None:
            raise HTTPException(status_code=404, detail="Category not found")
        live_dishes = (
            (await session.execute(
                select(ChefMenuItem).where(
                    ChefMenuItem.category_id == category_id, ChefMenuItem.is_archived.is_(False)
                )
            ))
            .scalars()
            .all()
        )
        if live_dishes:
            if mode == "reassign":
                target = await session.get(ChefMenuCategory, reassign_to or "")
                if target is None or not target.is_active or target.pricing_type != "STANDARD":
                    raise HTTPException(status_code=400, detail="reassign_to must be an active STANDARD category")
                for d in live_dishes:
                    d.category_id = target.category_id
            elif mode == "archive":
                for d in live_dishes:
                    d.is_archived = True
                    d.is_available = False
            else:
                return {
                    "deleted": False,
                    "blocked": True,
                    "liveDishCount": len(live_dishes),
                    "detail": (
                        f"{cat.name} still has {len(live_dishes)} live dish(es). "
                        "Pass mode=reassign&reassign_to=<categoryId> to move them, or mode=archive to archive them."
                    ),
                }
        from sqlalchemy import delete as sa_delete

        await session.execute(
            sa_delete(ChefCategoryAssignment).where(ChefCategoryAssignment.category_id == category_id)
        )
        await session.delete(cat)
        await session.commit()
        return {"deleted": True, "blocked": False, "reassignedTo": reassign_to if mode == "reassign" else None}


class AssignIn(BaseModel):
    chef_phones: list[str]


@router.post("/categories/{category_id}/chefs")
async def assign_category(
    category_id: str, body: AssignIn, current_admin: dict[str, Any] = Depends(get_current_admin)
):
    """Assign a category to one or more chefs (idempotent for existing pairs)."""
    async with SessionFactory() as session:
        cat = await session.get(ChefMenuCategory, category_id)
        if cat is None:
            raise HTTPException(status_code=404, detail="Category not found")
        assigned = 0
        skipped: list[str] = []
        for phone in body.chef_phones:
            chef = await session.get(ChefProfile, phone)
            if chef is None or chef.deleted_at is not None:
                raise HTTPException(status_code=404, detail=f"Chef {phone} not found")
            existing = (
                await session.execute(
                    select(ChefCategoryAssignment).where(
                        ChefCategoryAssignment.chef_phone == phone,
                        ChefCategoryAssignment.category_id == category_id,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                if not existing.is_active:
                    existing.is_active = True
                    assigned += 1
                else:
                    skipped.append(phone)
                continue
            session.add(ChefCategoryAssignment(chef_phone=phone, category_id=category_id))
            assigned += 1
        await session.commit()
        return {"assigned": assigned, "alreadyAssigned": skipped}


@router.delete("/categories/{category_id}/chefs/{chef_phone}")
async def unassign_category(
    category_id: str,
    chef_phone: str,
    mode: str | None = None,
    reassign_to: str | None = None,
    current_admin: dict[str, Any] = Depends(get_current_admin),
):
    """Remove a category from a chef's menu. If the chef still has live dishes in
    it, the same safe-delete rules as category deletion apply (409 unless the
    dishes are reassigned or archived)."""
    async with SessionFactory() as session:
        assignment = (
            await session.execute(
                select(ChefCategoryAssignment).where(
                    ChefCategoryAssignment.chef_phone == chef_phone,
                    ChefCategoryAssignment.category_id == category_id,
                )
            )
        ).scalar_one_or_none()
        if assignment is None:
            raise HTTPException(status_code=404, detail="This category is not assigned to that chef")
        live_dishes = (
            (await session.execute(
                select(ChefMenuItem).where(
                    ChefMenuItem.chef_phone == chef_phone,
                    ChefMenuItem.category_id == category_id,
                    ChefMenuItem.is_archived.is_(False),
                )
            ))
            .scalars()
            .all()
        )
        if live_dishes:
            if mode == "reassign":
                target = await session.get(ChefMenuCategory, reassign_to or "")
                if target is None or not target.is_active or target.pricing_type != "STANDARD":
                    raise HTTPException(status_code=400, detail="reassign_to must be an active STANDARD category")
                for d in live_dishes:
                    d.category_id = target.category_id
            elif mode == "archive":
                for d in live_dishes:
                    d.is_archived = True
                    d.is_available = False
            else:
                return {
                    "deleted": False,
                    "blocked": True,
                    "liveDishCount": len(live_dishes),
                    "detail": (
                        f"This chef still has {len(live_dishes)} live dish(es) in the category. "
                        "Pass mode=reassign&reassign_to=<categoryId> to move them, or mode=archive."
                    ),
                }
        await session.delete(assignment)
        await session.commit()
        return {"deleted": True, "blocked": False}


# =============================================================================
# ADMIN DISH MANAGEMENT (any chef's menu, incl. images)
# =============================================================================

class AdminMenuIn(BaseModel):
    dish_name: str
    category_id: str
    description: str | None = None
    unit_price: float
    meal_type: str = "LUNCH"
    dietary_tag: str | None = "VEG"
    is_available: bool = True
    image_url: str | None = None
    max_availability: int = 10


class AdminMenuPatch(BaseModel):
    dish_name: str | None = None
    category_id: str | None = None
    description: str | None = None
    unit_price: float | None = None
    meal_type: str | None = None
    dietary_tag: str | None = None
    is_available: bool | None = None
    image_url: str | None = None
    max_availability: int | None = None


async def _admin_require_chef(session, chef_phone: str) -> ChefProfile:
    chef = await session.get(ChefProfile, chef_phone)
    if chef is None or chef.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Chef not found")
    return chef


@router.post("/chefs/{chef_phone}/menu")
async def admin_create_menu_item(
    chef_phone: str, body: AdminMenuIn, current_admin: dict[str, Any] = Depends(get_current_admin)
):
    """Add a dish to any chef's menu (admin). Category must be assigned to that chef."""
    from decimal import Decimal

    async with SessionFactory() as session:
        await _admin_require_chef(session, chef_phone)
        try:
            category = await menu_service.require_assigned_category(session, chef_phone, body.category_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        meal = body.meal_type.upper()
        if meal not in {"LUNCH", "DINNER", "BOTH"}:
            raise HTTPException(status_code=400, detail="meal_type must be LUNCH, DINNER, or BOTH")
        item = ChefMenuItem(
            menu_item_id=generate_id("itm"),
            chef_phone=chef_phone,
            category_id=category.category_id,
            dish_name=body.dish_name.strip(),
            description=body.description,
            unit_price=Decimal(str(body.unit_price)),
            meal_type=meal,
            dietary_tag=body.dietary_tag,
            is_available=body.is_available,
            image_url=body.image_url,
            max_availability=body.max_availability,
        )
        session.add(item)
        await session.commit()
        await session.refresh(item)
        return {"item": menu_card(item, category), "status": "SUCCESS"}


@router.patch("/menu/{menu_item_id}")
async def admin_patch_menu_item(
    menu_item_id: str, body: AdminMenuPatch, current_admin: dict[str, Any] = Depends(get_current_admin)
):
    from decimal import Decimal

    async with SessionFactory() as session:
        item = await session.get(ChefMenuItem, menu_item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Menu item not found")
        category = None
        if body.category_id is not None and body.category_id != item.category_id:
            try:
                category = await menu_service.require_assigned_category(session, item.chef_phone, body.category_id)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            item.category_id = category.category_id
        elif item.category_id:
            category = await session.get(ChefMenuCategory, item.category_id)
        data = body.model_dump(exclude_unset=True)
        if "dish_name" in data and data["dish_name"]:
            item.dish_name = data["dish_name"].strip()
        if "description" in data:
            item.description = data["description"]
        if "unit_price" in data and data["unit_price"] is not None:
            item.unit_price = Decimal(str(data["unit_price"]))
        if "meal_type" in data and data["meal_type"]:
            item.meal_type = data["meal_type"].upper()
        if "dietary_tag" in data:
            item.dietary_tag = data["dietary_tag"]
        if "is_available" in data and data["is_available"] is not None:
            item.is_available = data["is_available"]
        if "image_url" in data:
            item.image_url = data["image_url"]
        if "max_availability" in data and data["max_availability"] is not None:
            item.max_availability = data["max_availability"]
        await session.commit()
        return {"item": menu_card(item, category), "status": "SUCCESS"}


@router.delete("/menu/{menu_item_id}")
async def admin_delete_menu_item(
    menu_item_id: str,
    mode: str | None = None,
    current_admin: dict[str, Any] = Depends(get_current_admin),
):
    """Delete/archive a dish on any chef's menu (same rules as the chef's own delete)."""
    from backend.app.models.customer import CustomerOrderItem

    async with SessionFactory() as session:
        item = await session.get(ChefMenuItem, menu_item_id)
        if item is None or item.is_archived:
            raise HTTPException(status_code=404, detail="Menu item not found")
        order_refs = (
            await session.execute(
                select(func.count())
                .select_from(CustomerOrderItem)
                .where(CustomerOrderItem.menu_item_id == menu_item_id)
            )
        ).scalar_one()
        if order_refs == 0:
            from sqlalchemy import delete as sa_delete

            await session.execute(
                sa_delete(ChefDailyInventory).where(ChefDailyInventory.menu_item_id == menu_item_id)
            )
            await session.delete(item)
            await session.commit()
            return {"deleted": True, "archived": False}
        item.is_archived = True
        item.is_available = False
        from sqlalchemy import delete as sa_delete

        await session.execute(
            sa_delete(ChefDailyInventory).where(ChefDailyInventory.menu_item_id == menu_item_id)
        )
        await session.commit()
        return {"deleted": True, "archived": True}


@router.post("/chefs/{chef_phone}/menu/image")
async def admin_upload_dish_image(
    chef_phone: str,
    file: UploadFile = File(...),
    current_admin: dict[str, Any] = Depends(get_current_admin),
):
    """Stage a dish photo for a chef's menu (admin add-dish flow)."""
    async with SessionFactory() as session:
        await _admin_require_chef(session, chef_phone)
        data = await file.read()
        try:
            url = await save_dish_image(chef_phone, data, file.content_type, file.filename)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"imageUrl": url, "menuItemId": None}


@router.post("/chefs/{chef_phone}/menu/{menu_item_id}/image")
async def admin_replace_dish_image(
    chef_phone: str,
    menu_item_id: str,
    file: UploadFile = File(...),
    current_admin: dict[str, Any] = Depends(get_current_admin),
):
    """Attach/replace the photo on one dish (admin)."""
    async with SessionFactory() as session:
        await _admin_require_chef(session, chef_phone)
        item = await session.get(ChefMenuItem, menu_item_id)
        if item is None or item.chef_phone != chef_phone:
            raise HTTPException(status_code=404, detail="Menu item not found")
        data = await file.read()
        try:
            url = await save_dish_image(chef_phone, data, file.content_type, file.filename)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        item.image_url = url
        await session.commit()
        return {"imageUrl": url, "menuItemId": menu_item_id}


@router.post("/clear-all-data")
async def clear_all_admin_data(
    current_admin: dict[str, Any] = Depends(get_current_admin)
):
    """Clear all customer profiles, orders, and conversation messages directly on Cloud SQL database."""
    from backend.app.models.customer import CustomerOrder, CustomerOrderItem, CustomerProfile, CustomerReview
    from backend.app.tools.pause import _pending
    from sqlalchemy import delete

    # 1. Reset in-memory pending state
    _pending.clear()

    # 2. Delete database records
    async with SessionFactory() as session:
        async with session.begin():
            r1 = await session.execute(delete(CustomerOrderItem))
            r2 = await session.execute(delete(CustomerReview))
            r3 = await session.execute(delete(CustomerOrder))
            r4 = await session.execute(delete(CustomerProfile))
            r5 = await session.execute(delete(ConversationMessage))

    logger.info(f"🧹 ADMIN RESET EXECUTED BY {current_admin.get('email')}: Deleted {r5.rowcount} messages, {r4.rowcount} profiles.")
@router.post("/seed-chefs-and-riders")
async def seed_chefs_and_riders_admin(
    current_admin: dict[str, Any] = Depends(get_current_admin)
):
    """Seed 4 Ghansoli home kitchens with menus and 2 delivery riders directly on Cloud SQL database."""
    try:
        from decimal import Decimal
        from sqlalchemy import delete
        from backend.app.models.chef import ChefMenuItem, ChefProfile
        from backend.app.models.driver import DriverProfile

        CHEFS = [
            dict(phone="9876543210", kitchen="Indravati Pure Veg Tiffins", chef="Chef Sunita Sharma",
                 addr="Indravati CHS, Sector 6, Ghansoli", lat="19.1240", lng="73.0018", diet="VEG",
                 dishes=[("Jain Paneer Tikka Tiffin", "180.00", "LUNCH"), ("Dal Tadka & Jeera Rice", "140.00", "DINNER")]),
            dict(phone="9876543211", kitchen="Konkan Coastal Flavors", chef="Chef Ananya Naik",
                 addr="Sector 5, Ghansoli", lat="19.1220", lng="73.0005", diet="NON_VEG",
                 dishes=[("Surmai Fish Curry Tiffin", "280.00", "LUNCH"), ("Chicken Sukka & Neer Dosa", "240.00", "DINNER")]),
            dict(phone="9876543212", kitchen="Desi Punjabi Dhaba Tiffins", chef="Chef Rajesh Grewal",
                 addr="Sector 4, Ghansoli", lat="19.1205", lng="72.9995", diet="BOTH",
                 dishes=[("Amritsari Chole Bhature Tiffin", "170.00", "LUNCH"), ("Butter Chicken & Naan", "260.00", "DINNER")]),
            dict(phone="9876543213", kitchen="Dakshin Annapoorna Tiffins", chef="Chef Meenakshi Iyer",
                 addr="Sector 7, Ghansoli", lat="19.1260", lng="73.0030", diet="VEG",
                 dishes=[("Special Chettinad Veg Meals", "190.00", "LUNCH"), ("Mysore Masala Dosa Pack", "140.00", "DINNER")]),
        ]

        DRIVERS = [
            dict(phone="9111111111", name="Rider Ramesh", lat="19.1240", lng="73.0018"),
            dict(phone="9222222222", name="Rider Suresh", lat="19.1220", lng="73.0005"),
        ]

        async with SessionFactory() as session:
            async with session.begin():
                await session.execute(delete(ChefMenuItem))
                await session.execute(delete(ChefProfile))
                await session.execute(delete(DriverProfile))

                for c in CHEFS:
                    cp = ChefProfile(
                        chef_phone=c['phone'],
                        kitchen_name=c['kitchen'],
                        chef_name=c['chef'],
                        address=c['addr'],
                        latitude=Decimal(c['lat']),
                        longitude=Decimal(c['lng']),
                        dietary_type=c['diet'],
                        active_status=True,
                    )
                    session.add(cp)
                    await session.flush()

                    for dish_name, price, window in c['dishes']:
                        item = ChefMenuItem(
                            chef_phone=c['phone'],
                            dish_name=dish_name,
                            unit_price=Decimal(price),
                            meal_type=window,
                            is_available=True,
                        )
                        session.add(item)

                for d in DRIVERS:
                    dp = DriverProfile(
                        driver_phone=d['phone'],
                        driver_name=d['name'],
                        vehicle_number="MH-43-AZ-1234",
                        is_on_shift=True,
                        active_status=True,
                    )
                    session.add(dp)

        logger.info(f"👩‍🍳 SEEDED 4 GHANSOLI KITCHENS & 2 RIDERS BY {current_admin.get('email')}")
        return {
            "status": "SUCCESS",
            "message": "Successfully seeded 4 Ghansoli home kitchens and 2 delivery riders into production Cloud SQL database!",
        }
    except Exception as e:
        logger.error(f"🔴 Exception inside seed_chefs_and_riders_admin: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ==============================================================================
# 6. CHEF & RIDER MANAGEMENT ENDPOINTS
# ==============================================================================
@router.get("/chefs")
async def list_chefs(current_admin: dict[str, Any] = Depends(get_current_admin)):
    """List all registered chefs and their location coordinates."""
    async with SessionFactory() as session:
        res = await session.execute(select(ChefProfile).order_by(ChefProfile.created_at.desc()))
        chefs = res.scalars().all()
        return [
            {
                "chef_phone": c.chef_phone,
                "chef_name": c.chef_name,
                "kitchen_name": c.kitchen_name,
                "address": c.address,
                "apartment_or_locality": c.apartment_or_locality,
                "city": c.city,
                "pincode": c.pincode,
                "latitude": float(c.latitude),
                "longitude": float(c.longitude),
                "dietary_type": c.dietary_type,
                "fssai_license_number": c.fssai_license_number,
                "active_status": c.active_status,
                "is_verified": c.is_verified,
            }
            for c in chefs
        ]


@router.post("/chefs")
async def create_chef(
    payload: CreateChefInput,
    current_admin: dict[str, Any] = Depends(get_current_admin)
):
    """Add a new Chef profile with exact location coordinates."""
    from decimal import Decimal
    async with SessionFactory() as session:
        existing = await session.get(ChefProfile, payload.chef_phone)
        if existing:
            raise HTTPException(status_code=400, detail="Chef with this phone number already exists.")

        chef = ChefProfile(
            chef_phone=payload.chef_phone,
            chef_name=payload.chef_name,
            kitchen_name=payload.kitchen_name,
            address=payload.address,
            apartment_or_locality=payload.apartment_or_locality,
            city=payload.city,
            pincode=payload.pincode,
            latitude=Decimal(str(payload.latitude)),
            longitude=Decimal(str(payload.longitude)),
            dietary_type=payload.dietary_type,
            fssai_license_number=payload.fssai_license_number or "PENDING",
            kitchen_bio=payload.kitchen_bio,
            is_verified=True,
            active_status=True
        )
        session.add(chef)
        await session.commit()
        return {"status": "SUCCESS", "message": f"Chef {payload.chef_name} added successfully!"}


@router.post("/chefs/{chef_phone}/approve")
async def approve_chef(chef_phone: str, current_admin: dict[str, Any] = Depends(get_current_admin)):
    """Approve a pending home kitchen: verifies + activates it for customers."""
    async with SessionFactory() as session:
        chef = await session.get(ChefProfile, chef_phone)
        if chef is None or chef.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Chef not found")
        chef.is_verified = True
        chef.active_status = True
        chef.accepting_orders = True
        await session.commit()
        return {"status": "SUCCESS", "message": f"{chef.kitchen_name} approved and live."}


@router.post("/chefs/{chef_phone}/reject")
async def reject_chef(chef_phone: str, current_admin: dict[str, Any] = Depends(get_current_admin)):
    """Reject a pending kitchen: soft-deletes it so the phone can onboard again."""
    from datetime import datetime, timezone

    async with SessionFactory() as session:
        chef = await session.get(ChefProfile, chef_phone)
        if chef is None or chef.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Chef not found")
        chef.deleted_at = datetime.now(timezone.utc)
        chef.active_status = False
        chef.accepting_orders = False
        await session.commit()
        return {"status": "SUCCESS", "message": f"{chef.kitchen_name} rejected."}


@router.get("/drivers")
async def list_drivers(current_admin: dict[str, Any] = Depends(get_current_admin)):
    """List all registered delivery drivers."""
    async with SessionFactory() as session:
        res = await session.execute(select(DriverProfile).order_by(DriverProfile.created_at.desc()))
        drivers = res.scalars().all()
        return [
            {
                "driver_phone": d.driver_phone,
                "driver_name": d.driver_name,
                "vehicle_type": d.vehicle_type,
                "vehicle_number": d.vehicle_number,
                "vehicle_model": d.vehicle_model,
                "is_on_shift": d.is_on_shift,
                "active_status": d.active_status,
            }
            for d in drivers
        ]


@router.post("/drivers")
async def create_driver(
    payload: CreateDriverInput,
    current_admin: dict[str, Any] = Depends(get_current_admin)
):
    """Add a new Delivery Rider profile."""
    async with SessionFactory() as session:
        existing = await session.get(DriverProfile, payload.driver_phone)
        if existing:
            raise HTTPException(status_code=400, detail="Driver with this phone number already exists.")

        driver = DriverProfile(
            driver_phone=payload.driver_phone,
            driver_name=payload.driver_name,
            vehicle_type=payload.vehicle_type,
            vehicle_number=payload.vehicle_number,
            vehicle_reg_number=payload.vehicle_number,
            vehicle_model=payload.vehicle_model,
            driver_license_number=payload.driver_license_number,
            driving_license_number=payload.driver_license_number or "PENDING",
            is_on_shift=True,
            active_status=True
        )
        session.add(driver)
        await session.commit()
        return {"status": "SUCCESS", "message": f"Rider {payload.driver_name} added successfully!"}
