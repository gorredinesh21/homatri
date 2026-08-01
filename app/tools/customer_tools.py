"""Customer Domain LLM Tools (Category 2).

Encapsulates Customer Concierge Agent tools with Guard 2 Pre-Condition Assertions.
Tool 1: get_customer_profile_tool (Read-only, Same Domain).
Tool 2: register_customer_profile_tool (Unified Customer Registration Tool, Write Executor #4).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.executors.customer import execute_customer_registration_and_location
from app.models.customer import CustomerProfile


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
# TOOL 2: register_customer_profile_tool
# =============================================================================
class RegisterCustomerProfileInput(BaseModel):
    customer_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of the customer (e.g. '9111111111')",
    )
    name: Optional[str] = Field(
        default=None,
        description="Customer's full name (e.g. 'Dinesh')",
    )
    delivery_address: Optional[str] = Field(
        default=None,
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
        description="GPS Latitude if location pin attachment is received",
    )
    longitude: Optional[float] = Field(
        default=None,
        description="GPS Longitude if location pin attachment is received",
    )
    dietary_preference: Optional[str] = Field(
        default="VEG",
        description="Dietary preference: 'VEG', 'NON_VEG', or 'BOTH'",
    )


async def register_customer_profile(
    session: AsyncSession,
    *,
    customer_phone: str,
    name: str | None = None,
    delivery_address: str | None = None,
    apartment_name: str | None = None,
    flat_number: str | None = None,
    landmark: str | None = None,
    city: str | None = "Hyderabad",
    latitude: float | None = None,
    longitude: float | None = None,
    dietary_preference: str | None = "VEG",
) -> CustomerProfile:
    """Unified registration tool executed by Customer Agent with Guard 2 Pre-Condition Assertions."""
    assert customer_phone and len(customer_phone) >= 10, f"Invalid customer phone number: {customer_phone}"

    existing = await session.get(CustomerProfile, customer_phone)
    if existing is None:
        assert name and len(name.strip()) >= 2, f"Customer name required and must be >= 2 chars, got '{name}'"
        assert delivery_address and len(delivery_address.strip()) >= 5, (
            f"Delivery address required and must be >= 5 chars, got '{delivery_address}'"
        )
        final_name = name.strip()
        final_address = delivery_address.strip()
    else:
        final_name = name.strip() if name else existing.name
        final_address = delivery_address.strip() if delivery_address else existing.delivery_address

    if latitude is not None:
        assert -90.0 <= latitude <= 90.0, f"Invalid latitude: {latitude}"
    if longitude is not None:
        assert -180.0 <= longitude <= 180.0, f"Invalid longitude: {longitude}"
    if dietary_preference:
        assert dietary_preference in {"VEG", "NON_VEG", "BOTH"}, f"Invalid dietary preference: {dietary_preference}"

    # Registration complete ONLY when both lat and lng are saved
    has_location = (
        (latitude is not None and longitude is not None)
        or (existing is not None and existing.latitude is not None and existing.longitude is not None)
    )

    profile = await execute_customer_registration_and_location(
        session,
        customer_phone=customer_phone,
        name=final_name,
        delivery_address=final_address,
        apartment_name=apartment_name if apartment_name is not None else (existing.apartment_name if existing else None),
        flat_number=flat_number if flat_number is not None else (existing.flat_number if existing else None),
        landmark=landmark if landmark is not None else (existing.landmark if existing else None),
        city=city or (existing.city if existing else "Hyderabad"),
        latitude=latitude if latitude is not None else (float(existing.latitude) if existing and existing.latitude is not None else None),
        longitude=longitude if longitude is not None else (float(existing.longitude) if existing and existing.longitude is not None else None),
        dietary_preference=dietary_preference or (existing.dietary_preference if existing else "VEG"),
        is_registered=has_location,
    )
    return profile


@tool("register_customer_profile_tool", args_schema=RegisterCustomerProfileInput)
async def register_customer_profile_tool(
    customer_phone: str,
    name: Optional[str] = None,
    delivery_address: Optional[str] = None,
    apartment_name: Optional[str] = None,
    flat_number: Optional[str] = None,
    landmark: Optional[str] = None,
    city: Optional[str] = "Hyderabad",
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    dietary_preference: Optional[str] = "VEG",
) -> str:
    """Register customer or save location pin. Used by Customer Agent for customer onboarding."""
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

        if profile.is_registered:
            return (
                f"Registration COMPLETE for {profile.name} ({profile.customer_phone})!\n"
                f"Delivery Address: {profile.delivery_address}\n"
                f"Location Pin: Saved (Lat {profile.latitude}, Lng {profile.longitude}).\n"
                f"Ready to discover home kitchens near you!"
            )
        else:
            return (
                f"Profile details saved for {profile.name} ({profile.customer_phone}).\n"
                f"Status: PENDING_LOCATION_PIN.\n"
                f"Action Required by Customer Agent: Send WhatsApp message to customer asking them to tap attachment clip and share Location Pin."
            )
