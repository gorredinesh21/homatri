"""Customer Saved Addresses API Router (Zomato / Swiggy Multi-Address Management).

Endpoints:
- GET    /api/v1/customer/addresses (Fetch all saved addresses for active user)
- POST   /api/v1/customer/addresses (Create & save new delivery address)
- DELETE /api/v1/customer/addresses/{address_id} (Remove a saved address)
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.db.session import get_db
from backend.app.models.customer import CustomerAddress
from backend.app.core.security import decode_access_token

logger = logging.getLogger("homatri_customer")
router = APIRouter(prefix="/customer", tags=["Customer Addresses"])


class AddressCreateSchema(BaseModel):
    address_type: str = Field(default="HOME")  # HOME, WORK, OTHER
    flat_no: str
    street_address: str
    landmark: Optional[str] = None
    phone: str
    cluster: str = "Ghansoli"
    latitude: Optional[float] = 19.1234
    longitude: Optional[float] = 73.0123
    is_default: bool = False


@router.get("/addresses", status_code=status.HTTP_200_OK)
async def get_saved_addresses(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> List[dict[str, Any]]:
    """Fetch all saved delivery addresses for the logged-in customer."""
    customer_phone = "7416767453"
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        try:
            payload = decode_access_token(token)
            if payload and payload.get("sub"):
                customer_phone = payload.get("sub")
        except Exception:
            pass

    res = await db.execute(
        select(CustomerAddress)
        .where(CustomerAddress.customer_phone == customer_phone)
        .order_by(CustomerAddress.is_default.desc(), CustomerAddress.created_at.desc())
    )
    addresses = res.scalars().all()

    out = []
    for addr in addresses:
        out.append({
            "id": str(addr.id),
            "address_type": addr.address_type,
            "flat_no": addr.flat_no,
            "street_address": addr.street_address,
            "landmark": addr.landmark,
            "full_address": addr.full_address,
            "phone": addr.phone,
            "cluster": addr.cluster,
            "latitude": float(addr.latitude) if addr.latitude else 19.1234,
            "longitude": float(addr.longitude) if addr.longitude else 73.0123,
            "is_default": addr.is_default,
        })
    return out


@router.post("/addresses", status_code=status.HTTP_201_CREATED)
async def save_address(
    req: AddressCreateSchema,
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Save a new delivery address for the customer (Zomato/Swiggy style)."""
    customer_phone = "7416767453"
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        try:
            payload = decode_access_token(token)
            if payload and payload.get("sub"):
                customer_phone = payload.get("sub")
        except Exception:
            pass

    full_addr = f"{req.flat_no}, {req.street_address}{f', Near {req.landmark}' if req.landmark else ''}, {req.cluster}"

    new_address = CustomerAddress(
        customer_phone=customer_phone,
        address_type=req.address_type.upper(),
        flat_no=req.flat_no,
        street_address=req.street_address,
        landmark=req.landmark,
        full_address=full_addr,
        phone=req.phone,
        cluster=req.cluster,
        latitude=req.latitude,
        longitude=req.longitude,
        is_default=req.is_default,
    )
    db.add(new_address)
    await db.commit()
    await db.refresh(new_address)

    return {
        "id": str(new_address.id),
        "full_address": new_address.full_address,
        "address_type": new_address.address_type,
        "message": "Delivery address saved successfully.",
    }


@router.delete("/addresses/{address_id}", status_code=status.HTTP_200_OK)
async def delete_address(
    address_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Delete a saved address."""
    addr = await db.get(CustomerAddress, address_id)
    if not addr:
        raise HTTPException(status_code=404, detail="Address not found.")
    await db.delete(addr)
    await db.commit()
    return {"message": "Address deleted successfully."}
