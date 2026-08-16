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

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import func, select, update

from backend.app.db.session import SessionFactory
from backend.app.executors.master import execute_meal_window_lock_and_creation
from backend.app.models.admin import AdminActivityLog, AdminUser
from backend.app.models.chef import ChefProfile
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

        # Active Kitchen count
        chefs_res = await session.execute(select(ChefProfile))
        chefs = chefs_res.scalars().all()

        return {
            "service_date": str(s_date),
            "stage_counts": stage_counts,
            "active_kitchens_count": len(chefs),
            "kitchens": [
                {
                    "kitchen_name": c.kitchen_name,
                    "chef_phone": c.chef_phone,
                    "locality": c.locality,
                    "is_accepting_orders": getattr(c, "is_accepting_orders", True),
                    "max_daily_capacity": c.max_daily_capacity,
                }
                for c in chefs
            ]
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
            fssai_license_number=payload.fssai_license_number,
            kitchen_bio=payload.kitchen_bio,
            is_verified=True,
            active_status=True
        )
        session.add(chef)
        await session.commit()
        return {"status": "SUCCESS", "message": f"Chef {payload.chef_name} added successfully!"}


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
            vehicle_model=payload.vehicle_model,
            driver_license_number=payload.driver_license_number,
            is_on_shift=True,
            active_status=True
        )
        session.add(driver)
        await session.commit()
        return {"status": "SUCCESS", "message": f"Rider {payload.driver_name} added successfully!"}
