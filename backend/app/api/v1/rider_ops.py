"""Rider shift, trip, pickup, OTP delivery, SOS."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.deps import require_role
from backend.app.db.session import get_db
from backend.app.executors.driver import execute_driver_trip_phase_update
from backend.app.models.customer import CustomerOrder
from backend.app.models.driver import DriverProfile, DriverTripStatus
from backend.app.models.system import SystemDeliveryStop, SystemDeliveryStopOrder
from backend.app.services.fulfillment import current_trip_payload
from backend.app.services.live_hub import hub
from backend.app.services.notify import notify_ops, notify_order
from backend.app.tools.master_tools import _escalate_to_admin

router = APIRouter(prefix="/rider", tags=["Rider"])


class ShiftIn(BaseModel):
    on: bool


class DeliverIn(BaseModel):
    order_id: str
    otp: str


class GateIn(BaseModel):
    deliveries: list[DeliverIn]


class UndeliveredIn(BaseModel):
    order_id: str
    reason: str


class ReportIn(BaseModel):
    kind: str
    order_id: Optional[str] = None


def _maps(lat, lng, address: str) -> str:
    if lat is not None and lng is not None:
        return f"https://www.google.com/maps/dir/?api=1&destination={lat},{lng}&travelmode=driving"
    return f"https://www.google.com/maps/dir/?api=1&destination={address}"


@router.post("/me/shift")
async def set_shift(
    body: ShiftIn,
    payload: dict = Depends(require_role("RIDER")),
    db: AsyncSession = Depends(get_db),
):
    driver = await db.get(DriverProfile, payload["sub"])
    if driver is None:
        raise HTTPException(status_code=404, detail="Finish rider onboarding first.")
    if not body.on:
        trip = (
            await db.execute(
                select(DriverTripStatus).where(
                    DriverTripStatus.driver_phone == driver.driver_phone,
                    DriverTripStatus.status != "COMPLETED",
                )
            )
        ).scalars().first()
        if trip and trip.status not in {"ASSIGNED", "COMPLETED"}:
            raise HTTPException(status_code=409, detail="Finish the current trip before going off shift.")
        driver.is_on_shift = False
        driver.on_shift = False
    else:
        driver.is_on_shift = True
        driver.on_shift = True
        driver.active_status = True
    await db.commit()
    return await current_trip_payload(db, driver.driver_phone)


@router.get("/me/trip")
async def get_trip(
    payload: dict = Depends(require_role("RIDER")),
    db: AsyncSession = Depends(get_db),
):
    driver = await db.get(DriverProfile, payload["sub"])
    if driver is None:
        raise HTTPException(status_code=404, detail="Finish rider onboarding first.")
    return await current_trip_payload(db, driver.driver_phone)


@router.post("/me/pickup")
async def confirm_pickup(
    payload: dict = Depends(require_role("RIDER")),
    db: AsyncSession = Depends(get_db),
):
    phone = payload["sub"]
    trip = (
        await db.execute(
            select(DriverTripStatus).where(
                DriverTripStatus.driver_phone == phone,
                DriverTripStatus.status != "COMPLETED",
            )
        )
    ).scalars().first()
    if trip is None:
        raise HTTPException(status_code=404, detail="No assigned trip.")
    stops = (
        (
            await db.execute(
                select(SystemDeliveryStop)
                .where(SystemDeliveryStop.route_id == trip.route_id)
                .order_by(SystemDeliveryStop.stop_index)
            )
        )
        .scalars()
        .all()
    )
    pickup = next((s for s in stops if s.stop_type in {"PICKUP", "PICKUP_KITCHEN"}), None)
    if pickup is None:
        raise HTTPException(status_code=409, detail="Route has no kitchen pickup stop.")
    pickup.status = "COMPLETED"
    pickup.actual_arrival = datetime.now()
    links = (
        (
            await db.execute(
                select(SystemDeliveryStopOrder).where(
                    SystemDeliveryStopOrder.stop_id.in_([s.stop_id for s in stops])
                )
            )
        )
        .scalars()
        .all()
    )
    for link in links:
        order = await db.get(CustomerOrder, link.order_id)
        if order and order.status == "PACKED":
            order.status = "PICKED_UP"
            await notify_order(
                db,
                order,
                message=f"Your {order.meal_window.lower()} tiffin is out for delivery. PIN {order.delivery_otp}.",
            )
    await execute_driver_trip_phase_update(
        db, driver_phone=phone, route_id=trip.route_id, target_status="EN_ROUTE_DELIVERY", current_stop_index=2
    )
    await db.commit()
    return await current_trip_payload(db, phone)


async def _deliver_one(db, driver_phone: str, order_id: str, otp: str) -> CustomerOrder:
    order = await db.get(CustomerOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if not order.delivery_otp or order.delivery_otp != otp.strip():
        raise HTTPException(status_code=403, detail="Delivery PIN does not match.")
    order.status = "DELIVERED"
    order.delivery_otp_verified_at = datetime.now()
    if order.payment_method == "COD":
        order.payment_status = "COD_COLLECTED"
    so = (
        await db.execute(select(SystemDeliveryStopOrder).where(SystemDeliveryStopOrder.order_id == order_id))
    ).scalars().first()
    if so:
        remaining = (
            await db.execute(
                select(SystemDeliveryStopOrder).where(SystemDeliveryStopOrder.stop_id == so.stop_id)
            )
        ).scalars().all()
        still = False
        for link in remaining:
            other = await db.get(CustomerOrder, link.order_id)
            if other and other.status not in {"DELIVERED", "CANCELLED"}:
                still = True
        stop = await db.get(SystemDeliveryStop, so.stop_id)
        if stop and not still:
            stop.status = "COMPLETED"
            stop.actual_arrival = datetime.now()
    await notify_order(db, order, message=f"Delivered. Enjoy your tiffin from {order.kitchen_name}.")
    return order


@router.post("/me/deliver")
async def deliver(
    body: DeliverIn,
    payload: dict = Depends(require_role("RIDER")),
    db: AsyncSession = Depends(get_db),
):
    await _deliver_one(db, payload["sub"], body.order_id, body.otp)
    await _maybe_complete_trip(db, payload["sub"])
    await db.commit()
    return await current_trip_payload(db, payload["sub"])


@router.post("/me/confirm-gate")
async def confirm_gate(
    body: GateIn,
    payload: dict = Depends(require_role("RIDER")),
    db: AsyncSession = Depends(get_db),
):
    for row in body.deliveries:
        await _deliver_one(db, payload["sub"], row.order_id, row.otp)
    await _maybe_complete_trip(db, payload["sub"])
    await db.commit()
    return await current_trip_payload(db, payload["sub"])


@router.post("/me/undelivered")
async def undelivered(
    body: UndeliveredIn,
    payload: dict = Depends(require_role("RIDER")),
    db: AsyncSession = Depends(get_db),
):
    order = await db.get(CustomerOrder, body.order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    order.status = "UNDELIVERED"
    order.cancellation_reason = body.reason
    order.cancelled_at = datetime.now()
    await notify_order(db, order, message=f"We could not complete delivery ({body.reason}). Support will follow up.")
    await notify_ops(db, message=f"UNDELIVERED {order.order_id}: {body.reason}", order_id=order.order_id)
    await db.commit()
    return await current_trip_payload(db, payload["sub"])


class CodCollectedIn(BaseModel):
    order_id: str


@router.post("/me/cod-collected")
async def cod_collected(
    body: CodCollectedIn,
    payload: dict = Depends(require_role("RIDER")),
    db: AsyncSession = Depends(get_db),
):
    """Rider confirms cash collected for a COD order."""
    order = await db.get(CustomerOrder, body.order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.payment_method != "COD":
        raise HTTPException(status_code=409, detail="Not a cash-on-delivery order.")
    order.payment_status = "COD_COLLECTED"
    await db.commit()
    return {"order_id": order.order_id, "payment_status": "COD_COLLECTED"}


@router.post("/me/report")
async def report(
    body: ReportIn,
    payload: dict = Depends(require_role("RIDER")),
    db: AsyncSession = Depends(get_db),
):
    if body.kind == "kitchen_delay":
        msg = "Master agent notified: kitchen delay. Customers will get a WhatsApp update."
    else:
        msg = "Master agent notified: address issue. A location pin will be requested from the customer."
    await notify_ops(db, message=f"Rider {payload['sub']}: {msg}", order_id=body.order_id)
    await _escalate_to_admin(
        db,
        source_role="DRIVER",
        escalation_type="STUCK",
        summary=msg,
        order_id=body.order_id,
    )
    await db.commit()
    return {"notice": msg}


@router.post("/me/sos")
async def sos(
    payload: dict = Depends(require_role("RIDER")),
    db: AsyncSession = Depends(get_db),
):
    driver = await db.get(DriverProfile, payload["sub"])
    gps = hub.last_rider.get(payload["sub"])
    text = (
        f"SOS from rider {driver.driver_name if driver else payload['sub']} "
        f"({payload['sub']}). Last GPS: {gps}"
    )
    await notify_ops(db, message=text)
    await _escalate_to_admin(db, source_role="DRIVER", escalation_type="STUCK", summary=text)
    await db.commit()
    return {"status": "ok", "notice": "Ops notified."}


async def _maybe_complete_trip(db: AsyncSession, driver_phone: str) -> None:
    trip = (
        await db.execute(
            select(DriverTripStatus).where(
                DriverTripStatus.driver_phone == driver_phone,
                DriverTripStatus.status != "COMPLETED",
            )
        )
    ).scalars().first()
    if not trip:
        return
    stops = (
        (
            await db.execute(
                select(SystemDeliveryStop).where(SystemDeliveryStop.route_id == trip.route_id)
            )
        )
        .scalars()
        .all()
    )
    dropoffs = [s for s in stops if s.stop_type not in {"PICKUP", "PICKUP_KITCHEN"}]
    if dropoffs and all(s.status == "COMPLETED" for s in dropoffs):
        await execute_driver_trip_phase_update(
            db, driver_phone=driver_phone, route_id=trip.route_id, target_status="COMPLETED"
        )
