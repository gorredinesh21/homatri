"""Customer domain models (customer_*)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.ids import id_factory
from backend.app.db.base import TS, Base, JSONB, TimestampMixin


class CustomerProfile(Base, TimestampMixin):
    __tablename__ = "customer_profiles"

    # Phone remains the live PK so existing WhatsApp/order FKs stay intact.
    customer_phone: Mapped[str] = mapped_column(String(20), primary_key=True)
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), unique=True, nullable=False, default=uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(100))
    delivery_address: Mapped[str] = mapped_column(Text, nullable=False)
    apartment_name: Mapped[str | None] = mapped_column(String(100), index=True)
    flat_number: Mapped[str | None] = mapped_column(String(50))
    landmark: Mapped[str | None] = mapped_column(String(100))
    address_line1: Mapped[str | None] = mapped_column(String(255))
    address_line2: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(100), default="Navi Mumbai")
    pincode: Mapped[str | None] = mapped_column(String(20))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(11, 8))
    alternate_phone: Mapped[str | None] = mapped_column(String(15))
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, default="avatar_tiffin_cartoon_1.png")
    is_cartoon_avatar: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    dietary_preference: Mapped[str | None] = mapped_column(String(20), default="VEG")
    dietary_preferences: Mapped[list] = mapped_column(JSONB, default=lambda: ["PURE_VEG"])
    delivery_instructions: Mapped[str | None] = mapped_column(Text)
    is_registered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(TS)


class CustomerOrder(Base, TimestampMixin):
    __tablename__ = "customer_orders"

    order_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("ord"))
    customer_phone: Mapped[str] = mapped_column(
        String(15), ForeignKey("customer_profiles.customer_phone"), nullable=False, index=True
    )
    chef_phone: Mapped[str] = mapped_column(
        String(15), ForeignKey("chef_profiles.chef_phone"), nullable=False, index=True
    )
    kitchen_name: Mapped[str] = mapped_column(String(100), nullable=False)  # immutable snapshot
    meal_window: Mapped[str] = mapped_column(String(20), nullable=False, default="LUNCH", index=True)
    service_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING_PAYMENT", index=True)
    cart_subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    delivery_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("30.00"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    special_instructions: Mapped[str | None] = mapped_column(Text)
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    cancelled_at: Mapped[datetime | None] = mapped_column(TS)


class CustomerOrderItem(Base):
    __tablename__ = "customer_order_items"

    item_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("ori"))
    order_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("customer_orders.order_id", ondelete="CASCADE"), nullable=False, index=True
    )
    menu_item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chef_menu_items.menu_item_id"), nullable=False, index=True
    )
    chef_phone: Mapped[str] = mapped_column(String(15), nullable=False, index=True)  # denorm for units_sold
    dish_name: Mapped[str] = mapped_column(String(100), nullable=False)  # snapshot
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)  # snapshot
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    item_subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    service_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)  # snapshot
    special_instructions: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())


class CustomerPayment(Base):
    __tablename__ = "customer_payments"

    payment_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("pay"))
    order_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("customer_orders.order_id"), nullable=False, index=True
    )
    customer_phone: Mapped[str] = mapped_column(
        String(15), ForeignKey("customer_profiles.customer_phone"), nullable=False, index=True
    )
    payment_type: Mapped[str] = mapped_column(String(20), nullable=False, default="INITIAL", index=True)
    amount_due: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    amount_paid: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    payment_link_url: Mapped[str | None] = mapped_column(Text)
    gateway: Mapped[str] = mapped_column(String(50), nullable=False, default="RAZORPAY")
    gateway_payment_id: Mapped[str | None] = mapped_column(String(100), index=True)
    gateway_order_id: Mapped[str | None] = mapped_column(String(100))
    transaction_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)
    refund_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(TS)


class CustomerReview(Base):
    __tablename__ = "customer_reviews"

    review_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=id_factory("rev"))
    order_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("customer_orders.order_id"), nullable=False, index=True
    )
    customer_phone: Mapped[str] = mapped_column(
        String(15), ForeignKey("customer_profiles.customer_phone"), nullable=False, index=True
    )
    chef_phone: Mapped[str] = mapped_column(
        String(15), ForeignKey("chef_profiles.chef_phone"), nullable=False, index=True
    )
    driver_phone: Mapped[str | None] = mapped_column(
        String(15), ForeignKey("driver_profiles.driver_phone"), index=True
    )
    chef_rating: Mapped[int] = mapped_column(Integer, nullable=False)
    driver_rating: Mapped[int | None] = mapped_column(Integer)
    review_text: Mapped[str | None] = mapped_column(Text)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())


class CustomerAddress(Base, TimestampMixin):
    __tablename__ = "customer_addresses"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    customer_phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    address_type: Mapped[str] = mapped_column(String(20), nullable=False, default="HOME")  # HOME, WORK, OTHER
    flat_no: Mapped[str] = mapped_column(String(100), nullable=False)
    street_address: Mapped[str] = mapped_column(String(255), nullable=False)
    landmark: Mapped[str | None] = mapped_column(String(255))
    full_address: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    cluster: Mapped[str] = mapped_column(String(50), default="Ghansoli")
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 8), default=Decimal("19.1234"))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(11, 8), default=Decimal("73.0123"))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)

