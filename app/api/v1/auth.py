"""Role Verification API Endpoint (app/api/v1/auth.py).

Provides authorization gate checks for Chef and Driver Web Clone portals.
"""

from __future__ import annotations

from typing import Any, Literal
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.db.session import SessionFactory
from app.models.chef import ChefProfile
from app.models.customer import CustomerProfile
from app.models.driver import DriverProfile

router = APIRouter(prefix="/auth", tags=["Auth & Portal Verification"])


class VerifyRoleRequest(BaseModel):
    phone: str = Field(..., description="Normalized 10-digit phone number (e.g. '9876543210')")
    requested_role: Literal["CUSTOMER", "CHEF", "DRIVER"] = Field(..., description="Requested portal role")


class VerifyRoleResponse(BaseModel):
    authorized: bool
    phone: str
    role: str
    name: str
    details: dict[str, Any]
    message: str


@router.post("/verify_role", response_model=VerifyRoleResponse)
async def verify_user_role(req: VerifyRoleRequest):
    """Verify if a phone number is authorized to access the requested portal role."""
    phone = req.phone.strip()
    if not phone or len(phone) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number must be at least 10 digits.",
        )

    async with SessionFactory() as session:
        if req.requested_role == "CHEF":
            chef = await session.get(ChefProfile, phone)
            if not chef:
                return VerifyRoleResponse(
                    authorized=False,
                    phone=phone,
                    role="CHEF",
                    name="Unknown Chef",
                    details={},
                    message=f"❌ Access Denied: Phone number {phone} is not registered as an authorized Home Chef.",
                )
            return VerifyRoleResponse(
                authorized=True,
                phone=phone,
                role="CHEF",
                name=chef.chef_name,
                details={
                    "kitchen_name": chef.kitchen_name,
                    "address": chef.address,
                    "is_active": chef.active_status == "ACTIVE",
                },
                message=f"✅ Welcome, {chef.chef_name} ({chef.kitchen_name})!",
            )

        elif req.requested_role == "DRIVER":
            driver = await session.get(DriverProfile, phone)
            if not driver:
                return VerifyRoleResponse(
                    authorized=False,
                    phone=phone,
                    role="DRIVER",
                    name="Unknown Driver",
                    details={},
                    message=f"❌ Access Denied: Phone number {phone} is not registered as an authorized Delivery Driver.",
                )
            return VerifyRoleResponse(
                authorized=True,
                phone=phone,
                role="DRIVER",
                name=driver.driver_name,
                details={
                    "vehicle_type": driver.vehicle_type,
                    "vehicle_number": driver.vehicle_number,
                    "is_on_shift": driver.is_on_shift,
                },
                message=f"✅ Welcome, Driver {driver.driver_name}!",
            )

        else:  # CUSTOMER
            cust = await session.get(CustomerProfile, phone)
            name = cust.name if cust else "New Customer"
            return VerifyRoleResponse(
                authorized=True,
                phone=phone,
                role="CUSTOMER",
                name=name,
                details={
                    "is_registered": cust is not None,
                    "delivery_address": cust.delivery_address if cust else None,
                },
                message=f"✅ Welcome to Homaatri, {name}!",
            )
