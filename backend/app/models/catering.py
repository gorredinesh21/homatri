"""Bulk catering templates and event orders."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import TS, Base


class ChefCateringTemplate(Base):
    __tablename__ = "chef_catering_templates"

    template_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    chef_phone: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("chef_profiles.chef_phone", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    template_name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_plate_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    min_guests: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())


class CateringTemplateItem(Base):
    __tablename__ = "catering_template_items"
    __table_args__ = (
        CheckConstraint(
            "category IN ('BREAD', 'RICE', 'SABZI', 'DAL', 'PROTEIN', 'DESSERT')",
            name="ck_catering_template_items_category",
        ),
    )

    item_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    template_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("chef_catering_templates.template_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    deduction_value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    is_removable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class BulkCateringOrder(Base):
    __tablename__ = "bulk_catering_orders"

    bulk_order_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    customer_phone: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("customer_profiles.customer_phone", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    chef_phone: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("chef_profiles.chef_phone", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    event_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    event_time: Mapped[time] = mapped_column(Time, nullable=False)
    guest_count: Mapped[int] = mapped_column(Integer, nullable=False)
    per_plate_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    advance_amount_paid: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="PENDING_CHEF_ACCEPTANCE", index=True
    )
    special_event_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
