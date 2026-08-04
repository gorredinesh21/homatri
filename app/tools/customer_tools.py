"""Customer-domain tools.

Pattern for every tool:
  1. A Pydantic input schema (the LLM-facing signature).
  2. An inner `async def _name(session, *, ...) -> dict` that holds the guards +
     DB work and returns `{status, ...data, message}` — unit-testable with a session.
  3. A `@tool` wrapper that opens a session (SessionFactory for reads,
     transaction() for writes), calls the inner fn, and returns the `message`
     string for the agent to read (guard-then-guide).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionFactory, transaction
from app.executors.customer import execute_customer_registration_and_location
from app.models.chef import ChefMenuItem, ChefProfile
from app.models.customer import CustomerProfile, CustomerReview
from app.tools.common import haversine_km, resolve_time_pool
from app.tools.pause import resume_handler, send_and_await_reply


# =============================================================================
# TOOL: get_customer_profile  (same-domain · READ)
# =============================================================================
class GetCustomerProfileInput(BaseModel):
    customer_phone: str = Field(
        ...,
        description="Normalized 10-digit customer phone number, e.g. '9123456789'.",
    )


async def _get_customer_profile(session: AsyncSession, *, customer_phone: str) -> dict[str, Any]:
    """Look up a customer by phone. Returns {status, profile?, message}.

    Guards:
      - no row            -> NOT_FOUND   (new user; guide to register_customer)
      - row, not finished -> INCOMPLETE  (started but no location pin; guide to register_customer)
      - row, registered   -> FOUND       (return the profile)
    """
    profile = await session.get(CustomerProfile, customer_phone)

    if profile is None:
        return {
            "status": "NOT_FOUND",
            "message": (
                f"No customer found for {customer_phone}. New user — "
                f"call register_customer with their name and delivery address to onboard them."
            ),
        }

    if not profile.is_registered:
        return {
            "status": "INCOMPLETE",
            "message": (
                f"Customer {customer_phone} started registration but never shared a location pin. "
                f"Call register_customer to complete it."
            ),
        }

    return {
        "status": "FOUND",
        "profile": {
            "customer_phone": profile.customer_phone,
            "name": profile.name,
            "delivery_address": profile.delivery_address,
            "latitude": float(profile.latitude) if profile.latitude is not None else None,
            "longitude": float(profile.longitude) if profile.longitude is not None else None,
            "dietary_preference": profile.dietary_preference,
        },
        "message": (
            f"Found registered customer {profile.name} ({customer_phone}) at "
            f"{profile.delivery_address}."
        ),
    }


@tool("get_customer_profile", args_schema=GetCustomerProfileInput)
async def get_customer_profile(customer_phone: str) -> str:
    """Identify a customer by phone on an inbound message; report whether they are registered."""
    async with SessionFactory() as session:
        res = await _get_customer_profile(session, customer_phone=customer_phone)
        return res["message"]


# =============================================================================
# TOOL: find_nearby_kitchens  (same-domain · READ)
# =============================================================================
class FindNearbyKitchensInput(BaseModel):
    latitude: float = Field(..., description="Customer latitude, e.g. 19.1214684")
    longitude: float = Field(..., description="Customer longitude, e.g. 73.0036295")


async def _find_nearby_kitchens(
    session: AsyncSession, *, latitude: float, longitude: float, now: datetime | None = None, limit: int = 5
) -> dict[str, Any]:
    """Nearest active chefs serving the current time-window that have >=1 available dish.

    Window is derived from the current time (resolve_time_pool). No start guard;
    single end guard NONE_OPEN. Returns {status, window, kitchens, message}.
    """
    window = resolve_time_pool(now)["window"]  # LUNCH or DINNER

    # active chefs with at least one available dish for this meal window
    serving = select(ChefMenuItem.chef_phone).where(
        ChefMenuItem.meal_type == window,
        ChefMenuItem.is_available.is_(True),
    )
    chefs = (
        await session.execute(
            select(ChefProfile).where(
                ChefProfile.active_status.is_(True),
                ChefProfile.chef_phone.in_(serving),
            )
        )
    ).scalars().all()
    if not chefs:
        return {
            "status": "NONE_OPEN",
            "window": window,
            "kitchens": [],
            "message": f"No kitchens are serving {window.lower()} right now.",
        }

    # average chef rating (missing for new chefs)
    rating_rows = (
        await session.execute(
            select(CustomerReview.chef_phone, func.avg(CustomerReview.chef_rating)).group_by(
                CustomerReview.chef_phone
            )
        )
    ).all()
    ratings = {ph: round(float(avg), 1) for ph, avg in rating_rows}

    kitchens = [
        {
            "chef_phone": c.chef_phone,
            "kitchen_name": c.kitchen_name,
            "chef_name": c.chef_name,
            "dietary_type": c.dietary_type,
            "rating": ratings.get(c.chef_phone),
            "distance_km": round(haversine_km(latitude, longitude, float(c.latitude), float(c.longitude)), 2),
        }
        for c in chefs
    ]
    kitchens.sort(key=lambda k: k["distance_km"])   # sort FIRST, then cut
    kitchens = kitchens[:limit]

    lines = []
    for i, k in enumerate(kitchens, 1):
        rating = f"⭐{k['rating']}" if k["rating"] is not None else "new"
        diet = f" [{k['dietary_type']}]" if k["dietary_type"] else ""
        lines.append(f"{i}. {k['kitchen_name']} ({k['chef_name']}) — {k['distance_km']} km, {rating}{diet}")
    msg = f"Nearest kitchens serving {window.lower()}:\n" + "\n".join(lines)
    return {"status": "OK", "window": window, "kitchens": kitchens, "message": msg}


@tool("find_nearby_kitchens", args_schema=FindNearbyKitchensInput)
async def find_nearby_kitchens(latitude: float, longitude: float) -> str:
    """Find the nearest active home kitchens serving the current meal window (by the clock), nearest first."""
    async with SessionFactory() as session:
        res = await _find_nearby_kitchens(session, latitude=latitude, longitude=longitude)
        return res["message"]


# =============================================================================
# TOOL: register_customer  (same-domain · WRITE + PAUSE)
# =============================================================================
class RegisterCustomerInput(BaseModel):
    customer_phone: str = Field(..., description="Normalized 10-digit customer phone, e.g. '7416767453'.")
    name: str = Field(..., description="Customer's name, e.g. 'Dinesh'.")
    delivery_address: str = Field(..., description="Full delivery address in text.")


async def _register_customer(
    session: AsyncSession, *, customer_phone: str, name: str, delivery_address: str
) -> dict[str, Any]:
    """Save name + address with is_registered=False. Returns {status, message, ctx}.

    Guard: missing name/address -> INVALID (guide the agent to ask again).
    """
    if not (name or "").strip() or not (delivery_address or "").strip():
        return {"status": "INVALID", "message": "I need both a name and a delivery address to register you."}

    await execute_customer_registration_and_location(
        session, customer_phone=customer_phone, name=name.strip(),
        delivery_address=delivery_address.strip(), is_registered=False,
    )
    return {
        "status": "AWAITING_LOCATION",
        "message": f"Thanks {name.strip()}! Your address is saved. Please share your location pin to finish registering.",
        "ctx": {"phone": customer_phone, "name": name.strip(), "address": delivery_address.strip()},
    }


@tool("register_customer", args_schema=RegisterCustomerInput)
async def register_customer(customer_phone: str, name: str, delivery_address: str) -> str:
    """Register a new customer: save their name & address, then ask for their location pin."""
    async with transaction() as session:
        res = await _register_customer(
            session, customer_phone=customer_phone, name=name, delivery_address=delivery_address,
        )
    if res["status"] == "AWAITING_LOCATION":
        # Pause the turn and wait for the location pin — resumes finish_registration.
        send_and_await_reply(
            customer_phone, res["message"],
            await_type="LOCATION_PIN", resume="finish_registration", ctx=res["ctx"],
        )
    return res["message"]  # only reached on INVALID (guard-then-guide)


async def _finish_registration(
    session: AsyncSession, *, customer_phone: str, name: str, delivery_address: str,
    latitude: float, longitude: float,
) -> None:
    """Save location + mark is_registered=True (the second half of registration)."""
    await execute_customer_registration_and_location(
        session, customer_phone=customer_phone, name=name, delivery_address=delivery_address,
        latitude=latitude, longitude=longitude, is_registered=True,
    )


@resume_handler("finish_registration")
async def finish_registration(phone: str, reply: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Resume handler: runs when the location pin arrives. Saves it, then shows kitchens."""
    lat, lng = float(reply["latitude"]), float(reply["longitude"])
    async with transaction() as session:
        await _finish_registration(
            session, customer_phone=phone, name=ctx["name"], delivery_address=ctx["address"],
            latitude=lat, longitude=lng,
        )
    async with SessionFactory() as session:
        res = await _find_nearby_kitchens(session, latitude=lat, longitude=lng)
    return f"You're all set, {ctx['name']}! Location saved.\n\n{res['message']}"
