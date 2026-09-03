"""User-to-user community direct messages (chefs are never recipients)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.ids import id_factory
from backend.app.db.base import TS, Base


class UserDirectMessage(Base):
    __tablename__ = "user_direct_messages"

    message_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("udm"))
    sender_phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    receiver_phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
