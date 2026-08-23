"""Authentication, OTP, and 30-day refresh session models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import TS, Base


class OtpVerification(Base):
    """Phone OTP challenge record. One active row per phone number."""

    __tablename__ = "otp_verifications"

    phone_number: Mapped[str] = mapped_column(String(20), primary_key=True)
    otp_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    attempts_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(TS, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())


class UserSession(Base):
    """Long-lived refresh session (30 days) backing HttpOnly refresh cookies."""

    __tablename__ = "user_sessions"
    __table_args__ = (
        CheckConstraint(
            "user_role IN ('CUSTOMER', 'CHEF', 'RIDER', 'ADMIN')",
            name="ck_user_sessions_user_role",
        ),
        Index("ix_user_sessions_user_id_role", "user_id", "user_role"),
        Index("ix_user_sessions_expires_at", "expires_at"),
    )

    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    user_role: Mapped[str] = mapped_column(String(20), nullable=False)
    refresh_token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    device_info: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    expires_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    is_revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
