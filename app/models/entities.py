"""Homaatri relational schema.

Roles are isolated (USERS -> CHEFS / DRIVERS), orders carry a full lifecycle,
and in-flight modifications are captured as OrderChangeRequest rows awaiting
chef/driver acceptance. Enums are stored as strings (portable to SQLite tests).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid
from app.db.types import EmbeddingVector
from app.models.enums import (
    ChangeStatus,
    ChangeType,
    DeliveryStatus,
    OrderStatus,
    PaymentStatus,
    UserRole,
)

EMBEDDING_DIM = 384


def _enum(py_enum, name: str):
    """String-backed enum column (native_enum=False => portable VARCHAR+CHECK)."""
    return SAEnum(py_enum, name=name, native_enum=False, length=32, validate_strings=True)


# ── People ──────────────────────────────────────────────────────────────────
class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[UserRole] = mapped_column(_enum(UserRole, "user_role"))

    chef: Mapped[Optional["Chef"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    driver: Mapped[Optional["Driver"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class Chef(Base, TimestampMixin):
    __tablename__ = "chefs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    kitchen_name: Mapped[str] = mapped_column(String(120))
    kitchen_address: Mapped[str] = mapped_column(String(255))
    gps_coordinates: Mapped[str] = mapped_column(String(40))  # "lat,lng"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    max_daily_capacity: Mapped[int] = mapped_column(Integer, default=20)

    user: Mapped["User"] = relationship(back_populates="chef")
    menu_items: Mapped[list["MenuItem"]] = relationship(
        back_populates="chef", cascade="all, delete-orphan"
    )


class Driver(Base, TimestampMixin):
    __tablename__ = "drivers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    vehicle_type: Mapped[str] = mapped_column(String(60))
    license_plate: Mapped[str] = mapped_column(String(20))
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    current_gps_coordinates: Mapped[str] = mapped_column(String(40))

    user: Mapped["User"] = relationship(back_populates="driver")


class MenuItem(Base, TimestampMixin):
    __tablename__ = "menu_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    chef_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chefs.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    price: Mapped[float] = mapped_column(Float)
    description: Mapped[str] = mapped_column(String(255), default="")
    available: Mapped[bool] = mapped_column(Boolean, default=True)

    chef: Mapped["Chef"] = relationship(back_populates="menu_items")


# ── Orders ──────────────────────────────────────────────────────────────────
class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    chef_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chefs.id"), index=True)
    status: Mapped[OrderStatus] = mapped_column(
        _enum(OrderStatus, "order_status"), default=OrderStatus.PENDING, index=True
    )

    customer_name: Mapped[str] = mapped_column(String(120), default="")
    delivery_address: Mapped[str] = mapped_column(String(255), default="")
    delivery_gps: Mapped[str] = mapped_column(String(40), default="")
    requested_delivery_time: Mapped[str] = mapped_column(String(40), default="")

    subtotal: Mapped[float] = mapped_column(Float, default=0.0)
    delivery_fee: Mapped[float] = mapped_column(Float, default=0.0)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    amount_paid: Mapped[float] = mapped_column(Float, default=0.0)  # cumulative paid (for top-ups)
    notes: Mapped[str] = mapped_column(Text, default="")

    @property
    def balance_due(self) -> float:
        return round(max(0.0, self.total - self.amount_paid), 2)

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    payment: Mapped[Optional["Payment"]] = relationship(
        back_populates="order", uselist=False, cascade="all, delete-orphan"
    )
    delivery: Mapped[Optional["Delivery"]] = relationship(
        back_populates="order", uselist=False, cascade="all, delete-orphan"
    )
    change_requests: Mapped[list["OrderChangeRequest"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )

    def recompute_totals(self, delivery_fee: float | None = None) -> None:
        self.subtotal = round(sum(i.line_total for i in self.items), 2)
        if delivery_fee is not None:
            self.delivery_fee = delivery_fee
        self.total = round(self.subtotal + self.delivery_fee, 2)


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    menu_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("menu_items.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(120))
    unit_price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[int] = mapped_column(Integer, default=1)

    order: Mapped["Order"] = relationship(back_populates="items")

    @property
    def line_total(self) -> float:
        return round(self.unit_price * self.quantity, 2)


class Delivery(Base, TimestampMixin):
    __tablename__ = "deliveries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id"), unique=True, index=True
    )
    driver_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("drivers.id"), nullable=True, index=True
    )
    status: Mapped[DeliveryStatus] = mapped_column(
        _enum(DeliveryStatus, "delivery_status"), default=DeliveryStatus.UNASSIGNED
    )
    pickup_gps: Mapped[str] = mapped_column(String(40), default="")
    dropoff_gps: Mapped[str] = mapped_column(String(40), default="")
    route_url: Mapped[str] = mapped_column(Text, default="")
    picked_up_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    order: Mapped["Order"] = relationship(back_populates="delivery")


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id"), unique=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(20), default="demo")
    provider_order_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    provider_payment_id: Mapped[str] = mapped_column(String(64), default="")
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[PaymentStatus] = mapped_column(
        _enum(PaymentStatus, "payment_status"), default=PaymentStatus.CREATED
    )
    signature: Mapped[str] = mapped_column(String(128), default="")

    order: Mapped["Order"] = relationship(back_populates="payment")


class OrderChangeRequest(Base, TimestampMixin):
    __tablename__ = "order_change_requests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    change_type: Mapped[ChangeType] = mapped_column(_enum(ChangeType, "change_type"))
    status: Mapped[ChangeStatus] = mapped_column(
        _enum(ChangeStatus, "change_status"), default=ChangeStatus.PENDING
    )
    description: Mapped[str] = mapped_column(String(255), default="")
    # payload examples:
    #   FOOD:            {"add": [{"name": "Butter Roti", "qty": 2}]}
    #   DELIVERY_TIME:   {"time": "8:30 PM"}
    #   DELIVERY_ADDRESS:{"address": "...", "gps": "lat,lng"}
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    order: Mapped["Order"] = relationship(back_populates="change_requests")


# ── Conversation memory (RAG) ────────────────────────────────────────────────
class KnowledgeEmbedding(Base):
    __tablename__ = "knowledge_embeddings"
    __table_args__ = (UniqueConstraint("id", name="uq_kb_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(EmbeddingVector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, nullable=False
    )


class ConversationState(Base, TimestampMixin):
    """Per-order working memory for the Manager agent.

    Holds a rolling natural-language ``summary`` (compacted only when the raw
    transcript grows long) plus ``open_threads`` (unresolved items the manager
    is tracking, e.g. "customer asked to add dal fry — awaiting chef"). Injected
    into every prompt so context survives long conversations without blowing the
    token budget.
    """

    __tablename__ = "conversation_state"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id"), unique=True, index=True
    )
    summary: Mapped[str] = mapped_column(Text, default="")
    open_threads: Mapped[list] = mapped_column(JSON, default=list)
    turn_count: Mapped[int] = mapped_column(Integer, default=0)


class RelationshipMemory(Base):
    """Shared, embedding-searchable history for a (customer, chef, driver) trio.

    Every order links these three parties; their interactions (from any role,
    plus the assistant) are recorded here keyed by the participant ids, so the
    assistant can recall the *shared* context when talking to any stakeholder —
    e.g. surface a customer's "less spicy" note to the chef, or a repeat
    customer's usual delivery preference to the driver.
    """

    __tablename__ = "relationship_memory"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    chef_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("chefs.id"), nullable=True, index=True
    )
    driver_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("drivers.id"), nullable=True, index=True
    )
    order_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("orders.id"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # CUSTOMER/CHEF/DRIVER/ASSISTANT
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(EmbeddingVector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, nullable=False, index=True
    )
