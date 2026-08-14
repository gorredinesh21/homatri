"""Customer domain write executors (Category 2 & Delegated Executors DW1, DW2).

Single-owner write executors for all customer_* tables.
Contains Delegated Executors DW1 (Order Status Transition) and DW2 (Payment Status Update).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.ids import generate_id
from backend.app.models.customer import (
    CustomerOrder,
    CustomerOrderItem,
    CustomerPayment,
    CustomerProfile,
    CustomerReview,
)
from backend.app.models.enums import OrderStatus, PaymentStatus


# =============================================================================
# EXECUTOR 1: Registration & Location Upsert
# =============================================================================
async def execute_customer_registration_and_location(
    session: AsyncSession,
    *,
    customer_phone: str,
    name: str,
    delivery_address: str,
    apartment_name: str | None = None,
    flat_number: str | None = None,
    landmark: str | None = None,
    city: str = "Hyderabad",
    pincode: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    dietary_preference: str = "VEG",
    is_registered: bool = True,
) -> CustomerProfile:
    """Executor #1 — Register a customer profile or update location coordinates.

    Idempotent: Inserts if phone doesn't exist; updates address/location if exists.
    """
    profile = await session.get(CustomerProfile, customer_phone)

    if profile is None:
        profile = CustomerProfile(
            customer_phone=customer_phone,
            name=name,
            delivery_address=delivery_address,
            apartment_name=apartment_name,
            flat_number=flat_number,
            landmark=landmark,
            city=city,
            pincode=pincode,
            latitude=Decimal(str(latitude)) if latitude is not None else None,
            longitude=Decimal(str(longitude)) if longitude is not None else None,
            dietary_preference=dietary_preference,
            is_registered=is_registered,
        )
        session.add(profile)
    else:
        profile.name = name
        profile.delivery_address = delivery_address
        if apartment_name is not None:
            profile.apartment_name = apartment_name
        if flat_number is not None:
            profile.flat_number = flat_number
        if landmark is not None:
            profile.landmark = landmark
        if latitude is not None:
            profile.latitude = Decimal(str(latitude))
        if longitude is not None:
            profile.longitude = Decimal(str(longitude))
        profile.dietary_preference = dietary_preference
        profile.is_registered = is_registered


    await session.flush()
    return profile


# =============================================================================
# EXECUTOR 2: Initialize Order Header
# =============================================================================
async def execute_customer_order_initialization(
    session: AsyncSession,
    *,
    customer_phone: str,
    chef_phone: str,
    kitchen_name: str,
    service_date: date,
    meal_window: str = "LUNCH",
    delivery_fee: Decimal = Decimal("30.00"),
    special_instructions: str | None = None,
) -> CustomerOrder:
    """Executor #2 — Create a new order header in PENDING_PAYMENT status.

    Generates a unique prefixed primary key (e.g. ord_a1b2c3d4e5f6).
    """
    customer = await session.get(CustomerProfile, customer_phone)
    assert customer is not None, f"Customer profile not found for phone: {customer_phone}"

    order_id = generate_id("ord")
    order = CustomerOrder(
        order_id=order_id,
        customer_phone=customer_phone,
        chef_phone=chef_phone,
        kitchen_name=kitchen_name,
        meal_window=meal_window,
        service_date=service_date,
        status="PENDING_PAYMENT",
        cart_subtotal=Decimal("0.00"),
        delivery_fee=delivery_fee,
        total_amount=delivery_fee,
        special_instructions=special_instructions,
    )
    session.add(order)
    await session.flush()
    return order


# =============================================================================
# EXECUTOR 3: Add / Update Item in Order
# =============================================================================
async def execute_add_item_to_order(
    session: AsyncSession,
    *,
    order_id: str,
    menu_item_id: str,
    dish_name: str,
    quantity: int,
    unit_price: Decimal,
    special_instructions: str | None = None,
) -> CustomerOrderItem:
    """Executor #3 — Add a line item to an order and recalculate order subtotals."""
    order = await session.get(CustomerOrder, order_id)
    assert order is not None, f"Order not found: {order_id}"
    assert order.status in {"DRAFT_CART", "PENDING_PAYMENT"}, f"Cannot modify items in order with status: {order.status}"

    existing_item = (
        await session.execute(
            select(CustomerOrderItem).where(
                CustomerOrderItem.order_id == order_id,
                CustomerOrderItem.menu_item_id == menu_item_id,
            )
        )
    ).scalar_one_or_none()

    if existing_item is None:
        item_id = generate_id("ori")
        item = CustomerOrderItem(
            item_id=item_id,
            order_id=order_id,
            menu_item_id=menu_item_id,
            chef_phone=order.chef_phone,
            dish_name=dish_name,
            quantity=quantity,
            unit_price=unit_price,
            item_subtotal=unit_price * quantity,
            service_date=order.service_date,
            special_instructions=special_instructions,
        )
        session.add(item)
    else:
        existing_item.quantity = quantity
        existing_item.unit_price = unit_price
        existing_item.item_subtotal = unit_price * quantity
        if special_instructions is not None:
            existing_item.special_instructions = special_instructions
        item = existing_item

    await session.flush()

    # Recalculate order subtotal and total
    subtotal_result = (
        await session.execute(
            select(func.sum(CustomerOrderItem.item_subtotal)).where(
                CustomerOrderItem.order_id == order_id
            )
        )
    ).scalar_one_or_none() or Decimal("0.00")

    order.cart_subtotal = subtotal_result
    order.total_amount = subtotal_result + order.delivery_fee
    await session.flush()

    return item


