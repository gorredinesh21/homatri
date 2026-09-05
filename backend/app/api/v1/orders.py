"""Authenticated checkout, order history, tracking snapshot, SSE, reviews."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.deps import require_phone, require_user
from backend.app.core.config import settings
from backend.app.core.ids import generate_id
from backend.app.db.session import SessionFactory, get_db
from backend.app.models.chef import ChefMenuItem, ChefProfile
from backend.app.models.customer import (
    CustomerAddress,
    CustomerOrder,
    CustomerOrderItem,
    CustomerPayment,
    CustomerProfile,
    CustomerReview,
)
from backend.app.services.fulfillment import serialize_order
from backend.app.services.kitchens import committed_meals
from backend.app.services.live_hub import hub
from backend.app.services.meal_clock import active_window, is_past_cutoff
from backend.app.services.notify import notify_order
from backend.app.tools.master_tools import _run_cutoff_batch

router = APIRouter(prefix="/orders", tags=["Orders"])


class OrderItemSchema(BaseModel):
    menu_item_id: str
    chef_id: str | None = None
    quantity: int = Field(gt=0)
    customer_note: Optional[str] = None


class DeliveryAddressSchema(BaseModel):
    flat_no: Optional[str] = None
    street_address: Optional[str] = None
    landmark: Optional[str] = None
    full_address: Optional[str] = None
    phone: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    address_id: Optional[str] = None


class CheckoutRequest(BaseModel):
    meal_window: Optional[str] = None
    dietary_notes: Optional[str] = None
    delivery_address: Optional[DeliveryAddressSchema] = None
    items: list[OrderItemSchema]
    payment_method: Optional[str] = "RAZORPAY"  # RAZORPAY | COD


class ReviewIn(BaseModel):
    chef_rating: int = Field(ge=1, le=5)
    driver_rating: Optional[int] = Field(default=None, ge=1, le=5)
    review_text: Optional[str] = None


def _addr_text(req: DeliveryAddressSchema | None, profile: CustomerProfile) -> str:
    if req and req.full_address:
        return req.full_address
    if req and req.flat_no and req.street_address:
        extra = f", Near {req.landmark}" if req.landmark else ""
        return f"{req.flat_no}, {req.street_address}{extra}"
    return profile.delivery_address or profile.address_line1 or ""


@router.post("/checkout", status_code=status.HTTP_201_CREATED)
async def checkout_order(
    req: CheckoutRequest,
    payload: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if payload.get("role") == "CHEF":
        raise HTTPException(status_code=403, detail="Chef accounts cannot place customer orders.")
    if not req.items:
        raise HTTPException(status_code=400, detail="Cart is empty.")

    phone = payload["sub"]
    profile = await db.get(CustomerProfile, phone)
    if profile is None:
        raise HTTPException(status_code=401, detail="Customer profile not found. Sign in again.")

    menu_ids = [i.menu_item_id for i in req.items]
    menu_rows = (
        (await db.execute(select(ChefMenuItem).where(ChefMenuItem.menu_item_id.in_(menu_ids))))
        .scalars()
        .all()
    )
    by_id = {m.menu_item_id: m for m in menu_rows}
    if len(by_id) != len(set(menu_ids)):
        raise HTTPException(status_code=400, detail="One or more menu items are not on a live kitchen menu.")

    chef_phones = {by_id[i.menu_item_id].chef_phone for i in req.items}
    if len(chef_phones) != 1:
        raise HTTPException(status_code=400, detail="Cart can hold items from one kitchen only.")
    chef_phone = next(iter(chef_phones))
    chef = await db.get(ChefProfile, chef_phone)
    if chef is None or not chef.active_status:
        raise HTTPException(status_code=400, detail="This kitchen is not accepting orders.")
    if not chef.accepting_orders:
        raise HTTPException(status_code=409, detail="Kitchen is paused.")

    window_info = active_window()
    meal_window = (req.meal_window or window_info["meal_window"]).upper()
    if meal_window not in {"LUNCH", "DINNER"}:
        raise HTTPException(status_code=400, detail="meal_window must be LUNCH or DINNER")
    service_date = date.fromisoformat(window_info["service_date"])
    if meal_window != window_info["meal_window"] and not window_info["is_past_cutoff"]:
        # Customer asked for the other slot of today
        if meal_window == "LUNCH" and window_info["meal_window"] == "DINNER":
            raise HTTPException(status_code=409, detail="Lunch cutoff has passed.")
    if meal_window == window_info["meal_window"] and is_past_cutoff(meal_window, service_date):
        raise HTTPException(status_code=409, detail=f"{meal_window.title()} cutoff has passed.")

    qty_total = sum(i.quantity for i in req.items)
    used = await committed_meals(db, chef_phone, service_date, meal_window)
    if used + qty_total > chef.daily_capacity:
        raise HTTPException(
            status_code=409,
            detail=f"Kitchen capacity reached ({used}/{chef.daily_capacity} meals already committed).",
        )

    for line in req.items:
        dish = by_id[line.menu_item_id]
        if not dish.is_available:
            raise HTTPException(status_code=409, detail=f"{dish.dish_name} is sold out.")
        if dish.meal_type not in {meal_window, "BOTH"}:
            raise HTTPException(
                status_code=400,
                detail=f"{dish.dish_name} is not on the {meal_window.lower()} menu.",
            )

    addr = req.delivery_address
    if addr and addr.address_id:
        try:
            saved = await db.get(CustomerAddress, UUID(str(addr.address_id)))
        except ValueError:
            saved = None
        if saved and saved.customer_phone == phone:
            addr = DeliveryAddressSchema(
                full_address=saved.full_address,
                latitude=float(saved.latitude) if saved.latitude is not None else None,
                longitude=float(saved.longitude) if saved.longitude is not None else None,
                phone=saved.phone,
            )

    full_addr = _addr_text(addr, profile)
    if not full_addr or full_addr.startswith("Default") or full_addr == "Address pending":
        raise HTTPException(status_code=400, detail="Save a delivery address before checkout.")

    lat = addr.latitude if addr and addr.latitude is not None else (float(profile.latitude) if profile.latitude is not None else None)
    lng = addr.longitude if addr and addr.longitude is not None else (float(profile.longitude) if profile.longitude is not None else None)
    if lat is None or lng is None:
        raise HTTPException(status_code=400, detail="Delivery pin (lat/lng) is required so the rider can be routed.")

    payment_method = (req.payment_method or "RAZORPAY").upper()
    if payment_method not in {"RAZORPAY", "COD"}:
        raise HTTPException(status_code=400, detail="payment_method must be RAZORPAY or COD.")

    profile.delivery_address = full_addr
    profile.latitude = Decimal(str(lat))
    profile.longitude = Decimal(str(lng))

    subtotal = Decimal("0.00")
    fee = Decimal(str(settings.default_delivery_fee))
    order_id = generate_id("ord")
    order = CustomerOrder(
        order_id=order_id,
        customer_phone=phone,
        chef_phone=chef_phone,
        kitchen_name=chef.kitchen_name,
        meal_window=meal_window,
        service_date=service_date,
        status="PENDING_PAYMENT" if payment_method == "RAZORPAY" else "CONFIRMED",
        payment_method=payment_method,
        payment_status="PENDING" if payment_method == "RAZORPAY" else "COD_PENDING",
        cart_subtotal=Decimal("0.00"),
        delivery_fee=fee,
        total_amount=Decimal("0.00"),
        special_instructions=req.dietary_notes,
        delivery_address=full_addr,
        delivery_latitude=Decimal(str(lat)),
        delivery_longitude=Decimal(str(lng)),
    )
    db.add(order)
    await db.flush()

    for line in req.items:
        dish = by_id[line.menu_item_id]
        line_total = (dish.unit_price * line.quantity).quantize(Decimal("0.01"))
        subtotal += line_total
        db.add(
            CustomerOrderItem(
                order_id=order_id,
                menu_item_id=dish.menu_item_id,
                chef_phone=chef_phone,
                dish_name=dish.dish_name,
                unit_price=dish.unit_price,
                quantity=line.quantity,
                item_subtotal=line_total,
                service_date=service_date,
                special_instructions=line.customer_note,
            )
        )

    order.cart_subtotal = subtotal
    order.total_amount = subtotal + fee
    await db.flush()

    payment_info: dict[str, Any] | None = None
    if payment_method == "RAZORPAY":
        from backend.app.services.payment_service import razorpay_service

        # Token amount only while payment_force_token_amount is on; real totals now.
        charge_amount = (
            float(settings.payment_token_amount_rupees)
            if settings.payment_force_token_amount
            else float(order.total_amount)
        )
        rp = await razorpay_service.create_order(
            order_id=order_id,
            amount_in_rupees=charge_amount,
            customer_phone=phone,
            customer_name=profile.name,
        )
        if rp.get("mode") == "ERROR":
            await db.rollback()
            raise HTTPException(status_code=502, detail=f"Payment gateway error: {rp.get('error')}")
        db.add(
            CustomerPayment(
                order_id=order_id,
                customer_phone=phone,
                payment_type="INITIAL",
                amount_due=Decimal(str(charge_amount)),
                payment_link_url=None,
                gateway="RAZORPAY",
                gateway_order_id=rp.get("razorpay_order_id"),
                status="PENDING",
            )
        )
        await db.flush()
        payment_info = {
            "mode": rp.get("mode"),
            "razorpay_order_id": rp.get("razorpay_order_id"),
            "key_id": rp.get("key_id"),
            "amount_rupees": charge_amount,
            "token_mode": settings.payment_force_token_amount,
            "order_total_rupees": float(order.total_amount),
        }

    await notify_order(
        db,
        order,
        message=(
            f"Order {order.order_id} confirmed at {chef.kitchen_name} — "
            f"{meal_window.title()} ₹{float(order.total_amount):.0f}. "
            + ("Pay cash on delivery." if payment_method == "COD" else "Complete payment to confirm.")
        ),
    )
    await execute_outbound_for_chef(db, chef_phone, order)

    if payment_method == "COD" and is_past_cutoff(meal_window, service_date):
        await _run_cutoff_batch(db, window=meal_window, service_date=service_date)

    await db.commit()
    await db.refresh(order)
    body = await serialize_order(db, order)
    if payment_method == "COD":
        body["message"] = "Order confirmed. Pay ₹{:.0f} cash on delivery.".format(float(order.total_amount))
    else:
        body["message"] = "Order created. Complete the ₹{:.0f} payment to confirm.".format(
            float(payment_info["amount_rupees"])
        )
        body["payment"] = payment_info
    return body


class VerifyPaymentIn(BaseModel):
    razorpay_order_id: Optional[str] = None
    razorpay_payment_id: Optional[str] = None
    razorpay_signature: Optional[str] = None
    simulate: bool = False  # mock-mode gateway simulator confirmation


@router.get("/{order_id}/payment")
async def get_order_payment(
    order_id: str,
    payload: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Pending-payment details so the client can re-open the Razorpay checkout
    (e.g. from the tracking page after a dismissed modal)."""
    order = await db.get(CustomerOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.customer_phone != payload["sub"]:
        raise HTTPException(status_code=403, detail="Not your order")
    if order.payment_method != "RAZORPAY":
        raise HTTPException(status_code=409, detail="This order is not an online-payment order.")
    if order.status != "PENDING_PAYMENT":
        return {"status": "PAID", "order_status": order.status}

    payment = (
        await db.execute(
            select(CustomerPayment).where(
                CustomerPayment.order_id == order_id, CustomerPayment.payment_type == "INITIAL"
            )
        )
    ).scalar_one_or_none()

    from backend.app.services.payment_service import razorpay_service

    mock_flow = settings.razorpay_mock_mode or settings.payment_force_token_amount
    return {
        "status": "PENDING",
        "mode": "MOCK" if mock_flow else "REAL",
        "razorpay_order_id": payment.gateway_order_id if payment else None,
        "key_id": settings.razorpay_key_id,
        "amount_rupees": float(payment.amount_due) if payment else float(order.total_amount),
        "order_total_rupees": float(order.total_amount),
        "token_mode": settings.payment_force_token_amount,
    }


@router.post("/{order_id}/verify-payment")
async def verify_payment(
    order_id: str,
    body: VerifyPaymentIn,
    payload: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Confirm an online (RAZORPAY) order payment.

    In mock/token mode the frontend simulator calls this with simulate=true;
    in real mode the Razorpay handler posts the gateway ids + signature.
    Marks the payment PAID and cascades the order to CONFIRMED.
    """
    order = await db.get(CustomerOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.customer_phone != payload["sub"]:
        raise HTTPException(status_code=403, detail="Not your order")
    if order.payment_method != "RAZORPAY":
        raise HTTPException(status_code=409, detail="This order is not an online-payment order.")
    if order.status != "PENDING_PAYMENT":
        return {"status": "ok", "order_status": order.status, "message": "Payment already confirmed."}

    payment = (
        await db.execute(
            select(CustomerPayment).where(
                CustomerPayment.order_id == order_id, CustomerPayment.payment_type == "INITIAL"
            )
        )
    ).scalar_one_or_none()

    # Real mode ALWAYS verifies the gateway signature — the simulate flag is only
    # honoured in mock/token mode (a client must never be able to self-confirm a
    # live payment by passing simulate=true).
    mock_flow = settings.razorpay_mock_mode or settings.payment_force_token_amount
    if not mock_flow:
        from backend.app.services.payment_service import razorpay_service

        if not (body.razorpay_order_id and body.razorpay_payment_id and body.razorpay_signature):
            raise HTTPException(status_code=400, detail="razorpay_order_id, razorpay_payment_id and razorpay_signature required.")
        if payment is not None and payment.gateway_order_id and body.razorpay_order_id != payment.gateway_order_id:
            raise HTTPException(status_code=400, detail="razorpay_order_id does not match this order.")
        if not razorpay_service.verify_payment_signature(
            body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature
        ):
            raise HTTPException(status_code=400, detail="Payment signature verification failed.")

    if payment is not None:
        payment.status = "PAID"
        payment.amount_paid = payment.amount_due
        payment.gateway_order_id = body.razorpay_order_id or payment.gateway_order_id
        payment.gateway_payment_id = body.razorpay_payment_id or payment.gateway_payment_id or f"pay_mock_{order_id}"
        payment.transaction_id = payment.gateway_payment_id
        payment.paid_at = datetime.now()

    order.status = "CONFIRMED"
    order.payment_status = "PAID"

    await notify_order(
        db,
        order,
        message=f"Payment received for {order.order_id}. Order confirmed at {order.kitchen_name}.",
    )

    window_info = active_window()
    if is_past_cutoff(order.meal_window, order.service_date):
        await _run_cutoff_batch(db, window=order.meal_window, service_date=order.service_date)

    await db.commit()
    await db.refresh(order)
    result = await serialize_order(db, order)
    result["message"] = "Payment verified. Order confirmed."
    return result


class DietaryRequestIn(BaseModel):
    note: str = Field(min_length=2, max_length=500)


@router.post("/{order_id}/dietary-request", status_code=status.HTTP_201_CREATED)
async def create_dietary_request(
    order_id: str,
    body: DietaryRequestIn,
    payload: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    from backend.app.models.chef import ChefDietaryRequest

    order = await db.get(CustomerOrder, order_id)
    if order is None or order.customer_phone != payload["sub"]:
        raise HTTPException(status_code=404, detail="Order not found.")
    if order.status not in {"CONFIRMED", "BATCHED", "COOKING"}:
        raise HTTPException(status_code=409, detail="Dietary requests are only possible while the order is being cooked.")
    open_req = (
        await db.execute(
            select(ChefDietaryRequest).where(
                ChefDietaryRequest.order_id == order_id,
                ChefDietaryRequest.status.in_(["WAITING_CHEF", "COUNTERED"]),
            )
        )
    ).scalar_one_or_none()
    if open_req:
        raise HTTPException(status_code=409, detail="A dietary request is already open on this order.")
    req = ChefDietaryRequest(
        order_id=order_id,
        customer_phone=order.customer_phone,
        chef_phone=order.chef_phone,
        note=body.note,
        status="WAITING_CHEF",
        turn=1,
    )
    db.add(req)
    await db.commit()
    return {
        "requestId": req.request_id,
        "orderId": order_id,
        "status": req.status,
        "message": "Request sent to the kitchen.",
    }


async def execute_outbound_for_chef(db, chef_phone: str, order: CustomerOrder) -> None:
    from backend.app.executors.master import execute_outbound_whatsapp_enqueue

    await execute_outbound_whatsapp_enqueue(
        db,
        recipient_phone=chef_phone,
        recipient_role="CHEF",
        message_text=f"New {order.meal_window.lower()} order {order.order_id} — ₹{float(order.total_amount):.0f}.",
        related_order_id=order.order_id,
    )


@router.get("")
@router.get("/mine")
async def list_my_orders(
    phone: str = Depends(require_phone),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        (
            await db.execute(
                select(CustomerOrder)
                .where(CustomerOrder.customer_phone == phone)
                .order_by(CustomerOrder.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return [await serialize_order(db, row) for row in rows]


@router.get("/{order_id}/stream")
async def stream_order(order_id: str, request: Request, payload: dict = Depends(require_user)):
    async with SessionFactory() as db:
        order = await db.get(CustomerOrder, order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        role = payload.get("role")
        if role == "CUSTOMER" and order.customer_phone != payload["sub"]:
            raise HTTPException(status_code=403, detail="Not your order")
        snapshot = await serialize_order(db, order)

    queue = await hub.subscribe_order(order_id)

    async def gen():
        try:
            yield f"event: snapshot\ndata: {json.dumps(snapshot)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: status\ndata: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            await hub.unsubscribe_order(order_id, queue)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/{order_id}")
async def get_order(
    order_id: str,
    payload: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    order = await db.get(CustomerOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    role = payload.get("role")
    phone = payload["sub"]
    if role == "CUSTOMER" and order.customer_phone != phone:
        raise HTTPException(status_code=403, detail="Not your order")
    if role == "CHEF" and order.chef_phone != phone:
        raise HTTPException(status_code=403, detail="Not your kitchen order")
    return await serialize_order(db, order)


@router.post("/{order_id}/review")
async def review_order(
    order_id: str,
    body: ReviewIn,
    phone: str = Depends(require_phone),
    db: AsyncSession = Depends(get_db),
):
    order = await db.get(CustomerOrder, order_id)
    if order is None or order.customer_phone != phone:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != "DELIVERED":
        raise HTTPException(status_code=409, detail="You can review after the order is delivered.")
    existing = (
        await db.execute(select(CustomerReview).where(CustomerReview.order_id == order_id))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="This order already has a review.")
    db.add(
        CustomerReview(
            order_id=order_id,
            customer_phone=phone,
            chef_phone=order.chef_phone,
            chef_rating=body.chef_rating,
            driver_rating=body.driver_rating,
            review_text=body.review_text,
        )
    )
    chef = await db.get(ChefProfile, order.chef_phone)
    if chef:
        rows = (
            (
                await db.execute(
                    select(CustomerReview.chef_rating).where(CustomerReview.chef_phone == order.chef_phone)
                )
            )
            .scalars()
            .all()
        )
        ratings = list(rows) + [body.chef_rating]
        chef.rating_average = Decimal(str(round(sum(ratings) / len(ratings), 2)))
    await db.commit()
    return {"status": "ok", "order_id": order_id}
