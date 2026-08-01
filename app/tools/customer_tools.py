"""Customer Domain LLM Tools (Category 2).

Encapsulates Customer Concierge Agent tools with Guard 2 Pre-Condition Assertions.
Tool 1: get_customer_profile_tool (Read-only, Same Domain).
Tool 2: register_customer_profile_tool (Invokes Customer Agent for Structured Location Output).
Tool 3: find_nearby_home_kitchens_tool (Read-only, Same Domain).
Tool 4: view_chef_menu_tool (Read-only, Same Domain).
Tool 5: add_item_to_order_tool (Write Executor #6, Same Domain).
Tool 6: get_order_history_tool (Read-only, Same Domain).
Tool 7: submit_order_review_tool (Write Executor #8, Same Domain).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import math
from typing import Any, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import LocationInterrupt
from app.executors.customer import (
    execute_add_item_to_order,
    execute_customer_order_initialization,
    execute_customer_registration_and_location,
    execute_submit_order_review,
)
from app.executors.master import execute_conversation_message_insert, execute_outbound_whatsapp_enqueue
from app.models.chef import ChefDailyInventory, ChefMenuItem, ChefProfile
from app.models.customer import CustomerOrder, CustomerOrderItem, CustomerProfile, CustomerReview


# =============================================================================
# HAVERSINE DISTANCE HELPER
# =============================================================================
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great circle distance between two points in kilometers."""
    r = 6371.0  # Earth's radius in kilometers
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


# =============================================================================
# STRUCTURED OUTPUT SCHEMA FOR CUSTOMER AGENT LOCATION RESPONSE
# =============================================================================
class LocationStructuredOutput(BaseModel):
    """Structured location output returned by Customer Agent."""

    latitude: float = Field(..., description="Extracted GPS latitude coordinate (e.g. 17.4450)")
    longitude: float = Field(..., description="Extracted GPS longitude coordinate (e.g. 78.3800)")


# =============================================================================
# TOOL 1: get_customer_profile_tool
# =============================================================================
class GetCustomerProfileInput(BaseModel):
    customer_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of the customer (e.g. '9111111111')",
    )


async def get_customer_profile(
    session: AsyncSession,
    customer_phone: str,
) -> dict[str, Any] | None:
    """Query database for customer profile details with Guard 2 Pre-Condition Assertions."""
    assert customer_phone and len(customer_phone) >= 10, f"Invalid customer phone number: {customer_phone}"

    customer = await session.get(CustomerProfile, customer_phone)
    if customer is None:
        return None

    return {
        "customer_phone": customer.customer_phone,
        "name": customer.name,
        "delivery_address": customer.delivery_address,
        "apartment_name": customer.apartment_name,
        "flat_number": customer.flat_number,
        "landmark": customer.landmark,
        "city": customer.city,
        "pincode": customer.pincode,
        "latitude": float(customer.latitude) if customer.latitude is not None else None,
        "longitude": float(customer.longitude) if customer.longitude is not None else None,
        "dietary_preference": customer.dietary_preference,
        "is_registered": customer.is_registered,
    }


@tool("get_customer_profile_tool", args_schema=GetCustomerProfileInput)
async def get_customer_profile_tool(customer_phone: str) -> str:
    """Retrieve identity, delivery address, location pin status, and registration status for a customer."""
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        data = await get_customer_profile(session, customer_phone=customer_phone)
        if data is None:
            return (
                f"Customer profile for phone {customer_phone} is NOT registered yet (UNREGISTERED).\n"
                f"Please prompt customer for their full name and delivery address to complete onboarding."
            )

        location_str = (
            f"Shared (Lat {data['latitude']}, Lng {data['longitude']})"
            if data["latitude"] is not None and data["longitude"] is not None
            else "NOT SHARED YET (Please prompt customer to share WhatsApp location pin)"
        )
        reg_str = "REGISTERED" if data["is_registered"] else "PENDING_LOCATION_PIN"

        return (
            f"Customer Profile for {data['name']} ({data['customer_phone']}):\n"
            f"Delivery Address: {data['delivery_address']}\n"
            f"Apartment/Flat: {data['apartment_name'] or 'N/A'}, Flat {data['flat_number'] or 'N/A'}\n"
            f"Location Pin: {location_str}\n"
            f"Dietary Preference: {data['dietary_preference'] or 'VEG'}\n"
            f"Registration Status: {reg_str}"
        )


