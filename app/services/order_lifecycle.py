"""Order lifecycle: creation, validated status transitions, modifications, and
the live-context snapshot injected into every role's chatbot prompt.

The transition table is the single source of truth for legal state changes;
illegal transitions raise ``InvalidTransition`` rather than silently corrupting
an order.
"""
from __future__ import annotations

import random
import string
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.models.entities import (
    Chef,
    Delivery,
    Driver,
    Order,
    OrderChangeRequest,
    OrderItem,
    User,
)
from app.models.enums import (
    ChangeStatus,
    ChangeType,
    DeliveryStatus,
    OrderStatus,
    UserRole,
)
from app.services.order_parsing import OrderDraft, ResolvedItem

log = get_logger("lifecycle")

DELIVERY_FEE = 30.0

_ALLOWED: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.AWAITING_PAYMENT, OrderStatus.CANCELLED},
    OrderStatus.AWAITING_PAYMENT: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
    OrderStatus.CONFIRMED: {OrderStatus.PREPARING, OrderStatus.CANCELLED},
    OrderStatus.PREPARING: {OrderStatus.READY_FOR_PICKUP, OrderStatus.CANCELLED},
    OrderStatus.READY_FOR_PICKUP: {OrderStatus.OUT_FOR_DELIVERY},
    OrderStatus.OUT_FOR_DELIVERY: {OrderStatus.DELIVERED},
    OrderStatus.DELIVERED: set(),
    OrderStatus.CANCELLED: set(),
}

# Statuses at which food can still be added.
MUTABLE_FOOD_STATUSES = {OrderStatus.CONFIRMED, OrderStatus.PREPARING}


class InvalidTransition(RuntimeError):
    pass


def generate_order_code() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"HM-{suffix}"


