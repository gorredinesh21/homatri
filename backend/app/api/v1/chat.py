"""Foodie-to-foodie community chat. Direct messages to chefs are blocked."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select

from backend.app.db.session import SessionFactory
from backend.app.models.chat import UserDirectMessage
from backend.app.models.chef import ChefProfile
from backend.app.models.customer import CustomerProfile

router = APIRouter(prefix="/chat", tags=["chat"])

CHEF_DM_BLOCKED = (
    "To protect kitchen cooking quality, chefs cannot be direct-messaged. "
    "Please comment on their reels or order their tiffin!"
)
DEMO_CHEF_PHONES = {"9876500001", "9876500002", "9876500003"}


class SendMessageIn(BaseModel):
    sender_phone: str
    receiver_phone: str
    message_text: str = Field(min_length=1, max_length=2000)


def _message_public(row: UserDirectMessage) -> dict[str, Any]:
    return {
        "message_id": row.message_id,
        "sender_phone": row.sender_phone,
        "receiver_phone": row.receiver_phone,
        "message_text": row.message_text,
        "is_read": row.is_read,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def _assert_not_chef(db, phone: str) -> None:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())[-10:]
    if digits in DEMO_CHEF_PHONES:
        raise HTTPException(status_code=403, detail=CHEF_DM_BLOCKED)
    chef = await db.get(ChefProfile, phone) or await db.get(ChefProfile, digits)
    if chef is not None:
        raise HTTPException(status_code=403, detail=CHEF_DM_BLOCKED)
    customer = await db.get(CustomerProfile, phone)
    if customer is not None and (customer.role or "").upper() == "CHEF":
        raise HTTPException(status_code=403, detail=CHEF_DM_BLOCKED)


@router.post("/send-user-message")
async def send_user_message(payload: SendMessageIn) -> dict[str, Any]:
    text = payload.message_text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    if payload.sender_phone == payload.receiver_phone:
        raise HTTPException(status_code=400, detail="You cannot message yourself")

    async with SessionFactory() as db:
        await _assert_not_chef(db, payload.receiver_phone)
        msg = UserDirectMessage(
            sender_phone=payload.sender_phone,
            receiver_phone=payload.receiver_phone,
            message_text=text,
        )
        db.add(msg)
        await db.commit()
        await db.refresh(msg)
        return {"status": "sent", "message_id": msg.message_id, "message": _message_public(msg)}


@router.get("/thread")
async def chat_thread(user_phone: str, peer_phone: str) -> list[dict[str, Any]]:
    async with SessionFactory() as db:
        await _assert_not_chef(db, peer_phone)
        rows = (
            (
                await db.execute(
                    select(UserDirectMessage)
                    .where(
                        or_(
                            and_(
                                UserDirectMessage.sender_phone == user_phone,
                                UserDirectMessage.receiver_phone == peer_phone,
                            ),
                            and_(
                                UserDirectMessage.sender_phone == peer_phone,
                                UserDirectMessage.receiver_phone == user_phone,
                            ),
                        )
                    )
                    .order_by(UserDirectMessage.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        return [_message_public(row) for row in rows]


@router.get("/inbox")
async def chat_inbox(user_phone: str) -> list[dict[str, Any]]:
    async with SessionFactory() as db:
        rows = (
            (
                await db.execute(
                    select(UserDirectMessage)
                    .where(
                        or_(
                            UserDirectMessage.sender_phone == user_phone,
                            UserDirectMessage.receiver_phone == user_phone,
                        )
                    )
                    .order_by(UserDirectMessage.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        seen: set[str] = set()
        inbox: list[dict[str, Any]] = []
        for row in rows:
            peer = row.receiver_phone if row.sender_phone == user_phone else row.sender_phone
            if peer in seen:
                continue
            seen.add(peer)
            peer_user = await db.get(CustomerProfile, peer)
            inbox.append(
                {
                    "peer_phone": peer,
                    "peer_username": peer_user.username if peer_user else None,
                    "peer_name": (peer_user.full_name or peer_user.name) if peer_user else peer,
                    "last_message": row.message_text,
                    "last_at": row.created_at.isoformat() if row.created_at else None,
                    "is_chef": False,
                }
            )
        return inbox


@router.get("/can-message/{phone}")
async def can_message(phone: str) -> dict[str, Any]:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())[-10:]
    if digits in DEMO_CHEF_PHONES:
        return {"allowed": False, "detail": CHEF_DM_BLOCKED}
    try:
        async with SessionFactory() as db:
            chef = await db.get(ChefProfile, phone) or await db.get(ChefProfile, digits)
            if chef is not None:
                return {"allowed": False, "detail": CHEF_DM_BLOCKED}
            customer = await db.get(CustomerProfile, phone)
            if customer is not None and (customer.role or "").upper() == "CHEF":
                return {"allowed": False, "detail": CHEF_DM_BLOCKED}
    except Exception:
        pass
    return {"allowed": True, "detail": None}
