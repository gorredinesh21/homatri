"""Customer Domain LLM Tools (Category 2).

Encapsulates Customer Concierge Agent tools with Guard 2 Pre-Condition Assertions.
Tool 1: get_customer_profile_tool (Read-only, Same Domain).
Tool 2: register_customer_profile_tool (Invokes Customer Agent for Structured Location Output).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession


from app.core.exceptions import LocationInterrupt
from app.executors.customer import execute_customer_registration_and_location
from app.executors.master import execute_conversation_message_insert, execute_outbound_whatsapp_enqueue
from app.models.customer import CustomerProfile


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
