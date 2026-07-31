"""Admin domain models (admin_*)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import id_factory
from app.db.base import TS, Base, JSONB, TimestampMixin


class AdminUser(Base, TimestampMixin):
    __tablename__ = "admin_users"

    admin_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("adm"))
    email: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False, default="OPS", index=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(TS)


class AdminActivityLog(Base):
    __tablename__ = "admin_activity_log"

    activity_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("act"))
    admin_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("admin_users.admin_id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    target_table: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    changes_diff: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now(), index=True)


class AdminAiQuery(Base):
    __tablename__ = "admin_ai_queries"

    query_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("aiq"))
    admin_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("admin_users.admin_id"), nullable=False, index=True
    )
    nl_question: Mapped[str] = mapped_column(Text, nullable=False)
    generated_sql: Mapped[str] = mapped_column(Text, nullable=False)
    result_row_count: Mapped[int | None] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
