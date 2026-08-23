"""Bulk catering templates and checkout."""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.app.db.session import SessionFactory
from backend.app.models.catering import BulkCateringOrder, CateringTemplateItem, ChefCateringTemplate
from backend.app.models.chef import ChefProfile
from backend.app.models.customer import CustomerProfile

router = APIRouter(prefix="/bulk", tags=["bulk"])

DEFAULT_TEMPLATES = [
    {
        "template_id": "standard",
        "template_name": "Standard Thali",
        "base_plate_price": 149.00,
        "min_guests": 10,
        "items": [
            {"item_id": "std-roti", "item_name": "Roti / Phulka (4 pcs)", "category": "BREAD", "deduction_value": 25.00, "is_removable": False},
            {"item_id": "std-rice", "item_name": "Steamed Rice", "category": "RICE", "deduction_value": 25.00, "is_removable": True},
            {"item_id": "std-sabzi", "item_name": "Dry Sabzi", "category": "SABZI", "deduction_value": 30.00, "is_removable": True},
            {"item_id": "std-dal", "item_name": "Dal Tadka", "category": "DAL", "deduction_value": 25.00, "is_removable": True},
            {"item_id": "std-sweet", "item_name": "Sweet", "category": "DESSERT", "deduction_value": 20.00, "is_removable": True},
        ],
    },
    {
        "template_id": "deluxe",
        "template_name": "Deluxe Thali",
        "base_plate_price": 199.00,
        "min_guests": 10,
        "items": [
            {"item_id": "dlx-roti", "item_name": "Roti / Phulka (4 pcs)", "category": "BREAD", "deduction_value": 30.00, "is_removable": False},
            {"item_id": "dlx-rice", "item_name": "Jeera Rice", "category": "RICE", "deduction_value": 30.00, "is_removable": True},
            {"item_id": "dlx-sabzi", "item_name": "Dry Sabzi (Chef's Choice)", "category": "SABZI", "deduction_value": 35.00, "is_removable": True},
            {"item_id": "dlx-dal", "item_name": "Dal (Tadka / Fry)", "category": "DAL", "deduction_value": 30.00, "is_removable": True},
            {"item_id": "dlx-protein", "item_name": "Protein Sabzi (Paneer/Soya)", "category": "PROTEIN", "deduction_value": 45.00, "is_removable": True},
            {"item_id": "dlx-sweet", "item_name": "Sweet (Gulab Jamun / Kheer)", "category": "DESSERT", "deduction_value": 29.00, "is_removable": True},
        ],
    },
    {
        "template_id": "feast",
        "template_name": "Grand Feast Thali",
        "base_plate_price": 299.00,
        "min_guests": 10,
        "items": [
            {"item_id": "fst-roti", "item_name": "Roti + Puris", "category": "BREAD", "deduction_value": 40.00, "is_removable": False},
            {"item_id": "fst-rice", "item_name": "Pulao / Jeera Rice", "category": "RICE", "deduction_value": 40.00, "is_removable": True},
            {"item_id": "fst-sabzi", "item_name": "Two Seasonal Sabzis", "category": "SABZI", "deduction_value": 50.00, "is_removable": True},
            {"item_id": "fst-dal", "item_name": "Dal Fry / Kadhi", "category": "DAL", "deduction_value": 35.00, "is_removable": True},
            {"item_id": "fst-protein", "item_name": "Paneer Special", "category": "PROTEIN", "deduction_value": 55.00, "is_removable": True},
            {"item_id": "fst-sweet", "item_name": "Dessert Duo", "category": "DESSERT", "deduction_value": 40.00, "is_removable": True},
        ],
    },
]


class BulkCheckoutIn(BaseModel):
    customer_phone: str
    chef_phone: str | None = None
    event_date: date
    event_time: time
    guest_count: int = Field(ge=10, le=500)
    template_id: str
    removed_item_ids: list[str] = Field(default_factory=list)
    special_event_note: str | None = None


