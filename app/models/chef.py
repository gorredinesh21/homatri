"""Chef domain models (chef_*)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import id_factory
from app.db.base import TS, Base, JSONB, TimestampMixin


class ChefProfile(Base, TimestampMixin):
    __tablename__ = "chef_profiles"

    chef_phone: Mapped[str] = mapped_column(String(15), primary_key=True)
    kitchen_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    chef_name: Mapped[str] = mapped_column(String(100), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    apartment_or_locality: Mapped[str | None] = mapped_column(String(100), index=True)
    city: Mapped[str] = mapped_column(String(50), nullable=False, default="Hyderabad")
    pincode: Mapped[str | None] = mapped_column(String(10))
    latitude: Mapped[Decimal] = mapped_column(Numeric(10, 8), nullable=False)
    longitude: Mapped[Decimal] = mapped_column(Numeric(11, 8), nullable=False)
    fssai_license_number: Mapped[str | None] = mapped_column(String(50))
    dietary_type: Mapped[str | None] = mapped_column(String(20))
    kitchen_bio: Mapped[str | None] = mapped_column(Text)
    profile_image_url: Mapped[str | None] = mapped_column(Text)
    alternate_phone: Mapped[str | None] = mapped_column(String(15))
    bank_account_details: Mapped[dict] = mapped_column(JSONB, default=dict)
    operating_days: Mapped[list] = mapped_column(
        JSONB, default=lambda: ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
    )
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active_status: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)


class ChefMenuItem(Base, TimestampMixin):
    __tablename__ = "chef_menu_items"

    menu_item_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("itm"))
    chef_phone: Mapped[str] = mapped_column(
        String(15), ForeignKey("chef_profiles.chef_phone"), nullable=False, index=True
    )
    dish_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    meal_type: Mapped[str] = mapped_column(String(20), nullable=False, default="LUNCH", index=True)
    dietary_tag: Mapped[str | None] = mapped_column(String(20), default="VEG")
    spice_level: Mapped[str | None] = mapped_column(String(20), default="MEDIUM")
    allergens: Mapped[list] = mapped_column(JSONB, default=list)
    preparation_time_mins: Mapped[int | None] = mapped_column(Integer)
    packaging_type: Mapped[str | None] = mapped_column(String(50), default="3_COMPARTMENT_BOX")
    image_url: Mapped[str | None] = mapped_column(Text)
    max_availability: Mapped[int | None] = mapped_column(Integer, default=10)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)


class ChefDailyInventory(Base, TimestampMixin):
    __tablename__ = "chef_daily_inventory"
    __table_args__ = (
        UniqueConstraint(
            "chef_phone", "menu_item_id", "service_date", "meal_window",
            name="uq_chef_inventory_slot",
        ),
    )

    inventory_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("inv"))
    chef_phone: Mapped[str] = mapped_column(
        String(15), ForeignKey("chef_profiles.chef_phone"), nullable=False, index=True
    )
    menu_item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chef_menu_items.menu_item_id"), nullable=False, index=True
    )
    service_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    meal_window: Mapped[str] = mapped_column(String(20), nullable=False, default="LUNCH", index=True)
    max_capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    is_unlimited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text)


class ChefOrderReadiness(Base):
    __tablename__ = "chef_order_readiness"

    readiness_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("red"))
    order_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("customer_orders.order_id"), nullable=False, index=True
    )
    chef_phone: Mapped[str] = mapped_column(
        String(15), ForeignKey("chef_profiles.chef_phone"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PACKED_READY", index=True)
    packed_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    box_count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    special_packing_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
