"""The manager's policy: what may happen to an order at each lifecycle stage.

This is the single source of truth for the "maître d' judgement". It is exposed
two ways:
  1. ``policy_text(order)`` — a short, stage-specific rules block injected into
     the agent prompt so the model knows what it's allowed to do.
  2. ``can_*`` predicates — hard pre-conditions the tools call, so the agent
     physically cannot perform a disallowed action even if it tries.
"""
from __future__ import annotations

from app.models.enums import OrderStatus

# Food can be added/changed at these stages (with chef approval past CONFIRMED).
_FOOD_OK = {
    OrderStatus.PENDING,
    OrderStatus.AWAITING_PAYMENT,
    OrderStatus.CONFIRMED,
    OrderStatus.PREPARING,
    OrderStatus.READY_FOR_PICKUP,
}
# Delivery time/address can change until the order is completed/cancelled.
_DELIVERY_OK = {
    OrderStatus.PENDING,
    OrderStatus.AWAITING_PAYMENT,
    OrderStatus.CONFIRMED,
    OrderStatus.PREPARING,
    OrderStatus.READY_FOR_PICKUP,
    OrderStatus.OUT_FOR_DELIVERY,
}
_CANCEL_OK = {
    OrderStatus.PENDING,
    OrderStatus.AWAITING_PAYMENT,
    OrderStatus.CONFIRMED,
    OrderStatus.PREPARING,
}


def can_add_food(order) -> tuple[bool, str]:
    if order is None:
        return False, "There's no active order to add to."
    if order.status in _FOOD_OK:
        return True, ""
    if order.status == OrderStatus.OUT_FOR_DELIVERY:
        return False, "The order is already out for delivery, so we can't add items — I can start a fresh order though."
    return False, "That order is already complete — I can help you place a new one."


def can_change_delivery(order) -> tuple[bool, str]:
    if order is None:
        return False, "There's no active order to change."
    if order.status in _DELIVERY_OK:
        return True, ""
    return False, "That order is already complete, so delivery details can't be changed."


def can_cancel(order) -> tuple[bool, str]:
    if order is None:
        return False, "There's no active order to cancel."
    if order.status in _CANCEL_OK:
        return True, ""
    return False, "It's too late to cancel — the order is already on its way or delivered."


def needs_chef_approval(order) -> bool:
    """Food changes after cooking has been committed must be chef-approved."""
    return order is not None and order.status in (
        OrderStatus.PREPARING, OrderStatus.READY_FOR_PICKUP
    )


def policy_text(order) -> str:
    """Stage-specific rules block for the prompt."""
    if order is None:
        return (
            "STAGE: no active order. You may take a new order or answer questions. "
            "Only create an order when the customer names specific menu dishes."
        )
    s = order.status
    lines = [f"STAGE: order {order.code} is {s.value}. At this stage:"]
    # food
    if s in (OrderStatus.PENDING, OrderStatus.AWAITING_PAYMENT):
        lines.append("• Items: can be added/removed freely (order not paid); the total & pay link update.")
    elif s == OrderStatus.CONFIRMED:
        lines.append("• Items: can be added; notify the chef. A top-up covers the extra amount.")
    elif s == OrderStatus.PREPARING:
        lines.append("• Items: only if the chef agrees it's still feasible — send the chef the request; charge a top-up.")
    elif s == OrderStatus.READY_FOR_PICKUP:
        lines.append("• Items: only if the rider hasn't left — ask the chef first.")
    else:
        lines.append("• Items: CANNOT be changed now — offer a new order instead.")
    # delivery
    if s in _DELIVERY_OK:
        if s == OrderStatus.OUT_FOR_DELIVERY:
            lines.append("• Delivery time/address: possible but the rider must confirm (they're en route).")
        else:
            lines.append("• Delivery time/address: can be changed" + (" (rider confirms if assigned)." if order.delivery and order.delivery.driver_id else "."))
    else:
        lines.append("• Delivery details: locked (order complete).")
    # cancel
    ok, _ = can_cancel(order)
    lines.append("• Cancellation: " + ("allowed." if ok else "no longer possible."))
    return "\n".join(lines)
