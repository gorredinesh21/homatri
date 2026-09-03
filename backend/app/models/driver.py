"""Driver domain models (driver_*)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.ids import id_factory
from backend.app.db.base import Base, JSONB, TS, TimestampMixin


class DriverProfile(Base, TimestampMixin):
    __tablename__ = "driver_profiles"

    driver_phone: Mapped[str] = mapped_column(String(20), primary_key=True)
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), unique=True, nullable=False, default=uuid4)
    driver_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    driving_license_number: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING")
    vehicle_type: Mapped[str] = mapped_column(String(30), nullable=False, default="SCOOTER")
    vehicle_number: Mapped[str] = mapped_column(String(30), nullable=False)
    vehicle_reg_number: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    vehicle_model: Mapped[str | None] = mapped_column(String(50))
    driver_license_number: Mapped[str | None] = mapped_column(String(50))
    assigned_cluster: Mapped[str] = mapped_column(String(100), nullable=False, default="Ghansoli")
    payout_upi_id: Mapped[str | None] = mapped_column(String(100))
    driver_photo_url: Mapped[str | None] = mapped_column(Text)
    alternate_phone: Mapped[str | None] = mapped_column(String(15))
    bank_account_details: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Soft reference to system_delivery_routes (no hard FK — avoids a circular
    # dependency with system_delivery_routes.driver_phone; enforced in app logic).
    current_assigned_route_id: Mapped[str | None] = mapped_column(String(36), index=True)
    is_on_shift: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    on_shift: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    active_status: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)


class DriverTripStatus(Base, TimestampMixin):
    __tablename__ = "driver_trip_status"

    trip_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("trp"))
    driver_phone: Mapped[str] = mapped_column(
        String(15), ForeignKey("driver_profiles.driver_phone"), nullable=False, index=True
    )
    route_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("system_delivery_routes.route_id"), nullable=False, index=True
    )
    service_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    meal_window: Mapped[str] = mapped_column(String(20), nullable=False, default="LUNCH", index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ASSIGNED", index=True)
    current_stop_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    total_stops: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_stops: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trip_started_at: Mapped[datetime | None] = mapped_column(TS)
    trip_completed_at: Mapped[datetime | None] = mapped_column(TS)
    delay_notes: Mapped[str | None] = mapped_column(Text)


class DriverLocationPing(Base, TimestampMixin):
    __tablename__ = "driver_location_pings"

    ping_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("gps"))
    driver_phone: Mapped[str] = mapped_column(
        String(15), ForeignKey("driver_profiles.driver_phone"), nullable=False, index=True
    )
    latitude: Mapped[Decimal] = mapped_column(Numeric(10, 8), nullable=False)
    longitude: Mapped[Decimal] = mapped_column(Numeric(11, 8), nullable=False)
    heading: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    recorded_at: Mapped[datetime] = mapped_column(TS, nullable=False, index=True)
