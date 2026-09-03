"""MSG91 OTP, Google login, 30-day sessions, and chef/rider onboarding."""

from __future__ import annotations

import logging
import random
import re
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.core.security import (
    REFRESH_COOKIE,
    bearer_payload,
    create_access_token,
    hash_token,
    new_refresh_token,
    utcnow,
)
from backend.app.db.session import SessionFactory
from backend.app.models.auth import UserSession
from backend.app.models.chef import ChefProfile
from backend.app.models.customer import CustomerProfile
from backend.app.models.driver import DriverProfile

logger = logging.getLogger("homatri_auth")
router = APIRouter(prefix="/auth", tags=["auth"])

PHONE_RE = re.compile(r"^[6-9]\d{9}$")
FSSAI_RE = re.compile(r"^\d{14}$")
UPI_RE = re.compile(r"^[\w.\-]{2,}@[\w.\-]{2,}$")
VEHICLE_RE = re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{1,4}$", re.I)


class RegisterIn(BaseModel):
    phone: str
    email: str
    password: str
    full_name: str | None = None


class LoginIn(BaseModel):
    phone: str
    password: str


class UsernameSetupIn(BaseModel):
    phone: str
    requested_username: str | None = None


class Msg91VerifyIn(BaseModel):
    phone: str
    msg91_token: str
    full_name: str | None = None
    avatar_url: str | None = None
    is_cartoon_avatar: bool = True


class GoogleLoginIn(BaseModel):
    id_token: str
    phone: str | None = None
    avatar_url: str | None = None
    is_cartoon_avatar: bool | None = None


class ChefOnboardingIn(BaseModel):
    chef_phone: str
    chef_name: str
    kitchen_name: str
    bio: str
    hometown_region: str
    fssai_license_number: str
    daily_capacity: int = Field(default=15, ge=1, le=200)
    address_line1: str
    city: str = "Navi Mumbai"
    latitude: float
    longitude: float
    payout_upi_id: str
    avatar_url: str | None = "avatar_chef_cartoon_1.png"


class RiderOnboardingIn(BaseModel):
    driver_phone: str
    driver_name: str
    driving_license_number: str
    vehicle_type: str = "SCOOTER"
    vehicle_reg_number: str
    assigned_cluster: str = "Ghansoli"
    payout_upi_id: str
    driver_photo_url: str | None = None


def _phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if not PHONE_RE.match(digits):
        raise HTTPException(status_code=400, detail="Enter a valid 10-digit Indian mobile number")
    return digits


def _hash_pwd(password: str) -> str:
    import hashlib
    return hashlib.sha256((password + "homatri_secure_salt").encode("utf-8")).hexdigest()