# ── Queries ──────────────────────────────────────────────────────────────────
async def get_order_by_code(session: AsyncSession, code: str) -> Order | None:
    code_clean = (code or "").strip().upper()
    from sqlalchemy import func
    stmt = (
        select(Order)
        .where(func.upper(Order.code) == code_clean)
        .options(
            selectinload(Order.items),
            selectinload(Order.payment),
            selectinload(Order.delivery),
            selectinload(Order.change_requests),
        )
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_active_order_for_customer(
    session: AsyncSession, phone: str
) -> Order | None:
    stmt = (
        select(Order)
        .join(User, Order.customer_id == User.id)
        .where(User.phone == phone)
        .where(Order.status.notin_([OrderStatus.DELIVERED, OrderStatus.CANCELLED]))
        .order_by(Order.created_at.desc())
        .options(
            selectinload(Order.items),
            selectinload(Order.payment),
            selectinload(Order.delivery),
            selectinload(Order.change_requests),
        )
    )
    return (await session.execute(stmt)).scalars().first()


# ── Mutations ─────────────────────────────────────────────────────────────────
async def create_order(
    session: AsyncSession, customer: User, chef: Chef, draft: OrderDraft
) -> Order:
    order = Order(
        code=generate_order_code(),
        customer_id=customer.id,
        chef_id=chef.id,
        status=OrderStatus.PENDING,
        customer_name=draft.customer_name or customer.name,
        delivery_address=draft.delivery_address or "",
        requested_delivery_time=draft.delivery_time or "ASAP",
        delivery_fee=DELIVERY_FEE,
    )
    for ri in draft.items:
        order.items.append(_order_item_from(ri))
    order.recompute_totals(delivery_fee=DELIVERY_FEE)
    session.add(order)
    await session.flush()
    log.info("order %s created (%d items, ₹%.2f)", order.code, len(order.items), order.total)
    return order


def _order_item_from(ri: ResolvedItem) -> OrderItem:
    mi = ri.menu_item
    name = mi["name"] if isinstance(mi, dict) else mi.name
    price = mi["price"] if isinstance(mi, dict) else mi.price
    mid = None if isinstance(mi, dict) else mi.id
    return OrderItem(menu_item_id=mid, name=name, unit_price=price, quantity=ri.quantity)


def set_status(order: Order, new_status: OrderStatus) -> None:
    if new_status == order.status:
        return
    if new_status not in _ALLOWED.get(order.status, set()):
        raise InvalidTransition(
            f"{order.status.value} -> {new_status.value} is not allowed"
        )
    log.info("order %s: %s -> %s", order.code, order.status.value, new_status.value)
    order.status = new_status


def add_food_items(order: Order, resolved: Iterable[ResolvedItem]) -> None:
    """Merge added items into the order (by name) and recompute totals."""
    by_name = {i.name.lower(): i for i in order.items}
    for ri in resolved:
        new = _order_item_from(ri)
        existing = by_name.get(new.name.lower())
        if existing:
            existing.quantity += new.quantity
        else:
            order.items.append(new)
            by_name[new.name.lower()] = new
    _drop_nonpositive(order)
    order.recompute_totals()


def remove_food_items(order: Order, resolved: Iterable[ResolvedItem]) -> list[str]:
    """Reduce quantities for the given items; drop any that reach zero.

    Returns human labels of what was removed. Quantity in each ResolvedItem is
    the amount to remove.
    """
    by_name = {i.name.lower(): i for i in order.items}
    removed: list[str] = []
    for ri in resolved:
        name = (ri.menu_item["name"] if isinstance(ri.menu_item, dict) else ri.menu_item.name)
        existing = by_name.get(name.lower())
        if existing:
            take = min(existing.quantity, ri.quantity)
            existing.quantity -= ri.quantity
            removed.append(f"{take}x {name}")
    _drop_nonpositive(order)
    order.recompute_totals()
    return removed


def _drop_nonpositive(order: Order) -> None:
    """Remove any order items whose quantity fell to zero or below."""
    for i in list(order.items):
        if i.quantity <= 0:
            order.items.remove(i)


def create_change_request(
    order: Order,
    change_type: ChangeType,
    payload: dict,
    description: str,
) -> OrderChangeRequest:
    cr = OrderChangeRequest(
        order_id=order.id,
        change_type=change_type,
        status=ChangeStatus.PENDING,
        payload=payload,
        description=description,
    )
    order.change_requests.append(cr)
    return cr


def apply_change_request(order: Order, cr: OrderChangeRequest) -> None:
    """Apply an accepted change request to the order."""
    if cr.change_type == ChangeType.DELIVERY_TIME:
        order.requested_delivery_time = cr.payload.get("time", order.requested_delivery_time)
    elif cr.change_type == ChangeType.DELIVERY_ADDRESS:
        order.delivery_address = cr.payload.get("address", order.delivery_address)
        if cr.payload.get("gps"):
            order.delivery_gps = cr.payload["gps"]
            if order.delivery:
                order.delivery.dropoff_gps = cr.payload["gps"]
    # FOOD changes are applied by the caller via add_food_items (needs menu context).
    cr.status = ChangeStatus.ACCEPTED


# ── Live context for role prompts ─────────────────────────────────────────────
def build_active_order_context(order: Order | None) -> str:
    if order is None:
        return "There is no active order right now."
    lines = [
        f"ACTIVE ORDER {order.code} (status: {order.status.value})",
        f"Customer: {order.customer_name}",
        "Items:",
    ]
    for it in order.items:
        lines.append(f"  - {it.quantity} x {it.name} (₹{it.unit_price:g})")
    lines.append(f"Subtotal ₹{order.subtotal:g} + delivery ₹{order.delivery_fee:g} = ₹{order.total:g}")
    lines.append(f"Delivery time: {order.requested_delivery_time or 'ASAP'}")
    if order.delivery_address:
        lines.append(f"Delivery address: {order.delivery_address}")
    if order.delivery and order.delivery.driver_id:
        lines.append(f"Rider assigned (delivery status: {order.delivery.status.value})")
    pending = [c for c in order.change_requests if c.status == ChangeStatus.PENDING]
    if pending:
        lines.append("Pending changes: " + "; ".join(c.description for c in pending))
    return "\n".join(lines)
