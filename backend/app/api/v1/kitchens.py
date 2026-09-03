"""Public kitchen discovery from chef_profiles + menus."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.session import get_db
from backend.app.models.chef import ChefProfile
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