def _price_for(template: dict, removed_item_ids: list[str]) -> Decimal:
    base = Decimal(str(template["base_plate_price"]))
    removed = set(removed_item_ids)
    deduction = Decimal("0.00")
    for item in template["items"]:
        if item["item_id"] in removed and item.get("is_removable", True):
            deduction += Decimal(str(item["deduction_value"]))
    return max(Decimal("1.00"), base - deduction)


@router.get("/templates")
async def list_templates(chef_phone: str | None = None) -> list[dict]:
    if not chef_phone:
        return DEFAULT_TEMPLATES
    async with SessionFactory() as db:
        rows = (
            (
                await db.execute(
                    select(ChefCateringTemplate).where(
                        ChefCateringTemplate.chef_phone == chef_phone,
                        ChefCateringTemplate.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return DEFAULT_TEMPLATES
        out: list[dict] = []
        for row in rows:
            items = (
                (
                    await db.execute(
                        select(CateringTemplateItem).where(CateringTemplateItem.template_id == row.template_id)
                    )
                )
                .scalars()
                .all()
            )
            out.append(
                {
                    "template_id": str(row.template_id),
                    "template_name": row.template_name,
                    "base_plate_price": float(row.base_plate_price),
                    "min_guests": row.min_guests,
                    "items": [
                        {
                            "item_id": str(item.item_id),
                            "item_name": item.item_name,
                            "category": item.category,
                            "deduction_value": float(item.deduction_value),
                            "is_removable": item.is_removable,
                        }
                        for item in items
                    ],
                }
            )
        return out


@router.post("/checkout")
async def checkout(payload: BulkCheckoutIn) -> dict:
    template = next((t for t in DEFAULT_TEMPLATES if t["template_id"] == payload.template_id), None)
    async with SessionFactory() as db:
        chef_phone = payload.chef_phone
        if template is None:
            try:
                tid = UUID(payload.template_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Unknown catering template") from exc
            row = await db.get(ChefCateringTemplate, tid)
            if row is None:
                raise HTTPException(status_code=400, detail="Unknown catering template")
            items = (
                (
                    await db.execute(
                        select(CateringTemplateItem).where(CateringTemplateItem.template_id == row.template_id)
                    )
                )
                .scalars()
                .all()
            )
            template = {
                "template_id": str(row.template_id),
                "base_plate_price": float(row.base_plate_price),
                "min_guests": row.min_guests,
                "items": [
                    {
                        "item_id": str(item.item_id),
                        "deduction_value": float(item.deduction_value),
                        "is_removable": item.is_removable,
                    }
                    for item in items
                ],
            }
            chef_phone = row.chef_phone

        if payload.guest_count < int(template.get("min_guests") or 10):
            raise HTTPException(status_code=400, detail="Guest count is below the template minimum")

        per_plate = _price_for(template, payload.removed_item_ids)
        total = per_plate * payload.guest_count

        customer = await db.get(CustomerProfile, payload.customer_phone)
        if customer is None:
            db.add(
                CustomerProfile(
                    customer_phone=payload.customer_phone,
                    name="Bulk guest",
                    full_name="Bulk guest",
                    delivery_address="Event address pending",
                    is_registered=True,
                )
            )
            await db.flush()

        if not chef_phone:
            chef = (await db.execute(select(ChefProfile).limit(1))).scalar_one_or_none()
            chef_phone = chef.chef_phone if chef else None
        if not chef_phone:
            raise HTTPException(status_code=400, detail="No homemaker is available for bulk catering yet")

        order = BulkCateringOrder(
            customer_phone=payload.customer_phone,
            chef_phone=chef_phone,
            event_date=payload.event_date,
            event_time=payload.event_time,
            guest_count=payload.guest_count,
            per_plate_price=per_plate,
            total_amount=total,
            special_event_note=payload.special_event_note,
            status="PENDING_CHEF_ACCEPTANCE",
        )
        db.add(order)
        await db.commit()
        await db.refresh(order)
        return {
            "bulk_order_id": str(order.bulk_order_id),
            "per_plate_price": float(per_plate),
            "total_amount": float(total),
            "guest_count": payload.guest_count,
            "status": order.status,
        }