# =============================================================================
# CUSTOMER AGENT CALL FOR STRUCTURED LOCATION OUTPUT
# =============================================================================
async def invoke_customer_agent(
    session: AsyncSession,
    *,
    customer_phone: str,
    task: str,
    context: dict[str, Any],
) -> LocationStructuredOutput:
    """Invokes Customer Agent to request location pin on WhatsApp and return LocationStructuredOutput."""
    name = context.get("name", "Customer")
    delivery_address = context.get("delivery_address", "")
    prompt_text = (
        f"Thanks {name}! Your delivery address ({delivery_address}) has been saved. "
        f"To complete your registration and see home kitchens near you, please tap the attachment clip on WhatsApp and share your live Location Pin."
    )

    # 1. Customer Agent enqueues WhatsApp message asking for Location Pin
    await execute_outbound_whatsapp_enqueue(
        session,
        recipient_phone=customer_phone,
        recipient_role="CUSTOMER",
        message_text=prompt_text,
    )
    await execute_conversation_message_insert(
        session,
        phone=customer_phone,
        actor_role="CUSTOMER",
        direction="OUTBOUND",
        source="CUSTOMER_AGENT",
        message_text=prompt_text,
    )

    # 2. Customer Agent pauses thread until WhatsApp location attachment is received
    raise LocationInterrupt(
        message=f"Awaiting Location Pin attachment from customer {customer_phone}",
        payload={
            "interrupt_type": "AWAIT_LOCATION_PIN",
            "customer_phone": customer_phone,
            "prompt": prompt_text,
        },
    )


# =============================================================================
# TOOL 2: register_customer_profile_tool
# =============================================================================
class RegisterCustomerProfileInput(BaseModel):
    customer_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of the customer (e.g. '9111111111')",
    )
    name: str = Field(
        ...,
        description="Customer's full name (e.g. 'Dinesh')",
    )
    delivery_address: str = Field(
        ...,
        description="Full street address (e.g. 'Flat 301, My Home Bhooja, Hitech City')",
    )
    apartment_name: Optional[str] = Field(
        default=None,
        description="Apartment building / society name (e.g. 'My Home Bhooja')",
    )
    flat_number: Optional[str] = Field(
        default=None,
        description="Flat or door number (e.g. '301')",
    )
    landmark: Optional[str] = Field(
        default=None,
        description="Nearby landmark (e.g. 'Opposite Bio-Diversity Park')",
    )
    city: Optional[str] = Field(
        default="Hyderabad",
        description="Operating city",
    )
    latitude: Optional[float] = Field(
        default=None,
        description="GPS Latitude if already provided (otherwise Customer Agent will request via WhatsApp)",
    )
    longitude: Optional[float] = Field(
        default=None,
        description="GPS Longitude if already provided (otherwise Customer Agent will request via WhatsApp)",
    )
    dietary_preference: Optional[str] = Field(
        default="VEG",
        description="Dietary preference: 'VEG', 'NON_VEG', or 'BOTH'",
    )


