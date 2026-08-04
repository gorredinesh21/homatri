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
from decimal import Decimal
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import SessionFactory, transaction
from app.executors.customer import (
    execute_add_item_to_order,
    execute_customer_order_initialization,
    execute_customer_registration_and_location,
)
from app.models.chef import ChefMenuItem, ChefProfile
from app.models.customer import (
    CustomerOrder,
    CustomerOrderItem,
    CustomerProfile,
    CustomerReview,
)
from app.models.system import SystemSetting
from app.tools.common import haversine_km, resolve_time_pool
from app.tools.master_tools import _mint_payment_link, _process_payment_webhook
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

    lat = float(profile.latitude) if profile.latitude is not None else None
    lng = float(profile.longitude) if profile.longitude is not None else None
    loc_hint = (
        f" Saved location: latitude {lat}, longitude {lng} — pass these to find_nearby_kitchens "
        f"if they want to browse (no need to ask for a location pin again)."
        if lat is not None and lng is not None else ""
    )
    return {
        "status": "FOUND",
        "profile": {
            "customer_phone": profile.customer_phone,
            "name": profile.name,
            "delivery_address": profile.delivery_address,
            "latitude": lat,
            "longitude": lng,
            "dietary_preference": profile.dietary_preference,
        },
        "message": (
            f"Found registered customer {profile.name} ({customer_phone}) at "
            f"{profile.delivery_address}.{loc_hint}"
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

    Guards:
      - already registered      -> ALREADY_REGISTERED (do NOT re-save; guide to browse)
      - missing name/address    -> INVALID (guide the agent to ask again)
    """
    existing = await session.get(CustomerProfile, customer_phone)
    if existing is not None and existing.is_registered:
        # Never re-register — that would flip is_registered off and loop the location pause.
        return {
            "status": "ALREADY_REGISTERED",
            "message": (
                f"{existing.name} is already registered. Do NOT register again. "
                f"To browse, call find_nearby_kitchens with their saved location "
                f"(latitude {float(existing.latitude) if existing.latitude is not None else '?'}, "
                f"longitude {float(existing.longitude) if existing.longitude is not None else '?'}); "
                f"or call view_chef_menu for a specific kitchen."
            ),
        }

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


# =============================================================================
# Reference resolvers — map human references (kitchen name / dish name) to rows,
# so the LLM never has to carry or fabricate internal IDs across turns.
# =============================================================================
def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


async def _resolve_chef(session: AsyncSession, ref: str) -> tuple[ChefProfile | None, str | None]:
    """Resolve a kitchen reference (a name, or a 10-digit phone) to one ChefProfile.

    Returns (chef, error) where error is None | 'NOT_FOUND' | 'AMBIGUOUS'.
    Name match is case-insensitive substring on kitchen_name (then chef_name).
    """
    ref = (ref or "").strip()
    if not ref:
        return None, "NOT_FOUND"
    if ref.isdigit():                                   # exact phone
        chef = await session.get(ChefProfile, ref)
        return (chef, None) if chef is not None else (None, "NOT_FOUND")

    chefs = (await session.execute(select(ChefProfile))).scalars().all()
    matches = [c for c in chefs if _norm(ref) in _norm(c.kitchen_name)]
    if not matches:
        matches = [c for c in chefs if _norm(ref) in _norm(c.chef_name)]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, "AMBIGUOUS"
    return None, "NOT_FOUND"


async def _resolve_dish(
    session: AsyncSession, *, chef_phone: str, ref: str, meal_type: str
) -> tuple[ChefMenuItem | None, str | None]:
    """Resolve a dish reference (a name) to one available ChefMenuItem for chef+window.

    Returns (dish, error) where error is None | 'NOT_FOUND' | 'AMBIGUOUS'.
    """
    ref = (ref or "").strip()
    if not ref:
        return None, "NOT_FOUND"
    rows = (
        await session.execute(
            select(ChefMenuItem).where(
                ChefMenuItem.chef_phone == chef_phone,
                ChefMenuItem.meal_type == meal_type,
                ChefMenuItem.is_available.is_(True),
            )
        )
    ).scalars().all()
    matches = [d for d in rows if _norm(ref) in _norm(d.dish_name)]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, "AMBIGUOUS"
    return None, "NOT_FOUND"


# =============================================================================
# TOOL: view_chef_menu  (same-domain · READ)
# =============================================================================
class ViewChefMenuInput(BaseModel):
    kitchen: str = Field(
        ...,
        description="The kitchen the customer picked, by NAME as shown in the nearby list "
                    "(e.g. 'Desi Punjabi' or 'Dakshin Annapoorna'). A partial name is fine.",
    )
    window: str | None = Field(
        default=None,
        description="Optional 'LUNCH' or 'DINNER'. Defaults to the current window by the clock.",
    )


async def _view_chef_menu(
    session: AsyncSession, *, kitchen: str, window: str | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """A kitchen's available dishes for the window. Returns {status, kitchen_name, window, dishes, message}.

    Availability = the menu item's `is_available` toggle (the chef flips it off when
    they run out). No capacity/units counting.

    Guards:
      - kitchen name matches nothing   -> NOT_FOUND
      - kitchen name matches several   -> AMBIGUOUS
      - no available dishes            -> NOT_SERVING
    """
    chef, err = await _resolve_chef(session, kitchen)
    if err == "AMBIGUOUS":
        return {"status": "AMBIGUOUS", "message": f"Several kitchens match '{kitchen}'. Ask the customer which one they mean."}
    if chef is None:
        return {"status": "NOT_FOUND", "message": f"No kitchen matches '{kitchen}'. Show the nearby list again and ask them to pick one."}

    raw = window or resolve_time_pool(now)["window"]
    meal_type = "DINNER" if "DINNER" in raw.upper() else "LUNCH"

    rows = (
        await session.execute(
            select(ChefMenuItem).where(
                ChefMenuItem.chef_phone == chef.chef_phone,
                ChefMenuItem.meal_type == meal_type,
                ChefMenuItem.is_available.is_(True),
            )
        )
    ).scalars().all()

    if not rows:
        return {
            "status": "NOT_SERVING",
            "window": meal_type,
            "message": f"{chef.kitchen_name} has nothing available for {meal_type.lower()} right now.",
        }

    dishes = [
        {"name": d.dish_name, "price": float(d.unit_price),
         "dietary": d.dietary_tag, "spice": d.spice_level}
        for d in rows
    ]
    lines = [
        f"{i}. {d['name']} — ₹{d['price']:.0f} ({d['dietary']}, {d['spice']} spice)"
        for i, d in enumerate(dishes, 1)
    ]
    msg = f"Menu at {chef.kitchen_name} ({chef.chef_name}) — {meal_type.lower()}:\n" + "\n".join(lines)
    return {"status": "OK", "kitchen_name": chef.kitchen_name, "window": meal_type, "dishes": dishes, "message": msg}


@tool("view_chef_menu", args_schema=ViewChefMenuInput)
async def view_chef_menu(kitchen: str, window: str | None = None) -> str:
    """Show a kitchen's available dishes (price, dietary, spice) for the current (or given) meal window. Pass the kitchen by name."""
    async with SessionFactory() as session:
        res = await _view_chef_menu(session, kitchen=kitchen, window=window)
        return res["message"]


# =============================================================================
# TOOL: create_order  (same-domain · WRITE)
# =============================================================================
ACTIVE_ORDER_STATUSES = ("DRAFT_CART", "PENDING_PAYMENT")


class OrderItemInput(BaseModel):
    dish_name: str = Field(..., description="Dish name exactly as shown on the menu (partial name is fine), e.g. 'Dal Makhani'.")
    quantity: int = Field(default=1, ge=1, description="How many of this dish.")


class CreateOrderInput(BaseModel):
    customer_phone: str = Field(..., description="Normalized 10-digit customer phone.")
    kitchen: str = Field(..., description="The kitchen the customer is ordering from, by NAME (partial is fine).")
    items: list[OrderItemInput] = Field(..., description="Dishes to order, each by dish name + quantity.")


async def _delivery_fee(session: AsyncSession) -> Decimal:
    """Read delivery fee from system_settings (key 'delivery_fee', value {'amount': N}); else config default."""
    row = await session.get(SystemSetting, "delivery_fee")
    if row is not None and isinstance(row.value, dict) and "amount" in row.value:
        return Decimal(str(row.value["amount"]))
    return Decimal(str(settings.default_delivery_fee))


async def _create_order(
    session: AsyncSession, *, customer_phone: str, kitchen: str,
    items: list[dict], now: datetime | None = None,
) -> dict[str, Any]:
    """Atomically create an order header + items (PENDING_PAYMENT). {status, order_id, ..., message}.

    Kitchen and dishes are given by NAME and resolved to real rows here.

    Guards:
      - customer already has an active order   -> ORDER_EXISTS (guide to add_item_to_order)
      - kitchen name matches nothing / several -> NOT_FOUND / AMBIGUOUS
      - empty / unknown / ambiguous dish       -> INVALID_ITEM
    """
    pool = resolve_time_pool(now)
    window, service_date = pool["window"], pool["service_date"]

    # Guard: one active order at a time
    existing = (
        await session.execute(
            select(CustomerOrder).where(
                CustomerOrder.customer_phone == customer_phone,
                CustomerOrder.status.in_(ACTIVE_ORDER_STATUSES),
            )
        )
    ).scalars().first()
    if existing is not None:
        return {
            "status": "ORDER_EXISTS",
            "order_id": existing.order_id,
            "message": (
                f"You already have an active order ({existing.order_id}). "
                f"Use add_item_to_order to add dishes to it, or pay/cancel it first."
            ),
        }

    chef, err = await _resolve_chef(session, kitchen)
    if err == "AMBIGUOUS":
        return {"status": "AMBIGUOUS", "message": f"Several kitchens match '{kitchen}'. Ask the customer which one."}
    if chef is None:
        return {"status": "NOT_FOUND", "message": f"No kitchen matches '{kitchen}'. Show the nearby list and ask them to pick."}

    if not items:
        return {"status": "INVALID_ITEM", "message": "No dishes selected to order."}

    # Resolve each dish by name against this chef's available menu for the window
    validated = []
    for it in items:
        ref = it.get("dish_name", "")
        qty = int(it.get("quantity", 1))
        dish, derr = await _resolve_dish(session, chef_phone=chef.chef_phone, ref=ref, meal_type=window)
        if derr == "AMBIGUOUS":
            return {"status": "INVALID_ITEM", "message": f"Several dishes match '{ref}' at {chef.kitchen_name}. Ask which one."}
        if dish is None:
            return {"status": "INVALID_ITEM", "message": f"'{ref}' isn't on {chef.kitchen_name}'s {window.lower()} menu. Show the menu again."}
        if qty < 1:
            return {"status": "INVALID_ITEM", "message": f"Quantity for '{dish.dish_name}' must be at least 1."}
        validated.append((dish, qty))

    delivery_fee = await _delivery_fee(session)

    order = await execute_customer_order_initialization(
        session, customer_phone=customer_phone, chef_phone=chef.chef_phone,
        kitchen_name=chef.kitchen_name, service_date=service_date,
        meal_window=window, delivery_fee=delivery_fee,
    )
    for dish, qty in validated:
        await execute_add_item_to_order(
            session, order_id=order.order_id, menu_item_id=dish.menu_item_id,
            dish_name=dish.dish_name, quantity=qty, unit_price=dish.unit_price,
        )

    lines = [f"{qty}× {dish.dish_name} (₹{float(dish.unit_price):.0f})" for dish, qty in validated]
    msg = (
        f"✅ Order {order.order_id} created:\n" + "\n".join(lines) +
        f"\nSubtotal ₹{float(order.cart_subtotal):.0f} + delivery ₹{float(order.delivery_fee):.0f} "
        f"= ₹{float(order.total_amount):.0f}. Ready for payment — call request_payment to send the payment link."
    )
    return {
        "status": "CREATED",
        "order_id": order.order_id,
        "subtotal": float(order.cart_subtotal),
        "delivery_fee": float(order.delivery_fee),
        "total": float(order.total_amount),
        "message": msg,
    }


def _norm_items(items: list) -> list[dict]:
    """Normalize the LLM's items arg to [{dish_name, quantity}] (accepts dicts or Pydantic)."""
    out = []
    for i in items:
        if isinstance(i, dict):
            out.append({"dish_name": i.get("dish_name", ""), "quantity": int(i.get("quantity", 1))})
        else:
            out.append({"dish_name": i.dish_name, "quantity": int(i.quantity)})
    return out


@tool("create_order", args_schema=CreateOrderInput)
async def create_order(customer_phone: str, kitchen: str, items: list) -> str:
    """Create an order (header + items) in PENDING_PAYMENT for the current window; then guide to payment. Pass kitchen + dishes by name."""
    async with transaction() as session:
        res = await _create_order(session, customer_phone=customer_phone, kitchen=kitchen, items=_norm_items(items))
        return res["message"]


# =============================================================================
# TOOL: add_item_to_order  (same-domain · WRITE)
# =============================================================================
class AddItemToOrderInput(BaseModel):
    customer_phone: str = Field(..., description="Normalized 10-digit customer phone.")
    items: list[OrderItemInput] = Field(
        ...,
        description=(
            "Dishes to set on the cart, each by dish name + quantity. IMPORTANT: `quantity` "
            "is the FINAL desired count of that dish in the cart, not how many to add. To go "
            "from 2 to 3, pass quantity=3. A dish not yet in the cart is simply added."
        ),
    )


async def _add_item_to_order(
    session: AsyncSession, *, customer_phone: str, items: list[dict],
) -> dict[str, Any]:
    """Set dish quantities on the customer's active (pre-payment) order. {status, ..., message}.

    Dishes are given by NAME and resolved against the order's own kitchen.
    Quantity is SET, not incremented (the executor upserts). The post-payment
    top-up (adding to an already CONFIRMED order) is a separate flow.

    Guards:
      - no active order (DRAFT_CART / PENDING_PAYMENT) -> NO_ACTIVE_ORDER (guide to create_order)
      - empty / unknown / ambiguous dish               -> INVALID_ITEM
    """
    order = (
        await session.execute(
            select(CustomerOrder).where(
                CustomerOrder.customer_phone == customer_phone,
                CustomerOrder.status.in_(ACTIVE_ORDER_STATUSES),
            )
        )
    ).scalars().first()
    if order is None:
        return {
            "status": "NO_ACTIVE_ORDER",
            "message": "You don't have an active cart yet. Call create_order to start one.",
        }

    if not items:
        return {"status": "INVALID_ITEM", "message": "No dishes to add."}

    # Resolve each dish by name against THIS order's chef + window (one kitchen per order)
    validated = []
    for it in items:
        ref = it.get("dish_name", "")
        qty = int(it.get("quantity", 1))
        dish, derr = await _resolve_dish(
            session, chef_phone=order.chef_phone, ref=ref, meal_type=order.meal_window
        )
        if derr == "AMBIGUOUS":
            return {"status": "INVALID_ITEM", "message": f"Several dishes match '{ref}' at {order.kitchen_name}. Ask which one."}
        if dish is None:
            return {"status": "INVALID_ITEM", "message": f"'{ref}' isn't on {order.kitchen_name}'s menu. Show the menu again."}
        if qty < 1:
            return {"status": "INVALID_ITEM", "message": f"Quantity for '{dish.dish_name}' must be at least 1."}
        validated.append((dish, qty))

    for dish, qty in validated:
        await execute_add_item_to_order(
            session, order_id=order.order_id, menu_item_id=dish.menu_item_id,
            dish_name=dish.dish_name, quantity=qty, unit_price=dish.unit_price,
        )

    # Read back the full cart for the summary
    rows = (
        await session.execute(
            select(CustomerOrderItem).where(CustomerOrderItem.order_id == order.order_id)
        )
    ).scalars().all()
    lines = [f"{r.quantity}× {r.dish_name} (₹{float(r.item_subtotal):.0f})" for r in rows]
    msg = (
        f"🛒 Updated cart (order {order.order_id}):\n" + "\n".join(lines) +
        f"\nSubtotal ₹{float(order.cart_subtotal):.0f} + delivery ₹{float(order.delivery_fee):.0f} "
        f"= ₹{float(order.total_amount):.0f}. Call request_payment to pay, or view_cart to review."
    )
    return {
        "status": "UPDATED",
        "order_id": order.order_id,
        "subtotal": float(order.cart_subtotal),
        "total": float(order.total_amount),
        "message": msg,
    }


@tool("add_item_to_order", args_schema=AddItemToOrderInput)
async def add_item_to_order(customer_phone: str, items: list) -> str:
    """Set dish quantities on the customer's active cart by dish name (quantity = final desired count); guide to payment."""
    async with transaction() as session:
        res = await _add_item_to_order(session, customer_phone=customer_phone, items=_norm_items(items))
        return res["message"]


# =============================================================================
# TOOL: view_cart  (same-domain · READ)
# =============================================================================
class ViewCartInput(BaseModel):
    customer_phone: str = Field(..., description="Normalized 10-digit customer phone.")


async def _view_cart(session: AsyncSession, *, customer_phone: str) -> dict[str, Any]:
    """Show the customer's active (pre-payment) cart. {status, order_id?, items?, ..., message}.

    Guard: no active order (DRAFT_CART / PENDING_PAYMENT) -> EMPTY (guide to browse/create_order).
    """
    order = (
        await session.execute(
            select(CustomerOrder).where(
                CustomerOrder.customer_phone == customer_phone,
                CustomerOrder.status.in_(ACTIVE_ORDER_STATUSES),
            )
        )
    ).scalars().first()
    if order is None:
        return {
            "status": "EMPTY",
            "message": "Your cart is empty. Call find_nearby_kitchens to browse and create_order to start one.",
        }

    rows = (
        await session.execute(
            select(CustomerOrderItem).where(CustomerOrderItem.order_id == order.order_id)
        )
    ).scalars().all()
    items = [
        {"item_id": r.menu_item_id, "dish_name": r.dish_name, "quantity": r.quantity,
         "unit_price": float(r.unit_price), "item_subtotal": float(r.item_subtotal)}
        for r in rows
    ]
    lines = [f"{r['quantity']}× {r['dish_name']} (₹{r['item_subtotal']:.0f})" for r in items]
    msg = (
        f"🛒 Your cart from {order.kitchen_name} ({order.meal_window.lower()}):\n" + "\n".join(lines) +
        f"\nSubtotal ₹{float(order.cart_subtotal):.0f} + delivery ₹{float(order.delivery_fee):.0f} "
        f"= ₹{float(order.total_amount):.0f}. Call request_payment to pay, or add_item_to_order to change it."
    )
    return {
        "status": "OK",
        "order_id": order.order_id,
        "kitchen_name": order.kitchen_name,
        "order_status": order.status,
        "items": items,
        "subtotal": float(order.cart_subtotal),
        "delivery_fee": float(order.delivery_fee),
        "total": float(order.total_amount),
        "message": msg,
    }


@tool("view_cart", args_schema=ViewCartInput)
async def view_cart(customer_phone: str) -> str:
    """Show the customer's current cart (items + totals) for their active order."""
    async with SessionFactory() as session:
        res = await _view_cart(session, customer_phone=customer_phone)
        return res["message"]


# =============================================================================
# TOOL: request_payment  (same-domain · WRITE + PAUSE)  [Flow 4]
#
# The customer never touches the gateway: minting the link + receiving the
# payment callback are the Master's job. Here (harness) that gateway role is
# played by `payment_service` (mock now, Razorpay test later — a config swap)
# plus the confirm_payment resume handler fired by the /pay callback.
# =============================================================================
class RequestPaymentInput(BaseModel):
    customer_phone: str = Field(..., description="Normalized 10-digit customer phone.")


async def _request_payment(session: AsyncSession, *, customer_phone: str) -> dict[str, Any]:
    """Ask Master to mint a payment link for the active order, then await confirmation. {status, ..., message, ctx?}.

    Guards (customer-facing):
      - no active order            -> NO_ACTIVE_ORDER (guide to create_order)
      - order not PENDING_PAYMENT  -> NOT_PAYABLE (already confirmed / cancelled)
      - total <= 0                 -> EMPTY_CART
    """
    order = (
        await session.execute(
            select(CustomerOrder).where(
                CustomerOrder.customer_phone == customer_phone,
                CustomerOrder.status.in_(ACTIVE_ORDER_STATUSES),
            )
        )
    ).scalars().first()
    if order is None:
        return {"status": "NO_ACTIVE_ORDER", "message": "You don't have an order to pay for. Call create_order first."}
    if order.status != "PENDING_PAYMENT":
        return {"status": "NOT_PAYABLE", "message": f"Order {order.order_id} is {order.status.lower()} — nothing to pay."}
    if order.total_amount is None or float(order.total_amount) <= 0:
        return {"status": "EMPTY_CART", "message": "Your cart is empty — add a dish before paying."}

    # Deterministic relay to Master — Master owns minting (and the webhook).
    mint = await _mint_payment_link(session, order_id=order.order_id)
    if mint["status"] != "MINTED":
        return {"status": mint["status"], "message": mint.get("message", "Couldn't start payment right now.")}

    return {
        "status": "AWAITING_PAYMENT",
        "order_id": order.order_id,
        "payment_id": mint["payment_id"],
        "amount": mint["amount"],
        "link": mint["link"],
        "message": (
            f"💳 Please pay ₹{mint['amount']:.0f} to confirm order {order.order_id}:\n"
            f"{mint['link']}\n\nI'll confirm the moment your payment goes through."
        ),
        "ctx": {"order_id": order.order_id, "payment_id": mint["payment_id"]},
    }


@tool("request_payment", args_schema=RequestPaymentInput)
async def request_payment(customer_phone: str) -> str:
    """Generate a payment link for the customer's active order, then wait for the payment to complete."""
    async with transaction() as session:
        res = await _request_payment(session, customer_phone=customer_phone)
    if res["status"] == "AWAITING_PAYMENT":
        # Pause the thread until the payment callback arrives -> resumes confirm_payment.
        send_and_await_reply(
            customer_phone, res["message"],
            await_type="PAYMENT_CONFIRM", resume="confirm_payment", ctx=res["ctx"],
        )
    return res["message"]  # only reached on a guard (guard-then-guide)


@resume_handler("confirm_payment")
async def confirm_payment(phone: str, reply: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Resume handler: runs when the payment callback arrives. Delegates to Master's
    process_payment_webhook (marks PAID -> order CONFIRMED), then confirms to the customer."""
    txn = (reply or {}).get("transaction_id") or (reply or {}).get("txn_id")
    async with transaction() as session:
        res = await _process_payment_webhook(session, payment_id=ctx["payment_id"], transaction_id=txn)
    if res["status"] in ("PAID", "ALREADY_PAID"):
        return (
            f"✅ Payment received! Your order {res['order_id']} is CONFIRMED. "
            f"The kitchen is notified at cutoff — you'll get updates here. 🍽️"
        )
    return res["message"]