@router.post("/register")
async def register(payload: RegisterIn, request: Request, response: Response) -> dict[str, Any]:
    phone = _phone(payload.phone)
    email = payload.email.strip().lower()
    if not email or "@" not in email or "." not in email:
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    if not payload.password or len(payload.password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")

    async with SessionFactory() as db:
        existing = await db.get(CustomerProfile, phone)
        if existing and existing.password_hash:
            raise HTTPException(status_code=400, detail="Phone number already registered. Please log in.")

        if existing is None:
            existing = CustomerProfile(
                customer_phone=phone,
                name=payload.full_name.strip() if payload.full_name else f"User {phone[-4:]}",
                full_name=payload.full_name.strip() if payload.full_name else f"User {phone[-4:]}",
                email=email,
                password_hash=_hash_pwd(payload.password),
                delivery_address="Default Delivery Address, Ghansoli",
                is_registered=True,
            )
            db.add(existing)
        else:
            existing.email = email
            existing.password_hash = _hash_pwd(payload.password)
            if payload.full_name:
                existing.name = payload.full_name.strip()
                existing.full_name = payload.full_name.strip()
            existing.is_registered = True

        await db.commit()
        await db.refresh(existing)
        profile = _customer_public(existing)

    return await _issue_session(
        response=response, user_id=phone, role="CUSTOMER", request=request, profile=profile
    )


@router.post("/login")
async def login(payload: LoginIn, request: Request, response: Response) -> dict[str, Any]:
    phone = _phone(payload.phone)
    if not payload.password:
        raise HTTPException(status_code=400, detail="Password is required")

    async with SessionFactory() as db:
        user = await db.get(CustomerProfile, phone)
        if user is None or not user.password_hash:
            raise HTTPException(status_code=400, detail="No account found with this phone number. Please sign up.")

        if user.password_hash != _hash_pwd(payload.password):
            raise HTTPException(status_code=400, detail="Incorrect password. Please try again.")

        chef = await db.get(ChefProfile, phone)
        driver = await db.get(DriverProfile, phone)
        if chef is not None:
            role = "CHEF"
            profile = {
                "phone": phone,
                "chef_name": chef.chef_name,
                "kitchen_name": chef.kitchen_name,
                "avatar_url": chef.avatar_url,
                "role": "CHEF",
            }
        elif driver is not None:
            role = "RIDER"
            profile = {
                "phone": phone,
                "driver_name": driver.driver_name,
                "assigned_cluster": driver.assigned_cluster,
                "role": "RIDER",
            }
        else:
            role = "CUSTOMER"
            profile = _customer_public(user)

    return await _issue_session(
        response=response, user_id=phone, role=role, request=request, profile=profile
    )


def _sanitize_username(raw: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s_]", "", (raw or "").lower())
    cleaned = re.sub(r"[\s]+", "_", cleaned.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned[:20]


def generate_smart_username(full_name: str | None, phone: str) -> str:
    name_part = _sanitize_username(full_name or "")
    digits = re.sub(r"\D", "", phone or "")[-4:] or "0000"
    suffix = random.randint(1000, 9999)
    if name_part:
        return f"{name_part}_{suffix}"
    return f"foodie_{digits}_{suffix}"


async def _allocate_username(db, base: str) -> str:
    candidate = base[:50]
    for _ in range(12):
        taken = (
            await db.execute(select(CustomerProfile).where(CustomerProfile.username == candidate))
        ).scalar_one_or_none()
        if taken is None:
            return candidate
        stem = _sanitize_username(base)[:18] or "foodie"
        candidate = f"{stem}_{random.randint(1000, 9999)}"
    return f"foodie_{random.randint(10000, 99999)}"


@router.post("/setup-username")
async def setup_username(payload: UsernameSetupIn) -> dict[str, Any]:
    phone = _phone(payload.phone)
    async with SessionFactory() as db:
        user = await db.get(CustomerProfile, phone)
        if not user:
            raise HTTPException(status_code=404, detail="User profile not found")

        requested = (payload.requested_username or "").strip()
        if requested and len(_sanitize_username(requested)) >= 3:
            target = _sanitize_username(requested)
        else:
            target = generate_smart_username(user.full_name or user.name, phone)

        user.username = await _allocate_username(db, target)
        await db.commit()
        await db.refresh(user)
        return {"status": "success", "username": user.username, "user": _customer_public(user)}


def _customer_public(profile: CustomerProfile) -> dict[str, Any]:
    return {
        "id": str(profile.id),
        "phone": profile.customer_phone,
        "full_name": profile.full_name or profile.name,
        "email": profile.email,
        "username": profile.username,
        "avatar_url": profile.avatar_url,
        "is_cartoon_avatar": profile.is_cartoon_avatar,
        "role": getattr(profile, "role", None) or "CUSTOMER",
    }


async def _issue_session(
    *,
    response: Response,
    user_id: str,
    role: str,
    request: Request,
    profile: dict[str, Any],
) -> dict[str, Any]:
    refresh = new_refresh_token()
    expires = utcnow() + timedelta(seconds=settings.jwt_refresh_ttl_seconds)
    async with SessionFactory() as db:
        row = UserSession(
            user_id=user_id,
            user_role=role,
            refresh_token_hash=hash_token(refresh),
            device_info=(request.headers.get("user-agent") or "")[:500],
            ip_address=request.client.host if request.client else None,
            expires_at=expires,
            is_revoked=False,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        session_id = row.session_id

    access = create_access_token(user_id=user_id, role=role, session_id=session_id)
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        httponly=True,
        secure=settings.env_state == "production",
        samesite="lax",
        max_age=settings.jwt_refresh_ttl_seconds,
        path="/",
    )
    return {
        "access_token": access,
        "token_type": "bearer",
        "expires_in": settings.jwt_access_ttl_seconds,
        "role": role,
        "user": profile,
    }


async def _verify_msg91_token(access_token: str) -> None:
    if settings.env_state == "development" and access_token.startswith("dev:"):
        return
    body = {
        "authkey": settings.msg91_widget_token,
        "widgetId": settings.msg91_widget_id,
        "accessToken": access_token,
    }
    urls = (
        "https://control.msg91.com/api/v5/widget/verifyAccessToken",
        "https://api.msg91.com/api/v5/widget/verifyAccessToken",
    )
    last_error = "MSG91 verification failed"
    async with httpx.AsyncClient(timeout=15.0) as client:
        for url in urls:
            try:
                resp = await client.post(url, json=body)
                payload = resp.json() if resp.content else {}
                if resp.status_code < 400 and str(payload.get("type", "")).lower() != "error":
                    return
                last_error = str(payload.get("message") or payload.get("msg") or last_error)
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logger.warning("MSG91 verify failed against %s: %s", url, exc)
    raise HTTPException(status_code=401, detail=last_error)


async def _verify_google_id_token(token: str) -> dict[str, Any]:
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="google-auth is not installed") from exc
    try:
        info = id_token.verify_oauth2_token(
            token, google_requests.Request(), settings.google_oauth_client_id
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail=f"Invalid Google ID token: {exc}") from exc
    if info.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        raise HTTPException(status_code=401, detail="Invalid Google token issuer")
    return info


async def _upsert_customer(
    *,
    phone: str | None,
    name: str,
    email: str | None,
    google_sub: str | None,
    avatar_url: str | None,
    is_cartoon_avatar: bool,
) -> CustomerProfile:
    async with SessionFactory() as db:
        profile: CustomerProfile | None = None
        if google_sub:
            profile = (
                await db.execute(select(CustomerProfile).where(CustomerProfile.google_sub == google_sub))
            ).scalar_one_or_none()
        if profile is None and phone:
            profile = await db.get(CustomerProfile, phone)
        if profile is None and email:
            profile = (
                await db.execute(select(CustomerProfile).where(CustomerProfile.email == email))
            ).scalar_one_or_none()

        if profile is None:
            if not phone:
                digits = "".join(ch for ch in (google_sub or "9000000000") if ch.isdigit())[-10:].zfill(10)
                phone = digits if digits[0] in "6789" else "9" + digits[1:]
            profile = CustomerProfile(
                customer_phone=phone,
                name=name or "Guest",
                full_name=name or "Guest",
                delivery_address="Address pending",
                address_line1="Address pending",
                city="Navi Mumbai",
                email=email,
                google_sub=google_sub,
                avatar_url=avatar_url or "avatar_tiffin_cartoon_1.png",
                is_cartoon_avatar=is_cartoon_avatar,
                is_registered=True,
            )
            db.add(profile)
        else:
            if name:
                profile.name = name
                profile.full_name = name
            if email and not profile.email:
                profile.email = email
            if google_sub and not profile.google_sub:
                profile.google_sub = google_sub
            if avatar_url:
                profile.avatar_url = avatar_url
                profile.is_cartoon_avatar = is_cartoon_avatar
            profile.is_registered = True
        await db.commit()
        await db.refresh(profile)
        return profile


async def _profile_for(db, user_id: str, role: str) -> dict[str, Any]:
    if role == "CHEF":
        chef = await db.get(ChefProfile, user_id)
        if not chef:
            return {"phone": user_id, "role": role}
        return {
            "phone": chef.chef_phone,
            "chef_name": chef.chef_name,
            "kitchen_name": chef.kitchen_name,
            "avatar_url": chef.avatar_url,
            "role": role,
        }
    if role == "RIDER":
        driver = await db.get(DriverProfile, user_id)
        if not driver:
            return {"phone": user_id, "role": role}
        return {
            "phone": driver.driver_phone,
            "driver_name": driver.driver_name,
            "assigned_cluster": driver.assigned_cluster,
            "role": role,
        }
    customer = await db.get(CustomerProfile, user_id)
    if not customer:
        return {"phone": user_id, "role": role}
    return _customer_public(customer)


@router.post("/verify-msg91-widget")
@router.post("/verify_msg91_widget")
async def verify_msg91_widget(payload: Msg91VerifyIn, request: Request, response: Response) -> dict[str, Any]:
    phone = _phone(payload.phone)
    await _verify_msg91_token(payload.msg91_token)
    profile = await _upsert_customer(
        phone=phone,
        name=payload.full_name or "Guest",
        email=None,
        google_sub=None,
        avatar_url=payload.avatar_url,
        is_cartoon_avatar=payload.is_cartoon_avatar,
    )
    return await _issue_session(
        response=response,
        user_id=profile.customer_phone,
        role="CUSTOMER",
        request=request,
        profile=_customer_public(profile),
    )


@router.post("/google-login")
async def google_login(payload: GoogleLoginIn, request: Request, response: Response) -> dict[str, Any]:
    info = await _verify_google_id_token(payload.id_token)
    avatar = payload.avatar_url or info.get("picture")
    cartoon = True if payload.avatar_url else False if info.get("picture") else True
    if payload.is_cartoon_avatar is not None:
        cartoon = payload.is_cartoon_avatar
    phone_clean = _phone(payload.phone) if payload.phone else None
    profile = await _upsert_customer(
        phone=phone_clean,
        name=info.get("name") or "Guest",
        email=info.get("email"),
        google_sub=str(info.get("sub")),
        avatar_url=avatar,
        is_cartoon_avatar=cartoon,
    )
    return await _issue_session(
        response=response,
        user_id=profile.customer_phone,
        role="CUSTOMER",
        request=request,
        profile=_customer_public(profile),
    )


@router.post("/onboarding/chef")
async def onboard_chef(payload: ChefOnboardingIn, request: Request, response: Response) -> dict[str, Any]:
    phone = _phone(payload.chef_phone)
    fssai = re.sub(r"\D", "", payload.fssai_license_number)
    if not FSSAI_RE.match(fssai):
        raise HTTPException(status_code=400, detail="FSSAI license must be 14 digits")
    if not UPI_RE.match(payload.payout_upi_id.strip()):
        raise HTTPException(status_code=400, detail="Enter a valid UPI ID (example: homemaker@upi)")
    if not (-90 <= payload.latitude <= 90 and -180 <= payload.longitude <= 180):
        raise HTTPException(status_code=400, detail="Invalid latitude/longitude")

    async with SessionFactory() as db:
        chef = await db.get(ChefProfile, phone)
        if chef is None:
            chef = ChefProfile(
                chef_phone=phone,
                chef_name=payload.chef_name.strip(),
                kitchen_name=payload.kitchen_name.strip(),
                address=payload.address_line1.strip(),
                address_line1=payload.address_line1.strip(),
                city=payload.city.strip() or "Navi Mumbai",
                latitude=Decimal(str(payload.latitude)),
                longitude=Decimal(str(payload.longitude)),
                fssai_license_number=fssai,
                hometown_region=payload.hometown_region.strip(),
                kitchen_bio=payload.bio.strip(),
                bio=payload.bio.strip(),
                daily_capacity=payload.daily_capacity,
                payout_upi_id=payload.payout_upi_id.strip(),
                avatar_url=payload.avatar_url,
                is_verified=True,
                active_status=True,
                accepting_orders=True,
            )
            db.add(chef)
        else:
            chef.chef_name = payload.chef_name.strip()
            chef.kitchen_name = payload.kitchen_name.strip()
            chef.address = payload.address_line1.strip()
            chef.address_line1 = payload.address_line1.strip()
            chef.city = payload.city.strip() or chef.city
            chef.latitude = Decimal(str(payload.latitude))
            chef.longitude = Decimal(str(payload.longitude))
            chef.fssai_license_number = fssai
            chef.hometown_region = payload.hometown_region.strip()
            chef.kitchen_bio = payload.bio.strip()
            chef.bio = payload.bio.strip()
            chef.daily_capacity = payload.daily_capacity
            chef.payout_upi_id = payload.payout_upi_id.strip()
            if payload.avatar_url:
                chef.avatar_url = payload.avatar_url
            chef.is_verified = True
            chef.active_status = True
        await db.commit()

    public = {
        "phone": phone,
        "chef_name": payload.chef_name.strip(),
        "kitchen_name": payload.kitchen_name.strip(),
        "role": "CHEF",
    }
    return await _issue_session(
        response=response, user_id=phone, role="CHEF", request=request, profile=public
    )


@router.post("/onboarding/rider")
async def onboard_rider(payload: RiderOnboardingIn, request: Request, response: Response) -> dict[str, Any]:
    phone = _phone(payload.driver_phone)
    vehicle = payload.vehicle_reg_number.strip().upper().replace(" ", "-")
    compact = vehicle.replace("-", "")
    if not VEHICLE_RE.match(compact):
        raise HTTPException(status_code=400, detail="Vehicle registration must look like MH-43-AZ-1234")
    if not UPI_RE.match(payload.payout_upi_id.strip()):
        raise HTTPException(status_code=400, detail="Enter a valid UPI ID")
    vehicle_type = payload.vehicle_type.strip().upper()
    if vehicle_type in {"ELECTRIC", "EV"}:
        vehicle_type = "EV"
    elif vehicle_type not in {"SCOOTER", "BIKE", "EV"}:
        vehicle_type = "SCOOTER"

    async with SessionFactory() as db:
        driver = await db.get(DriverProfile, phone)
        if driver is None:
            driver = DriverProfile(
                driver_phone=phone,
                driver_name=payload.driver_name.strip(),
                driving_license_number=payload.driving_license_number.strip(),
                driver_license_number=payload.driving_license_number.strip(),
                vehicle_type=vehicle_type,
                vehicle_number=vehicle,
                vehicle_reg_number=vehicle,
                assigned_cluster=payload.assigned_cluster.strip() or "Ghansoli",
                payout_upi_id=payload.payout_upi_id.strip(),
                driver_photo_url=payload.driver_photo_url,
                is_active=True,
                active_status=True,
                on_shift=False,
                is_on_shift=False,
            )
            db.add(driver)
        else:
            driver.driver_name = payload.driver_name.strip()
            driver.driving_license_number = payload.driving_license_number.strip()
            driver.driver_license_number = payload.driving_license_number.strip()
            driver.vehicle_type = vehicle_type
            driver.vehicle_number = vehicle
            driver.vehicle_reg_number = vehicle
            driver.assigned_cluster = payload.assigned_cluster.strip() or driver.assigned_cluster
            driver.payout_upi_id = payload.payout_upi_id.strip()
            if payload.driver_photo_url:
                driver.driver_photo_url = payload.driver_photo_url
            driver.is_active = True
            driver.active_status = True
        await db.commit()

    public = {
        "phone": phone,
        "driver_name": payload.driver_name.strip(),
        "assigned_cluster": payload.assigned_cluster,
        "role": "RIDER",
    }
    return await _issue_session(
        response=response, user_id=phone, role="RIDER", request=request, profile=public
    )


@router.post("/refresh")
async def refresh_session(request: Request) -> dict[str, Any]:
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise HTTPException(status_code=401, detail="Refresh cookie missing")
    async with SessionFactory() as db:
        row = (
            await db.execute(
                select(UserSession).where(
                    UserSession.refresh_token_hash == hash_token(raw),
                    UserSession.is_revoked.is_(False),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=401, detail="Refresh session expired")
        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=utcnow().tzinfo)
        if expires_at < utcnow():
            raise HTTPException(status_code=401, detail="Refresh session expired")
        access = create_access_token(user_id=row.user_id, role=row.user_role, session_id=row.session_id)
        profile = await _profile_for(db, row.user_id, row.user_role)
    return {
        "access_token": access,
        "token_type": "bearer",
        "expires_in": settings.jwt_access_ttl_seconds,
        "role": row.user_role,
        "user": profile,
    }


@router.get("/me")
async def me(request: Request) -> dict[str, Any]:
    payload = bearer_payload(request)
    async with SessionFactory() as db:
        profile = await _profile_for(db, payload["sub"], payload["role"])
    return {"role": payload["role"], "user": profile}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, str]:
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        async with SessionFactory() as db:
            row = (
                await db.execute(select(UserSession).where(UserSession.refresh_token_hash == hash_token(raw)))
            ).scalar_one_or_none()
            if row is not None:
                row.is_revoked = True
                await db.commit()
    response.delete_cookie(REFRESH_COOKIE, path="/")
    return {"status": "logged_out"}