async def register_customer_profile(
    session: AsyncSession,
    *,
    customer_phone: str,
    name: str,
    delivery_address: str,
    apartment_name: str | None = None,
    flat_number: str | None = None,
    landmark: str | None = None,
    city: str | None = "Hyderabad",
    latitude: float | None = None,
    longitude: float | None = None,
    dietary_preference: str | None = "VEG",
) -> CustomerProfile:
    """Unified registration tool with Guard 2 Pre-Condition Assertions.

    If lat/lng are missing, calls Customer Agent inside function to request location & return LocationStructuredOutput.
    Once LocationStructuredOutput is returned, inserts complete profile into DB with is_registered = True!
    """
    assert name and len(name.strip()) >= 2, f"Customer name must be >= 2 chars, got '{name}'"
    assert delivery_address and len(delivery_address.strip()) >= 5, (
        f"Delivery address must be >= 5 chars, got '{delivery_address}'"
    )
    if dietary_preference:
        assert dietary_preference in {"VEG", "NON_VEG", "BOTH"}, f"Invalid dietary preference: {dietary_preference}"

    # Condition Check: If latitude or longitude are missing, invoke Customer Agent!
    if latitude is None or longitude is None:
        location_output: LocationStructuredOutput = await invoke_customer_agent(
            session,
            customer_phone=customer_phone,
            task="request_location_pin",
            context={"name": name.strip(), "delivery_address": delivery_address.strip()},
        )
        latitude = location_output.latitude
        longitude = location_output.longitude

    assert -90.0 <= latitude <= 90.0, f"Invalid latitude: {latitude}"
    assert -180.0 <= longitude <= 180.0, f"Invalid longitude: {longitude}"

    # Complete single registration write into DB with is_registered = True!
    profile = await execute_customer_registration_and_location(
        session,
        customer_phone=customer_phone,
        name=name.strip(),
        delivery_address=delivery_address.strip(),
        apartment_name=apartment_name,
        flat_number=flat_number,
        landmark=landmark,
        city=city or "Hyderabad",
        latitude=latitude,
        longitude=longitude,
        dietary_preference=dietary_preference or "VEG",
        is_registered=True,
    )
    return profile


@tool("register_customer_profile_tool", args_schema=RegisterCustomerProfileInput)
async def register_customer_profile_tool(
    customer_phone: str,
    name: str,
    delivery_address: str,
    apartment_name: Optional[str] = None,
    flat_number: Optional[str] = None,
    landmark: Optional[str] = None,
    city: Optional[str] = "Hyderabad",
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    dietary_preference: Optional[str] = "VEG",
) -> str:
    """Register customer profile. If location pin is missing, invokes Customer Agent to request location and get LocationStructuredOutput."""
    from app.db.session import transaction

    async with transaction() as session:
        profile = await register_customer_profile(
            session,
            customer_phone=customer_phone,
            name=name,
            delivery_address=delivery_address,
            apartment_name=apartment_name,
            flat_number=flat_number,
            landmark=landmark,
            city=city,
            latitude=latitude,
            longitude=longitude,
            dietary_preference=dietary_preference,
        )
        return (
            f"Registration COMPLETE for {profile.name} ({profile.customer_phone})!\n"
            f"Delivery Address: {profile.delivery_address}\n"
            f"Location Pin: Saved (Lat {profile.latitude}, Lng {profile.longitude}).\n"
            f"Ready to discover home kitchens near you!"
        )


# =============================================================================
# TOOL 3: find_nearby_home_kitchens_tool
# =============================================================================
class FindNearbyHomeKitchensInput(BaseModel):
    customer_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of the customer (e.g. '9111111111')",
    )
    meal_window: str = Field(
        ...,
        description="Meal window: 'LUNCH' or 'DINNER'",
    )


