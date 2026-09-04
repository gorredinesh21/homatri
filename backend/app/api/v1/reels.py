"""Chef reels feed, gallery, and 2-level nested comments."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.app.api.deps import require_role, require_user
from backend.app.core.config import settings
from backend.app.db.session import SessionFactory
from backend.app.models.chef import ChefMenuItem, ChefProfile
from backend.app.models.customer import CustomerProfile
from backend.app.models.social import ChefReel, ReelComment, ReelLike

router = APIRouter(prefix="/reels", tags=["reels"])

SAMPLE_REELS = [
    {
        "reel_id": "sample-malvani-masala",
        "chef_phone": "9876500001",
        "chef_name": "Sunita Deshmukh",
        "kitchen_name": "Surmai Konkan Kitchen",
        "title": "Hand-grinding Malvani masala at 6 AM",
        "caption": "Hand-grinding fresh Malvani masala at 6 AM in Ghansoli! 🌶️",
        "video_url": "https://images.unsplash.com/photo-1556910103-1c02745aae4d?auto=format&fit=crop&w=800&q=80",
        "thumbnail_url": "https://images.unsplash.com/photo-1556910103-1c02745aae4d?auto=format&fit=crop&w=800&q=80",
        "likes_count": 128,
        "comments_count": 14,
        "is_chef": True,
    },
    {
        "reel_id": "sample-kathiyawadi-thali",
        "chef_phone": "9876500002",
        "chef_name": "Meenakshi Joshi",
        "kitchen_name": "Annapurna Shuddh Rasoi",
        "title": "Sunday Kathiyawadi thali packing",
        "caption": "Zero onion, zero garlic Jain thali packing for Sector 8. 🌱",
        "video_url": "https://images.unsplash.com/photo-1589301760014-d929f3979dbc?auto=format&fit=crop&w=800&q=80",
        "thumbnail_url": "https://images.unsplash.com/photo-1589301760014-d929f3979dbc?auto=format&fit=crop&w=800&q=80",
        "likes_count": 96,
        "comments_count": 9,
        "is_chef": True,
    },
    {
        "reel_id": "sample-kolhapuri",
        "chef_phone": "9876500003",
        "chef_name": "Pradip Patil",
        "kitchen_name": "Kolhapuri Flavors",
        "title": "Tambda rassa rolling boil",
        "caption": "Kolhapuri tambda rassa hitting a rolling boil before lunch cutoff. 🔥",
        "video_url": "https://images.unsplash.com/photo-1546069901-ba9599a7e63c?auto=format&fit=crop&w=800&q=80",
        "thumbnail_url": "https://images.unsplash.com/photo-1546069901-ba9599a7e63c?auto=format&fit=crop&w=800&q=80",
        "likes_count": 210,
        "comments_count": 22,
        "is_chef": True,
    },
]


class CommentIn(BaseModel):
    reel_id: str
    user_phone: str
    text: str = Field(min_length=1, max_length=500)
    parent_comment_id: str | None = None
    username: str | None = None
    avatar_url: str | None = None


SAMPLE_COMMENT_STORE: dict[str, list[dict[str, Any]]] = {}


def _sample_comment(payload: CommentIn) -> dict[str, Any]:
    parent_id = payload.parent_comment_id
    if parent_id:
        siblings = SAMPLE_COMMENT_STORE.get(payload.reel_id, [])
        parent = next((c for c in siblings if c["comment_id"] == parent_id), None)
        if parent is None:
            raise HTTPException(status_code=400, detail="Parent comment not found")
        if parent.get("parent_comment_id"):
            raise HTTPException(
                status_code=400,
                detail="Only 2-level replies are allowed. Reply to the original comment instead.",
            )
    comment = {
        "comment_id": str(uuid4()),
        "reel_id": payload.reel_id,
        "user_phone": payload.user_phone,
        "username": payload.username or "foodie",
        "avatar_url": payload.avatar_url or "avatar_tiffin_cartoon_1.png",
        "text": payload.text.strip(),
        "parent_comment_id": parent_id,
        "likes_count": 0,
        "created_at": None,
    }
    SAMPLE_COMMENT_STORE.setdefault(payload.reel_id, []).append(comment)
    return comment


def _comment_public(row: ReelComment) -> dict[str, Any]:
    return {
        "comment_id": str(row.comment_id),
        "reel_id": str(row.reel_id),
        "user_phone": row.customer_phone,
        "username": row.username or "foodie",
        "avatar_url": row.avatar_url or "avatar_tiffin_cartoon_1.png",
        "text": row.content,
        "parent_comment_id": str(row.parent_comment_id) if row.parent_comment_id else None,
        "likes_count": row.likes_count or 0,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _reel_public(row: ChefReel, chef: ChefProfile | None) -> dict[str, Any]:
    return {
        "reel_id": str(row.reel_id),
        "chef_phone": row.chef_phone,
        "chef_name": chef.chef_name if chef else None,
        "kitchen_name": chef.kitchen_name if chef else None,
        "title": row.title,
        "caption": row.title or row.dish_tag_name,
        "video_url": row.video_url,
        "thumbnail_url": row.thumbnail_url or row.video_url,
        "likes_count": row.likes_count,
        "comments_count": row.comments_count,
        "is_chef": True,
    }


@router.get("/feed")
async def reels_feed() -> list[dict[str, Any]]:
    try:
        async with SessionFactory() as db:
            rows = (
                (
                    await db.execute(
                        select(ChefReel)
                        .join(ChefProfile, ChefProfile.chef_phone == ChefReel.chef_phone)
                        .where(ChefReel.deleted_at.is_(None), ChefReel.published.is_(True))
                        .order_by(
                            ChefProfile.is_featured.desc(),
                            ChefReel.created_at.desc(),
                            ChefReel.reel_id.desc(),
                        )
                        .limit(40)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                return []
            out = []
            for row in rows:
                chef = await db.get(ChefProfile, row.chef_phone)
                out.append(_reel_public(row, chef))
            return out
    except Exception:
        raise HTTPException(status_code=503, detail="Could not load reels feed")


@router.get("/gallery/{chef_phone}")
async def chef_video_gallery(chef_phone: str) -> list[dict[str, Any]]:
    async with SessionFactory() as db:
        chef = await db.get(ChefProfile, chef_phone)
        rows = (
            (
                await db.execute(
                    select(ChefReel)
                    .where(
                        ChefReel.chef_phone == chef_phone,
                        ChefReel.deleted_at.is_(None),
                    )
                    .order_by(ChefReel.created_at.desc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return []
        return [_reel_public(row, chef) for row in rows]


@router.get("/{reel_id}/comments")
async def list_comments(reel_id: str) -> list[dict[str, Any]]:
    try:
        rid = UUID(reel_id)
    except ValueError:
        return SAMPLE_COMMENT_STORE.get(reel_id, [])
    async with SessionFactory() as db:
        rows = (
            (
                await db.execute(
                    select(ReelComment)
                    .where(ReelComment.reel_id == rid, ReelComment.deleted_at.is_(None))
                    .order_by(ReelComment.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        return [_comment_public(row) for row in rows]


@router.post("/comments")
async def post_comment(payload: CommentIn) -> dict[str, Any]:
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Comment cannot be empty")

    try:
        rid = UUID(payload.reel_id)
    except ValueError:
        return {"status": "posted", "comment": _sample_comment(payload)}

    parent_id: UUID | None = None
    async with SessionFactory() as db:
        reel = await db.get(ChefReel, rid)
        if reel is None:
            raise HTTPException(status_code=404, detail="Reel not found")

        if payload.parent_comment_id:
            try:
                parent_id = UUID(payload.parent_comment_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid parent comment") from exc
            parent = await db.get(ReelComment, parent_id)
            if parent is None or parent.reel_id != rid:
                raise HTTPException(status_code=400, detail="Parent comment not found")
            if parent.parent_comment_id is not None:
                raise HTTPException(
                    status_code=400,
                    detail="Only 2-level replies are allowed. Reply to the original comment instead.",
                )

        user = await db.get(CustomerProfile, payload.user_phone)
        username = payload.username or (user.username if user else None) or "foodie"
        avatar = payload.avatar_url or (user.avatar_url if user else None) or "avatar_tiffin_cartoon_1.png"

        comment = ReelComment(
            comment_id=uuid4(),
            reel_id=rid,
            customer_phone=payload.user_phone,
            content=text,
            username=username,
            avatar_url=avatar,
            parent_comment_id=parent_id,
            likes_count=0,
        )
        db.add(comment)
        reel.comments_count = (reel.comments_count or 0) + 1
        await db.commit()
        await db.refresh(comment)
        return {"status": "posted", "comment": _comment_public(comment)}


MAX_REEL_BYTES = 50 * 1024 * 1024


@router.post("/upload")
async def upload_reel(
    video: UploadFile = File(...),
    caption: str = Form(""),
    featured_menu_item_id: str | None = Form(default=None),
    payload: dict = Depends(require_role("CHEF")),
):
    data = await video.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty video file")
    if len(data) > MAX_REEL_BYTES:
        raise HTTPException(status_code=400, detail="Max upload size is 50 MB")
    suffix = Path(video.filename or "clip.mp4").suffix.lower() or ".mp4"
    if suffix not in {".mp4", ".mov", ".webm"}:
        raise HTTPException(status_code=400, detail="Upload mp4, mov, or webm")
    reel_id = uuid4()
    folder = Path(settings.uploads_dir) / "reels"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{reel_id}{suffix}"
    dest.write_bytes(data)
    public_url = f"/media/reels/{dest.name}"
    dish_name = None
    dish_price = None
    async with SessionFactory() as db:
        if featured_menu_item_id:
            item = await db.get(ChefMenuItem, featured_menu_item_id)
            if item and item.chef_phone == payload["sub"]:
                dish_name = item.dish_name
                dish_price = item.unit_price
        row = ChefReel(
            reel_id=reel_id,
            chef_phone=payload["sub"],
            video_url=public_url,
            thumbnail_url=public_url,
            title=caption.strip() or "Kitchen reel",
            dish_tag_name=dish_name,
            dish_tag_price=dish_price,
            published=True,
            published_at=datetime.now(timezone.utc),
        )
        db.add(row)
        await db.commit()
        chef = await db.get(ChefProfile, payload["sub"])
        return {"status": "ok", "reel": _reel_public(row, chef)}


@router.post("/{reel_id}/like")
async def like_reel(reel_id: str, payload: dict = Depends(require_user)):
    try:
        rid = UUID(reel_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Reel not found") from exc
    async with SessionFactory() as db:
        reel = await db.get(ChefReel, rid)
        if reel is None:
            raise HTTPException(status_code=404, detail="Reel not found")
        existing = (
            await db.execute(
                select(ReelLike).where(ReelLike.reel_id == rid, ReelLike.customer_phone == payload["sub"])
            )
        ).scalar_one_or_none()
        if existing:
            await db.delete(existing)
            reel.likes_count = max(0, (reel.likes_count or 0) - 1)
            liked = False
        else:
            db.add(ReelLike(reel_id=rid, customer_phone=payload["sub"]))
            reel.likes_count = (reel.likes_count or 0) + 1
            liked = True
        await db.commit()
        return {"liked": liked, "likes_count": reel.likes_count}
