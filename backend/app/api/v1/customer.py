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
from backend.app.api.deps import require_phone

logger = logging.getLogger("homatri_customer")
router = APIRouter(prefix="/customer", tags=["Customer Addresses"])


class AddressCreateSchema(BaseModel):
    id: Optional[str] = None
    address_type: str = Field(default="HOME")  # HOME, WORK, OTHER
    flat_no: str
    street_address: str
    landmark: Optional[str] = None
    phone: str
    cluster: str = "Ghansoli"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    is_default: bool = False


@router.get("/addresses", status_code=status.HTTP_200_OK)
async def get_saved_addresses(
    customer_phone: str = Depends(require_phone),
    db: AsyncSession = Depends(get_db),
) -> List[dict[str, Any]]:

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
            "latitude": float(addr.latitude) if addr.latitude is not None else None,
            "longitude": float(addr.longitude) if addr.longitude is not None else None,
            "is_default": addr.is_default,
        })
    return out


@router.post("/addresses", status_code=status.HTTP_201_CREATED)
async def save_address(
    req: AddressCreateSchema,
    customer_phone: str = Depends(require_phone),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:

    full_addr = f"{req.flat_no}, {req.street_address}{f', Near {req.landmark}' if req.landmark else ''}, {req.cluster}"

    # Update existing address if id provided
    if req.id and not req.id.startswith("addr_"):
        try:
            target_uuid = UUID(req.id)
            existing = await db.get(CustomerAddress, target_uuid)
            if existing and existing.customer_phone == customer_phone:
                existing.address_type = req.address_type.upper()
                existing.flat_no = req.flat_no
                existing.street_address = req.street_address
                existing.landmark = req.landmark
                existing.full_address = full_addr
                existing.phone = req.phone
                existing.cluster = req.cluster
                existing.latitude = req.latitude
                existing.longitude = req.longitude
                if req.is_default:
                    existing.is_default = True
                await db.commit()
                await db.refresh(existing)
                return {
                    "id": str(existing.id),
                    "full_address": existing.full_address,
                    "address_type": existing.address_type,
                    "message": "Delivery address updated successfully.",
                }
        except Exception as e:
            logger.warning(f"Address update lookup notice: {e}")

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
    customer_phone: str = Depends(require_phone),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Delete a saved address."""
    addr = await db.get(CustomerAddress, address_id)
    if not addr or addr.customer_phone != customer_phone:
        raise HTTPException(status_code=404, detail="Address not found.")
    await db.delete(addr)
    await db.commit()
    return {"message": "Address deleted successfully."}