async def find_nearby_home_kitchens(
    session: AsyncSession,
    *,
    customer_phone: str,
    meal_window: str,
) -> list[dict[str, Any]]:
    """Discover nearby home kitchens using Haversine distance with Guard 2 Pre-Condition Assertions."""
    assert meal_window in {"LUNCH", "DINNER"}, f"Invalid meal window: '{meal_window}'. Must be LUNCH or DINNER"

    customer = await session.get(CustomerProfile, customer_phone)
    assert customer is not None, f"Customer profile not found for phone: {customer_phone}"
    assert customer.is_registered is True and customer.latitude is not None and customer.longitude is not None, (
        f"Customer {customer_phone} has not shared a live location pin yet. "
        f"Please prompt customer to share WhatsApp location pin attachment first."
    )

    cust_lat = float(customer.latitude)
    cust_lng = float(customer.longitude)

    # 1. Fetch active home kitchens
    stmt_chefs = select(ChefProfile).where(ChefProfile.active_status.is_(True))
    chefs = (await session.execute(stmt_chefs)).scalars().all()

    results = []
    for chef in chefs:
        chef_lat = float(chef.latitude)
        chef_lng = float(chef.longitude)
        distance_km = haversine_km(cust_lat, cust_lng, chef_lat, chef_lng)

        # 2. Fetch available dishes for this chef & meal window
        stmt_items = select(ChefMenuItem).where(
            ChefMenuItem.chef_phone == chef.chef_phone,
            ChefMenuItem.is_available.is_(True),
            ChefMenuItem.meal_type.in_([meal_window, "BOTH"]),
        )
        menu_items = (await session.execute(stmt_items)).scalars().all()

        if menu_items:
            results.append({
                "chef_phone": chef.chef_phone,
                "kitchen_name": chef.kitchen_name,
                "chef_name": chef.chef_name,
                "address": chef.address,
                "distance_km": round(distance_km, 2),
                "dietary_type": chef.dietary_type,
                "dishes": [
                    {
                        "menu_item_id": item.menu_item_id,
                        "dish_name": item.dish_name,
                        "unit_price": float(item.unit_price),
                        "dietary_tag": item.dietary_tag,
                    }
                    for item in menu_items
                ],
            })

    # Sort kitchens closest to customer
    results.sort(key=lambda x: x["distance_km"])
    return results


@tool("find_nearby_home_kitchens_tool", args_schema=FindNearbyHomeKitchensInput)
async def find_nearby_home_kitchens_tool(
    customer_phone: str,
    meal_window: str,
) -> str:
    """Find nearby active home kitchens with available dish menus for a specified meal window (LUNCH/DINNER)."""
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        kitchens = await find_nearby_home_kitchens(
            session,
            customer_phone=customer_phone,
            meal_window=meal_window,
        )

        if not kitchens:
            return f"No active home kitchens found serving {meal_window} near customer location."

        lines = [f"Home Kitchens open for {meal_window} near your location:"]
        for idx, k in enumerate(kitchens, 1):
            dish_summary = ", ".join(f"{d['dish_name']} (₹{d['unit_price']:.2f})" for d in k["dishes"])
            lines.append(
                f"{idx}. {k['kitchen_name']} ({k['chef_name']}) — {k['distance_km']} km away [{k['dietary_type']}]\n"
                f"   Address: {k['address']}\n"
                f"   Dishes Available: {dish_summary}"
            )
        return "\n".join(lines)


# =============================================================================
# TOOL 4: view_chef_menu_tool
# =============================================================================
class ViewChefMenuInput(BaseModel):
    chef_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of the chef / home kitchen (e.g. '9876543210')",
    )
    meal_window: str = Field(
        ...,
        description="Meal window: 'LUNCH' or 'DINNER'",
    )


