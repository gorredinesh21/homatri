"""Shared runtime model — the unified conversation log."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import id_factory
from app.db.base import TS, Base, JSONB


class ConversationMessage(Base):
    """Unified, INSERT-ONLY chat log (inbound + outbound). Runtime-written.

    Context Assembler reads the last 3-4 inbound + 3-4 outbound for a phone,
    ordered by created_at, before every LLM call.
    """

    __tablename__ = "conversation_messages"

    message_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("msg"))
    phone: Mapped[str] = mapped_column(String(15), nullable=False, index=True)
    actor_role: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    message_type: Mapped[str] = mapped_column(String(30), nullable=False, default="TEXT")
    message_text: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(11, 8))
    media_ref: Mapped[str | None] = mapped_column(Text)
    related_order_id: Mapped[str | None] = mapped_column(String(36), index=True)
    wa_message_id: Mapped[str | None] = mapped_column(String(100), unique=True)  # inbound dedup
    raw_payload: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now(), index=True)
