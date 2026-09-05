"""Public kitchen discovery from chef_profiles + menus."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.session import get_db
from backend.app.models.chef import ChefProfile
from backend.app.models.customer import CustomerProfile, CustomerReview
from backend.app.services.kitchens import list_kitchens, serialize_kitchen

router = APIRouter(tags=["Kitchens"])


@router.get("/kitchens")
@router.get("/chefs")
async def get_kitchens(
    cluster: str | None = Query(default="Ghansoli"),
    meal_window: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    window = (meal_window or "").upper() or None
    if window and window not in {"LUNCH", "DINNER"}:
        raise HTTPException(status_code=400, detail="meal_window must be LUNCH or DINNER")
    return await list_kitchens(db, cluster=cluster, meal_window=window)


@router.get("/kitchens/{chef_phone}")
@router.get("/chefs/{chef_phone}")
async def get_kitchen(chef_phone: str, db: AsyncSession = Depends(get_db)):
    chef = await db.get(ChefProfile, chef_phone)
    if chef is None or chef.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Kitchen not found")
    return await serialize_kitchen(db, chef)


def _display_name(full_name: str | None, fallback_name: str | None) -> str:
    """First name + last initial (privacy-safe) for public testimonials."""
    source = (full_name or fallback_name or "").strip()
    parts = source.split()
    if not parts:
        return "Homatri customer"
    if len(parts) == 1:
        return parts[0].capitalize()
    return f"{parts[0].capitalize()} {parts[-1][:1].upper()}."


@router.get("/reviews/featured")
async def featured_reviews(
    limit: int = Query(default=6, ge=1, le=12),
    db: AsyncSession = Depends(get_db),
):
    """Public customer testimonials (real delivered-order reviews, consent via is_public)."""
    rows = (
        await db.execute(
            select(CustomerReview, CustomerProfile, ChefProfile)
            .join(CustomerProfile, CustomerProfile.customer_phone == CustomerReview.customer_phone)
            .join(ChefProfile, ChefProfile.chef_phone == CustomerReview.chef_phone)
            .where(
                CustomerReview.is_public.is_(True),
                CustomerReview.review_text.isnot(None),
                CustomerReview.review_text != "",
                ChefProfile.deleted_at.is_(None),
            )
            .order_by(CustomerReview.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "reviewId": review.review_id,
            "rating": review.chef_rating,
            "text": review.review_text,
            "customerName": _display_name(customer.full_name, customer.name),
            "kitchenName": chef.kitchen_name,
            "region": chef.hometown_region,
            "createdAt": review.created_at.isoformat(),
        }
        for review, customer, chef in rows
    ]
