"""Social reels, comments, likes, and chef follow relationships."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import TS, Base


class ChefReel(Base):
    __tablename__ = "chef_reels"

    reel_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    chef_phone: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("chef_profiles.chef_phone", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    video_url: Mapped[str] = mapped_column(Text, nullable=False)
    hls_playlist_url: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(String(200))
    dish_tag_name: Mapped[str | None] = mapped_column(String(150))
    dish_tag_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    likes_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comments_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TS, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(TS)


class ReelComment(Base):
    __tablename__ = "reel_comments"

    comment_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    reel_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("chef_reels.reel_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_phone: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("customer_profiles.customer_phone", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str | None] = mapped_column(String(50))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    parent_comment_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("reel_comments.comment_id", ondelete="CASCADE"),
        index=True,
    )
    likes_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TS, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(TS)


class ReelLike(Base):
    __tablename__ = "reel_likes"
    __table_args__ = (
        UniqueConstraint("reel_id", "customer_phone", name="uq_reel_likes_reel_customer"),
    )

    like_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    reel_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("chef_reels.reel_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_phone: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("customer_profiles.customer_phone", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())


class ChefFollower(Base):
    __tablename__ = "chef_followers"
    __table_args__ = (
        UniqueConstraint("chef_phone", "customer_phone", name="uq_chef_followers_chef_customer"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    chef_phone: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("chef_profiles.chef_phone", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_phone: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("customer_profiles.customer_phone", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
