"""Domain enums, stored as strings for cross-dialect portability."""
from __future__ import annotations

import enum


class UserRole(str, enum.Enum):
    CUSTOMER = "CUSTOMER"
    CHEF = "CHEF"
    DRIVER = "DRIVER"


class OrderStatus(str, enum.Enum):
    """Order lifecycle. Transitions are enforced in the lifecycle service."""

    PENDING = "PENDING"                # parsed, not yet paid
    AWAITING_PAYMENT = "AWAITING_PAYMENT"
    CONFIRMED = "CONFIRMED"            # payment captured
    PREPARING = "PREPARING"            # chef cooking
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


class PaymentStatus(str, enum.Enum):
    CREATED = "CREATED"        # payment intent/order created at gateway
    PENDING = "PENDING"
    PAID = "PAID"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class DeliveryStatus(str, enum.Enum):
    UNASSIGNED = "UNASSIGNED"
    ASSIGNED = "ASSIGNED"
    PICKED_UP = "PICKED_UP"
    DELIVERED = "DELIVERED"


class ChangeType(str, enum.Enum):
    FOOD = "FOOD"                      # add/remove items
    DELIVERY_TIME = "DELIVERY_TIME"
    DELIVERY_ADDRESS = "DELIVERY_ADDRESS"


class ChangeStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