async def view_chef_menu(
    session: AsyncSession,
    *,
    chef_phone: str,
    meal_window: str,
) -> dict[str, Any]:
    """Query menu offerings and availability for a kitchen with Guard 2 Pre-Condition Assertions."""
    assert chef_phone and len(chef_phone) >= 10, f"Invalid chef phone number: {chef_phone}"
    assert meal_window in {"LUNCH", "DINNER"}, f"Invalid meal window: '{meal_window}'. Must be LUNCH or DINNER"

    chef = await session.get(ChefProfile, chef_phone)
    assert chef is not None, f"Kitchen profile not found for phone: {chef_phone}"

    stmt = select(ChefMenuItem).where(
        ChefMenuItem.chef_phone == chef_phone,
        ChefMenuItem.is_available.is_(True),
        ChefMenuItem.meal_type.in_([meal_window, "BOTH"]),
    ).order_by(ChefMenuItem.dish_name)

    items = (await session.execute(stmt)).scalars().all()

    return {
        "chef_phone": chef.chef_phone,
        "kitchen_name": chef.kitchen_name,
        "chef_name": chef.chef_name,
        "address": chef.address,
        "dietary_type": chef.dietary_type,
        "meal_window": meal_window,
        "dishes": [
            {
                "menu_item_id": item.menu_item_id,
                "dish_name": item.dish_name,
                "description": item.description,
                "unit_price": float(item.unit_price),
                "dietary_tag": item.dietary_tag,
                "spice_level": item.spice_level,
                "is_available": item.is_available,
            }
            for item in items
        ],
    }


@tool("view_chef_menu_tool", args_schema=ViewChefMenuInput)
async def view_chef_menu_tool(
    chef_phone: str,
    meal_window: str,
) -> str:
    """View the active food menu catalog and dish prices for a specific home kitchen and meal window."""
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        data = await view_chef_menu(
            session,
            chef_phone=chef_phone,
            meal_window=meal_window,
        )

        if not data["dishes"]:
            return f"No available dishes found for {data['kitchen_name']} ({meal_window})."

        dishes_text = "\n".join(
            f"- [{d['menu_item_id']}] {d['dish_name']} — ₹{d['unit_price']:.2f} "
            f"({d['dietary_tag']}, {d['spice_level']} spice)"
            for d in data["dishes"]
        )

        return (
            f"Menu for {data['kitchen_name']} ({data['chef_name']}) — {meal_window}:\n"
            f"Address: {data['address']}\n"
            f"Dietary Classification: {data['dietary_type']}\n"
            f"Available Dishes:\n{dishes_text}"
        )


# =============================================================================
# TOOL 5: add_item_to_order_tool
# =============================================================================
class AddItemToOrderInput(BaseModel):
    customer_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of the customer (e.g. '9111111111')",
    )
    chef_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of the chef / home kitchen (e.g. '9876543210')",
    )
    menu_item_id: str = Field(
        ...,
        description="Prefixed dish ID (e.g. 'itm_paneer01')",
    )
    quantity: int = Field(
        ...,
        description="Portion quantity to order (e.g. 2)",
    )
    service_date: str = Field(
        ...,
        description="Service date in ISO format YYYY-MM-DD (e.g. '2026-08-01')",
    )
    meal_window: str = Field(
        ...,
        description="Meal window: 'LUNCH' or 'DINNER'",
    )
    special_instructions: Optional[str] = Field(
        default=None,
        description="Optional dish preparation note (e.g. 'Extra gravy, less oil')",
    )


