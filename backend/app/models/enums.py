"""Enum value sets (stored as VARCHAR per the finalized schema; validated in code).

Kept as Python str-enums for validation in tools/executors (Guard 2) without
coupling the DB to native Postgres ENUM types.
"""

from __future__ import annotations

from enum import Enum


class MealWindow(str, Enum):
    LUNCH = "LUNCH"
    DINNER = "DINNER"


class MealType(str, Enum):  # chef_menu_items availability (a dish may serve BOTH)
    LUNCH = "LUNCH"
    DINNER = "DINNER"
    BOTH = "BOTH"


class OrderStatus(str, Enum):
    DRAFT_CART = "DRAFT_CART"  # added: referenced by add_item pre-conditions
    PENDING_PAYMENT = "PENDING_PAYMENT"
    CONFIRMED = "CONFIRMED"
    BATCHED = "BATCHED"
    COOKING = "COOKING"
    PACKED = "PACKED"
    PICKED_UP = "PICKED_UP"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


class PaymentStatus(str, Enum):
    PENDING = "PENDING"
    PAID = "PAID"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class PaymentType(str, Enum):
    INITIAL = "INITIAL"
    TOPUP = "TOPUP"
    REFUND = "REFUND"


class ReadinessStatus(str, Enum):
    PREPARING = "PREPARING"
    PACKED_READY = "PACKED_READY"


class TripStatus(str, Enum):
    ASSIGNED = "ASSIGNED"
    EN_ROUTE_PICKUP = "EN_ROUTE_PICKUP"
    AT_KITCHEN = "AT_KITCHEN"
    EN_ROUTE_DELIVERY = "EN_ROUTE_DELIVERY"
    AT_GATE = "AT_GATE"
    COMPLETED = "COMPLETED"


# The set of trip phases that count as "trip in progress" (fixes the old
# report_vehicle_delay assertion that checked a non-existent 'IN_PROGRESS').
TRIP_ACTIVE_PHASES = frozenset(
    {
        TripStatus.EN_ROUTE_PICKUP,
        TripStatus.AT_KITCHEN,
        TripStatus.EN_ROUTE_DELIVERY,
        TripStatus.AT_GATE,
    }
)


class WindowStatus(str, Enum):
    OPEN = "OPEN"
    LOCKED_PROCESSING = "LOCKED_PROCESSING"
    COMPLETED = "COMPLETED"


class RouteStatus(str, Enum):
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class StopStatus(str, Enum):
    PENDING = "PENDING"
    ARRIVED = "ARRIVED"
    COMPLETED = "COMPLETED"


class StopType(str, Enum):
    PICKUP_KITCHEN = "PICKUP_KITCHEN"
    DROPOFF_GATE = "DROPOFF_GATE"


class LogSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class OutboundStatus(str, Enum):
    QUEUED = "QUEUED"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    READ = "READ"
    FAILED = "FAILED"


class HitlStatus(str, Enum):
    WAITING = "WAITING"
    RESUMED = "RESUMED"
    EXPIRED = "EXPIRED"
    RESOLVED = "RESOLVED"


class HitlInterruptType(str, Enum):
    DIETARY_APPROVAL = "DIETARY_APPROVAL"
    CANCELLATION_APPROVAL = "CANCELLATION_APPROVAL"
    UNLOCATABLE_ADDRESS = "UNLOCATABLE_ADDRESS"
    AWAIT_LOCATION_PIN = "AWAIT_LOCATION_PIN"
    PAYMENT_AWAIT_MASTER_APPROVAL = "PAYMENT_AWAIT_MASTER_APPROVAL"
    PAYMENT_AWAIT_PROVIDER = "PAYMENT_AWAIT_PROVIDER"


class AdminRole(str, Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    OPS = "OPS"
    SUPPORT = "SUPPORT"


class Direction(str, Enum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class ActorRole(str, Enum):
    CUSTOMER = "CUSTOMER"
    CHEF = "CHEF"
    DRIVER = "DRIVER"


class MessageSource(str, Enum):
    USER = "USER"
    CUSTOMER_AGENT = "CUSTOMER_AGENT"
    CHEF_AGENT = "CHEF_AGENT"
    DRIVER_AGENT = "DRIVER_AGENT"
    MASTER_AGENT = "MASTER_AGENT"
    SYSTEM = "SYSTEM"


class MessageType(str, Enum):
    TEXT = "TEXT"
    LOCATION = "LOCATION"
    INTERACTIVE = "INTERACTIVE"
    IMAGE = "IMAGE"
    TEMPLATE = "TEMPLATE"