# =============================================================================
# EXECUTOR 3a: Append Extra Items to a Post-Payment Order (top-up)
# =============================================================================
async def execute_append_items_to_order(
    session: AsyncSession,
    *,
    order_id: str,
    items: list[dict],
) -> CustomerOrder:
    """Executor #3a — Append extra items to an already-paid order and recompute totals.

    `items` = [{menu_item_id, dish_name, quantity, unit_price}]. Quantity is ADDED to
    any existing line for the same dish (a top-up adds more of it). Unlike Executor #3,
    this permits CONFIRMED/BATCHED/COOKING orders — the post-payment top-up flow appends
    here only after the delta payment has cleared.
    """
    order = await session.get(CustomerOrder, order_id)
    assert order is not None, f"Order not found: {order_id}"
    assert order.status in {"CONFIRMED", "BATCHED", "COOKING"}, (
        f"Cannot top-up items in order with status: {order.status}"
    )

    for it in items:
        menu_item_id = it["menu_item_id"]
        qty = int(it["quantity"])
        unit_price = it["unit_price"]
        existing = (
            await session.execute(
                select(CustomerOrderItem).where(
                    CustomerOrderItem.order_id == order_id,
                    CustomerOrderItem.menu_item_id == menu_item_id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(CustomerOrderItem(
                item_id=generate_id("ori"),
                order_id=order_id,
                menu_item_id=menu_item_id,
                chef_phone=order.chef_phone,
                dish_name=it["dish_name"],
                quantity=qty,
                unit_price=unit_price,
                item_subtotal=unit_price * qty,
                service_date=order.service_date,
            ))
        else:
            existing.quantity += qty
            existing.item_subtotal = existing.unit_price * existing.quantity
    await session.flush()

    subtotal = (
        await session.execute(
            select(func.sum(CustomerOrderItem.item_subtotal)).where(
                CustomerOrderItem.order_id == order_id
            )
        )
    ).scalar_one_or_none() or Decimal("0.00")
    order.cart_subtotal = subtotal
    order.total_amount = subtotal + order.delivery_fee
    await session.flush()
    return order


# =============================================================================
# EXECUTOR 3b: Update Order Special Instructions (dietary note)
# =============================================================================
async def execute_order_special_instructions_update(
    session: AsyncSession,
    *,
    order_id: str,
    special_instructions: str,
) -> CustomerOrder:
    """Executor #3b — Set an order's special_instructions (e.g. an accepted dietary note)."""
    order = await session.get(CustomerOrder, order_id)
    assert order is not None, f"Order not found: {order_id}"
    order.special_instructions = special_instructions
    await session.flush()
    return order


# =============================================================================
# EXECUTOR 4: Create Payment Record
# =============================================================================
async def execute_payment_record_creation(
    session: AsyncSession,
    *,
    order_id: str,
    amount_due: Decimal,
    payment_method: str = "UPI",
    payment_type: str = "INITIAL",
    gateway_order_id: str | None = None,
    payment_link_url: str | None = None,
) -> CustomerPayment:
    """Executor #4 — Create a payment record in PENDING status for an order.

    `payment_type` is 'INITIAL' for the order's first payment or 'TOPUP' for a
    post-payment add-on (extra items on an already-paid order).
    """
    order = await session.get(CustomerOrder, order_id)
    assert order is not None, f"Order not found: {order_id}"

    payment_id = generate_id("pay")
    payment = CustomerPayment(
        payment_id=payment_id,
        order_id=order_id,
        customer_phone=order.customer_phone,
        payment_type=payment_type,
        amount_due=amount_due,
        amount_paid=Decimal("0.00"),
        gateway="RAZORPAY",
        status="PENDING",
        gateway_order_id=gateway_order_id,
        payment_link_url=payment_link_url,
    )
    session.add(payment)
    await session.flush()
    return payment


# =============================================================================
# EXECUTOR 5: Submit Order Review
# =============================================================================
async def execute_submit_order_review(
    session: AsyncSession,
    *,
    order_id: str,
    customer_phone: str,
    chef_phone: str,
    chef_rating: int,
    driver_phone: str | None = None,
    driver_rating: int | None = None,
    review_text: str | None = None,
) -> CustomerReview:
    """Executor #5 — Submit a review for a delivered order.

    Guard 2 Assert: rating must be between 1 and 5; order must be DELIVERED.
    """
    assert 1 <= chef_rating <= 5, f"Rating must be between 1 and 5, got {chef_rating}"
    order = await session.get(CustomerOrder, order_id)
    assert order is not None, f"Order not found: {order_id}"
    assert order.status == "DELIVERED", f"Can only review DELIVERED orders, current status is: {order.status}"

    review_id = generate_id("rev")
    review = CustomerReview(
        review_id=review_id,
        order_id=order_id,
        customer_phone=customer_phone,
        chef_phone=chef_phone,
        driver_phone=driver_phone,
        chef_rating=chef_rating,
        driver_rating=driver_rating,
        review_text=review_text,
    )
    session.add(review)
    await session.flush()
    return review


# =============================================================================
# DELEGATED EXECUTOR DW1: Order Status Transition
# =============================================================================
VALID_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT_CART": {"PENDING_PAYMENT", "CANCELLED"},
    "PENDING_PAYMENT": {"CONFIRMED", "CANCELLED"},
    "CONFIRMED": {"BATCHED", "CANCELLED"},
    "BATCHED": {"COOKING", "CANCELLED"},
    "COOKING": {"PACKED", "CANCELLED"},   # CANCELLED only via chef-approved cancel_order
    "PACKED": {"PICKED_UP"},
    "PICKED_UP": {"DELIVERED"},
    "DELIVERED": set(),  # Terminal state
    "CANCELLED": set(),  # Terminal state
}


async def execute_order_status_transition(
    session: AsyncSession,
    *,
    order_id: str,
    target_status: str,
    actor_role: str = "SYSTEM",
    reason: str | None = None,
) -> CustomerOrder:
    """Delegated Executor DW1 — Centralized single owner for order status transitions.

    Subagents and Master Agent delegate ALL status changes through this executor.
    Enforces state machine assertions (e.g. COOKING -> PACKED -> PICKED_UP -> DELIVERED).
    """
    order = await session.get(CustomerOrder, order_id)
    assert order is not None, f"Order not found: {order_id}"

    current = order.status
    if current == target_status:
        return order

    allowed = VALID_TRANSITIONS.get(current, set())


    assert target_status in allowed, (
        f"Invalid order status transition: '{current}' -> '{target_status}'. "
        f"Allowed target states from '{current}' are: {allowed} (attempted by {actor_role})"
    )

    order.status = target_status
    if target_status == "CANCELLED" and reason:
        order.cancellation_reason = reason

    await session.flush()
    return order


# =============================================================================
# DELEGATED EXECUTOR DW2: Payment Status Update
# =============================================================================
async def execute_payment_status_update(
    session: AsyncSession,
    *,
    payment_id: str,
    target_status: str,
    gateway_transaction_id: str | None = None,
    failure_reason: str | None = None,
    cascade_confirm: bool = True,
) -> CustomerPayment:
    """Delegated Executor DW2 — Update payment status (PAID / FAILED / REFUNDED).

    If target_status is 'PAID', automatically invokes DW1 to transition order to
    'CONFIRMED' — UNLESS cascade_confirm is False. A TOPUP payment (extra items on
    an already-CONFIRMED/COOKING order) sets cascade_confirm=False: the order is
    already past PENDING_PAYMENT, so a CONFIRMED transition would be invalid.
    """
    payment = await session.get(CustomerPayment, payment_id)
    assert payment is not None, f"Payment record not found: {payment_id}"

    assert target_status in {"PAID", "FAILED", "REFUNDED"}, f"Invalid payment target status: {target_status}"

    payment.status = target_status
    if target_status == "PAID":
        payment.amount_paid = payment.amount_due
    if gateway_transaction_id:
        payment.transaction_id = gateway_transaction_id
    if failure_reason:
        payment.refund_reason = failure_reason

    await session.flush()

    # Automatic Cascade: If payment becomes PAID, transition order to CONFIRMED via DW1
    if target_status == "PAID" and cascade_confirm:
        await execute_order_status_transition(
            session,
            order_id=payment.order_id,
            target_status="CONFIRMED",
            actor_role="PAYMENT_WEBHOOK",
            reason=f"Payment {payment_id} marked PAID via gateway transaction {gateway_transaction_id}",
        )

    return payment
