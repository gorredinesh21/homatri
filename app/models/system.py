"""Master / System domain models (system_*)."""

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


class SystemMealWindow(Base, TimestampMixin):
    __tablename__ = "system_meal_windows"
    __table_args__ = (UniqueConstraint("service_date", "meal_type", name="uq_meal_window_slot"),)

    window_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("win"))
    service_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    meal_type: Mapped[str] = mapped_column(String(20), nullable=False, default="LUNCH", index=True)
    cutoff_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="OPEN", index=True)
    total_confirmed_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_revenue: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    locked_at: Mapped[datetime | None] = mapped_column(TS)
    completed_at: Mapped[datetime | None] = mapped_column(TS)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="GENERAL", index=True)
    description: Mapped[str | None] = mapped_column(Text)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by_admin_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("admin_users.admin_id")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TS, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SystemRouteOptimizationRun(Base):
    __tablename__ = "system_route_optimization_runs"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("run"))
    window_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("system_meal_windows.window_id"), nullable=False, index=True
    )
    service_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    meal_window: Mapped[str] = mapped_column(String(20), nullable=False, default="LUNCH")
    total_input_shipments: Mapped[int] = mapped_column(Integer, nullable=False)
    total_input_vehicles: Mapped[int] = mapped_column(Integer, nullable=False)
    total_routes_generated: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_request_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    raw_response_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    api_latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), default=Decimal("0.0000"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="SUCCESS", index=True)
    error_detail: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())


class SystemDeliveryRoute(Base, TimestampMixin):
    __tablename__ = "system_delivery_routes"

    route_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("rt"))
    window_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("system_meal_windows.window_id"), nullable=False, index=True
    )
    driver_phone: Mapped[str] = mapped_column(
        String(15), ForeignKey("driver_profiles.driver_phone"), nullable=False, index=True
    )
    service_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    meal_window: Mapped[str] = mapped_column(String(20), nullable=False, default="LUNCH", index=True)
    total_stops: Mapped[int] = mapped_column(Integer, nullable=False)
    total_orders: Mapped[int] = mapped_column(Integer, nullable=False)
    total_distance_km: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    estimated_duration_mins: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ASSIGNED", index=True)
    encoded_polyline: Mapped[str | None] = mapped_column(Text)
    optimized_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())


class SystemDeliveryStop(Base, TimestampMixin):
    __tablename__ = "system_delivery_stops"
    __table_args__ = (UniqueConstraint("route_id", "stop_index", name="uq_route_stop"),)

    stop_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("stp"))
    route_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("system_delivery_routes.route_id"), nullable=False, index=True
    )
    stop_index: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    stop_type: Mapped[str] = mapped_column(String(20), nullable=False, default="DROPOFF_GATE", index=True)
    target_ref_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    location_name: Mapped[str] = mapped_column(String(100), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    latitude: Mapped[Decimal] = mapped_column(Numeric(10, 8), nullable=False)
    longitude: Mapped[Decimal] = mapped_column(Numeric(11, 8), nullable=False)
    single_leg_maps_url: Mapped[str | None] = mapped_column(Text)
    estimated_arrival: Mapped[datetime] = mapped_column(TS, nullable=False)
    actual_arrival: Mapped[datetime | None] = mapped_column(TS)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)


class SystemDeliveryStopOrder(Base):
    __tablename__ = "system_delivery_stop_orders"

    stop_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("system_delivery_stops.stop_id"), primary_key=True
    )
    order_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("customer_orders.order_id"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())


class SystemHitlSession(Base):
    __tablename__ = "system_hitl_sessions"

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("hitl"))
    thread_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    interrupt_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    waiting_on_role: Mapped[str] = mapped_column(String(20), nullable=False)
    waiting_on_phone: Mapped[str | None] = mapped_column(String(15), index=True)
    order_id: Mapped[str | None] = mapped_column(String(36), index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    default_on_expiry: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="WAITING", index=True)
    expires_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())


class SystemPaymentWebhookEvent(Base):
    __tablename__ = "system_payment_webhook_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("evt"))
    gateway: Mapped[str] = mapped_column(String(50), nullable=False, default="RAZORPAY")
    gateway_event_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payment_id: Mapped[str | None] = mapped_column(String(36), index=True)
    order_id: Mapped[str | None] = mapped_column(String(36), index=True)
    signature_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    processing_status: Mapped[str] = mapped_column(String(20), nullable=False, default="RECEIVED", index=True)
    error_detail: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(TS)


class SystemAgentLog(Base):
    __tablename__ = "system_agent_logs"

    log_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("log"))
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    source_role: Mapped[str] = mapped_column(String(20), nullable=False)
    target_role: Mapped[str | None] = mapped_column(String(20))
    order_id: Mapped[str | None] = mapped_column(String(36), index=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="INFO")
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now(), index=True)


class SystemOutboundQueue(Base):
    __tablename__ = "system_outbound_queue"

    message_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("out"))
    recipient_phone: Mapped[str] = mapped_column(String(15), nullable=False, index=True)
    recipient_role: Mapped[str] = mapped_column(String(20), nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(String(30), nullable=False, default="TEXT")
    template_name: Mapped[str | None] = mapped_column(String(100))
    wa_message_id: Mapped[str | None] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="QUEUED", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_detail: Mapped[str | None] = mapped_column(Text)
    related_order_id: Mapped[str | None] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(TS)