async def add_item_to_order(
    session: AsyncSession,
    *,
    customer_phone: str,
    chef_phone: str,
    menu_item_id: str,
    quantity: int,
    service_date: str,
    meal_window: str,
    special_instructions: str | None = None,
) -> CustomerOrder:
    """Add a dish line item to a customer's cart with Guard 2 Pre-Condition Assertions."""
    assert quantity >= 1, f"Quantity must be at least 1, got {quantity}"
    assert meal_window in {"LUNCH", "DINNER"}, f"Invalid meal window: '{meal_window}'. Must be LUNCH or DINNER"

    # Guard 2 Assert: Check menu item existence and availability
    item = await session.get(ChefMenuItem, menu_item_id)
    assert item is not None, f"Menu item not found: {menu_item_id}"
    assert item.chef_phone == chef_phone, (
        f"Dish {menu_item_id} ('{item.dish_name}') belongs to chef {item.chef_phone}, not chef {chef_phone}."
    )
    assert item.is_available is True, f"Dish '{item.dish_name}' ({menu_item_id}) is currently OUT OF STOCK."

    chef = await session.get(ChefProfile, chef_phone)
    assert chef is not None, f"Kitchen profile not found for phone: {chef_phone}"

    date_obj = date.fromisoformat(service_date)

    # 1. Search for existing active cart (DRAFT_CART or PENDING_PAYMENT)
    stmt_order = select(CustomerOrder).where(
        CustomerOrder.customer_phone == customer_phone,
        CustomerOrder.chef_phone == chef_phone,
        CustomerOrder.service_date == date_obj,
        CustomerOrder.meal_window == meal_window,
        CustomerOrder.status.in_(["DRAFT_CART", "PENDING_PAYMENT"]),
    )
    order = (await session.execute(stmt_order)).scalar_one_or_none()

    # 2. If no active cart exists, create order header via Customer Write Exec #2
    if order is None:
        order = await execute_customer_order_initialization(
            session,
            customer_phone=customer_phone,
            chef_phone=chef_phone,
            kitchen_name=chef.kitchen_name,
            service_date=date_obj,
            meal_window=meal_window,
        )

    # 3. Append/update item and recalculate subtotal via Customer Write Exec #3
    await execute_add_item_to_order(
        session,
        order_id=order.order_id,
        menu_item_id=menu_item_id,
        dish_name=item.dish_name,
        quantity=quantity,
        unit_price=item.unit_price,
        special_instructions=special_instructions,
    )

    await session.refresh(order)
    return order


@tool("add_item_to_order_tool", args_schema=AddItemToOrderInput)
async def add_item_to_order_tool(
    customer_phone: str,
    chef_phone: str,
    menu_item_id: str,
    quantity: int,
    service_date: str,
    meal_window: str,
    special_instructions: Optional[str] = None,
) -> str:
    """Add a dish to the customer's cart, creating an order header if necessary, and updating subtotals."""
    from app.db.session import transaction

    async with transaction() as session:
        order = await add_item_to_order(
            session,
            customer_phone=customer_phone,
            chef_phone=chef_phone,
            menu_item_id=menu_item_id,
            quantity=quantity,
            service_date=service_date,
            meal_window=meal_window,
            special_instructions=special_instructions,
        )

        return (
            f"Successfully updated cart for {order.kitchen_name} (Order ID: {order.order_id}):\n"
            f"Cart Subtotal: ₹{order.cart_subtotal:.2f}\n"
            f"Delivery Fee: ₹{order.delivery_fee:.2f}\n"
            f"Total Amount Payable: ₹{order.total_amount:.2f}"
        )


# =============================================================================
# TOOL 6: get_order_history_tool
# =============================================================================
class GetOrderHistoryInput(BaseModel):
    customer_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of the customer (e.g. '9111111111')",
    )
    limit: Optional[int] = Field(
        default=5,
        description="Number of past order records to retrieve (default 5, max 20)",
    )


async def get_order_history(
    session: AsyncSession,
    *,
    customer_phone: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Retrieve past orders for a customer with Guard 2 Pre-Condition Assertions."""
    assert customer_phone and len(customer_phone) >= 10, f"Invalid customer phone number: {customer_phone}"
    assert 1 <= limit <= 20, f"Limit must be between 1 and 20, got {limit}"

    stmt_orders = (
        select(CustomerOrder)
        .where(CustomerOrder.customer_phone == customer_phone)
        .order_by(CustomerOrder.created_at.desc())
        .limit(limit)
    )
    orders = (await session.execute(stmt_orders)).scalars().all()

    history = []
    for order in orders:
        stmt_items = select(CustomerOrderItem).where(CustomerOrderItem.order_id == order.order_id)
        items = (await session.execute(stmt_items)).scalars().all()

        history.append({
            "order_id": order.order_id,
            "kitchen_name": order.kitchen_name,
            "meal_window": order.meal_window,
            "service_date": order.service_date.isoformat(),
            "status": order.status,
            "cart_subtotal": float(order.cart_subtotal),
            "delivery_fee": float(order.delivery_fee),
            "total_amount": float(order.total_amount),
            "items": [
                {
                    "dish_name": item.dish_name,
                    "quantity": item.quantity,
                    "unit_price": float(item.unit_price),
                    "item_subtotal": float(item.item_subtotal),
                }
                for item in items
            ],
        })

    return history


@tool("get_order_history_tool", args_schema=GetOrderHistoryInput)
async def get_order_history_tool(
    customer_phone: str,
    limit: Optional[int] = 5,
) -> str:
    """Retrieve receipt details and item lists for a customer's past orders."""
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        orders = await get_order_history(
            session,
            customer_phone=customer_phone,
            limit=limit or 5,
        )

        if not orders:
            return f"No past order history found for customer {customer_phone}."

        lines = [f"Order History for customer ({customer_phone}):"]
        for idx, o in enumerate(orders, 1):
            items_str = ", ".join(f"{i['dish_name']} x {i['quantity']}" for i in o["items"])
            lines.append(
                f"{idx}. Order #{o['order_id']} — {o['kitchen_name']} ({o['service_date']} {o['meal_window']}) [{o['status']}]\n"
                f"   Items: {items_str or 'No items'}\n"
                f"   Total Amount Paid: ₹{o['total_amount']:.2f}"
            )
        return "\n".join(lines)


# =============================================================================
# TOOL 7: submit_order_review_tool
# =============================================================================
class SubmitOrderReviewInput(BaseModel):
    order_id: str = Field(
        ...,
        description="Prefixed order ID (e.g. 'ord_chk_101')",
    )
    customer_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of the customer (e.g. '9111111111')",
    )
    rating: int = Field(
        ...,
        description="Rating score from 1 to 5 stars",
    )
    review_text: Optional[str] = Field(
        default=None,
        description="Optional customer feedback comment",
    )


async def submit_order_review(
    session: AsyncSession,
    *,
    order_id: str,
    customer_phone: str,
    rating: int,
    review_text: str | None = None,
) -> CustomerReview:
    """Submit a rating and review for a delivered order with Guard 2 Pre-Condition Assertions."""
    assert 1 <= rating <= 5, f"Rating score must be between 1 and 5 stars, got {rating}"

    # Guard 2 Assert: Check order existence, customer ownership, and DELIVERED status
    order = await session.get(CustomerOrder, order_id)
    assert order is not None, f"Order not found: {order_id}"
    assert order.customer_phone == customer_phone, (
        f"Order {order_id} belongs to customer {order.customer_phone}, not customer {customer_phone}."
    )
    assert order.status == "DELIVERED", (
        f"Cannot submit review for order {order_id} with status '{order.status}'. Order must be DELIVERED first."
    )

    review = await execute_submit_order_review(
        session,
        order_id=order_id,
        customer_phone=customer_phone,
        chef_phone=order.chef_phone,
        chef_rating=rating,
        review_text=review_text,
    )
    return review


@tool("submit_order_review_tool", args_schema=SubmitOrderReviewInput)
async def submit_order_review_tool(
    order_id: str,
    customer_phone: str,
    rating: int,
    review_text: Optional[str] = None,
) -> str:
    """Submit a rating score (1-5 stars) and review comment for a delivered order."""
    from app.db.session import transaction

    async with transaction() as session:
        review = await submit_order_review(
            session,
            order_id=order_id,
            customer_phone=customer_phone,
            rating=rating,
            review_text=review_text,
        )

        stars = "⭐" * review.chef_rating
        comment_str = f' "{review.review_text}"' if review.review_text else ""
        return (
            f"Review successfully submitted for Order #{review.order_id}! {stars} ({review.chef_rating}/5){comment_str}\n"
            f"Thank you for sharing your feedback with the home kitchen!"
        )
