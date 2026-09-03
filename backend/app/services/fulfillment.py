"""Order snapshots, delivery OTPs, packed/pickup/deliver transitions."""

from __future__ import annotations

import secrets
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.executors.chef import execute_order_readiness_record
from backend.app.executors.driver import execute_driver_trip_phase_update
from backend.app.executors.master import execute_outbound_whatsapp_enqueue
from backend.app.models.chef import ChefProfile
from backend.app.models.customer import CustomerOrder, CustomerOrderItem, CustomerProfile
from backend.app.models.driver import DriverLocationPing, DriverProfile, DriverTripStatus
from backend.app.models.system import SystemDeliveryRoute, SystemDeliveryStop, SystemDeliveryStopOrder
from backend.app.services.live_hub import hub
from backend.app.services.meal_clock import active_window
from backend.app.services.notify import notify_order


def new_otp() -> str:
    return f"{secrets.randbelow(9000) + 1000:04d}"


def gate_id(lat: float | None, lng: float | None) -> str:
    if lat is None or lng is None:
        return "gate-unknown"
    return f"gate-{round(float(lat), 4)}-{round(float(lng), 4)}"


async def serialize_order(session: AsyncSession, order: CustomerOrder) -> dict[str, Any]:
    items = (
        (
            await session.execute(
                select(CustomerOrderItem).where(CustomerOrderItem.order_id == order.order_id)
            )
        )
        .scalars()
        .all()
    )
    rider = None
    location = None
    so = (
        await session.execute(
            select(SystemDeliveryStopOrder).where(SystemDeliveryStopOrder.order_id == order.order_id)
        )
    ).scalars().first()
    if so:
        stop = await session.get(SystemDeliveryStop, so.stop_id)
        if stop:
            route = await session.get(SystemDeliveryRoute, stop.route_id)
            if route:
                driver = await session.get(DriverProfile, route.driver_phone)
                ping = hub.last_rider.get(route.driver_phone)
                if ping is None:
                    last = (
                        await session.execute(
                            select(DriverLocationPing)
                            .where(DriverLocationPing.driver_phone == route.driver_phone)
                            .order_by(DriverLocationPing.recorded_at.desc())
                            .limit(1)
                        )
                    ).scalars().first()
                    if last:
                        ping = {
                            "latitude": float(last.latitude),
                            "longitude": float(last.longitude),
                            "heading": float(last.heading) if last.heading is not None else None,
                            "timestamp": last.recorded_at.isoformat(),
                        }
                if driver:
                    rider = {
                        "driver_phone": driver.driver_phone,
                        "driver_name": driver.driver_name,
                        "vehicle_number": driver.vehicle_number,
                        "vehicle_type": driver.vehicle_type,
                    }
                location = ping
    return {
        "order_id": order.order_id,
        "order_status": order.status,
        "status": order.status,
        "customer_phone": order.customer_phone,
        "chef_phone": order.chef_phone,
        "kitchen_name": order.kitchen_name,
        "meal_window": order.meal_window,
        "service_date": order.service_date.isoformat() if order.service_date else None,
        "cart_subtotal": float(order.cart_subtotal),
        "delivery_fee": float(order.delivery_fee),
        "total_amount": float(order.total_amount),
        "payment_method": order.payment_method,
        "payment_status": order.payment_status,
        "delivery_address": order.delivery_address,
        "special_instructions": order.special_instructions,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "items": [
            {
                "menu_item_id": it.menu_item_id,
                "dish_name": it.dish_name,
                "quantity": it.quantity,
                "unit_price": float(it.unit_price),
                "item_subtotal": float(it.item_subtotal),
                "special_instructions": it.special_instructions,
            }
            for it in items
        ],
        "rider": rider,
        "rider_location": location,
        "needs_otp": order.status in {"PICKED_UP", "PACKED"} and bool(order.delivery_otp),
    }


async def issue_otps_for_orders(session: AsyncSession, orders: list[CustomerOrder]) -> None:
    for order in orders:
        if order.delivery_otp:
            continue
        order.delivery_otp = new_otp()
        await execute_outbound_whatsapp_enqueue(
            session,
            recipient_phone=order.customer_phone,
            recipient_role="CUSTOMER",
            message_text=(
                f"Your Homatri delivery PIN for {order.kitchen_name} is {order.delivery_otp}. "
                "Share it only with the rider at your gate."
            ),
            related_order_id=order.order_id,
        )


async def chef_earnings(session: AsyncSession, chef_phone: str) -> dict[str, Any]:
    today = date.today()
    delivered = (
        select(CustomerOrder).where(
            CustomerOrder.chef_phone == chef_phone,
            CustomerOrder.status == "DELIVERED",
        )
    )
    rows = (await session.execute(delivered)).scalars().all()
    today_income = sum(float(o.total_amount) - float(o.delivery_fee) for o in rows if o.service_date == today)
    week_income = sum(
        float(o.total_amount) - float(o.delivery_fee)
        for o in rows
        if o.service_date and (today - o.service_date).days < 7
    )
    phones = {o.customer_phone for o in rows}
    repeats = 0
    for phone in phones:
        n = sum(1 for o in rows if o.customer_phone == phone)
        if n > 1:
            repeats += 1
    retention = round((repeats / len(phones)) * 100) if phones else 0
    return {
        "todayIncome": round(today_income, 2),
        "weeklyPayout": round(week_income, 2),
        "completedOrders": len(rows),
        "repeatRetentionPct": retention,
    }


async def current_trip_payload(session: AsyncSession, driver_phone: str) -> dict[str, Any]:
    window = active_window()
    driver = await session.get(DriverProfile, driver_phone)
    if driver is None:
        return {"status": "NO_PROFILE"}
    trip = (
        await session.execute(
            select(DriverTripStatus)
            .where(
                DriverTripStatus.driver_phone == driver_phone,
                DriverTripStatus.status != "COMPLETED",
            )
            .order_by(DriverTripStatus.created_at.desc())
        )
    ).scalars().first()
    if trip is None:
        return {
            "status": "NO_TRIP",
            "shift_on": bool(driver.is_on_shift or driver.on_shift),
            "rider": {
                "riderId": driver.driver_phone,
                "fullName": driver.driver_name,
                "phoneNumber": driver.driver_phone,
                "vehicleNumber": driver.vehicle_number,
            },
            "windowInfo": window,
            "machineState": "ON_SHIFT" if (driver.is_on_shift or driver.on_shift) else "OFF_SHIFT",
            "stops": [],
            "kitchen": None,
        }
    stops = (
        (
            await session.execute(
                select(SystemDeliveryStop)
                .where(SystemDeliveryStop.route_id == trip.route_id)
                .order_by(SystemDeliveryStop.stop_index)
            )
        )
        .scalars()
        .all()
    )
    kitchen = None
    serialized_stops = []
    pickup_done = False
    for stop in stops:
        links = (
            (
                await session.execute(
                    select(SystemDeliveryStopOrder).where(SystemDeliveryStopOrder.stop_id == stop.stop_id)
                )
            )
            .scalars()
            .all()
        )
        if stop.stop_type in {"PICKUP", "PICKUP_KITCHEN"}:
            chef = await session.get(ChefProfile, stop.target_ref_id)
            kitchen = {
                "chefId": stop.target_ref_id,
                "kitchenName": stop.location_name,
                "chefName": chef.chef_name if chef else "",
                "address": stop.address,
                "latitude": float(stop.latitude),
                "longitude": float(stop.longitude),
            }
            pickup_done = stop.status == "COMPLETED"
            continue
        for link in links:
            order = await session.get(CustomerOrder, link.order_id)
            cust = await session.get(CustomerProfile, order.customer_phone) if order else None
            qty = 0
            if order:
                qty = int(
                    (
                        await session.execute(
                            select(func.coalesce(func.sum(CustomerOrderItem.quantity), 0)).where(
                                CustomerOrderItem.order_id == order.order_id
                            )
                        )
                    ).scalar_one()
                    or 0
                )
            serialized_stops.append(
                {
                    "stopId": stop.stop_id,
                    "stopNumber": stop.stop_index,
                    "gateId": gate_id(float(stop.latitude), float(stop.longitude)),
                    "orderId": link.order_id,
                    "customerName": stop.location_name,
                    "customerPhone": order.customer_phone if order else stop.target_ref_id,
                    "address": stop.address,
                    "latitude": float(stop.latitude),
                    "longitude": float(stop.longitude),
                    "tiffinCount": qty,
                    "paymentMethod": order.payment_method if order else None,
                    "paymentStatus": order.payment_status if order else None,
                    "amountToCollect": float(order.total_amount)
                    if order and order.payment_method == "COD" and order.payment_status != "COD_COLLECTED"
                    else None,
                    "status": "PENDING"
                    if stop.status == "PENDING" and order and order.status not in {"DELIVERED", "CANCELLED", "UNDELIVERED"}
                    else (order.status if order else stop.status),
                    "mapsUrl": stop.single_leg_maps_url,
                }
            )

    pending = [s for s in serialized_stops if s["status"] == "PENDING"]
    machine = "OFF_SHIFT"
    if driver.is_on_shift or driver.on_shift:
        if not pickup_done:
            machine = "ASSIGNED_BATCH"
        elif not pending:
            machine = "BATCH_COMPLETED"
        else:
            machine = "DELIVERIES_IN_PROGRESS"

    return {
        "status": "OK",
        "shift_on": bool(driver.is_on_shift or driver.on_shift),
        "trip_id": trip.trip_id,
        "route_id": trip.route_id,
        "trip_status": trip.status,
        "pickupDone": pickup_done,
        "machineState": machine,
        "windowInfo": window,
        "rider": {
            "riderId": driver.driver_phone,
            "fullName": driver.driver_name,
            "phoneNumber": driver.driver_phone,
            "vehicleNumber": driver.vehicle_number,
        },
        "kitchen": kitchen,
        "stops": serialized_stops,
        "tiffinCount": sum(s["tiffinCount"] for s in serialized_stops),
        "gps": hub.last_rider.get(driver_phone),
    }


async def mark_kitchen_packed(session: AsyncSession, chef_phone: str, meal_window: str, service_date: date) -> dict[str, Any]:
    orders = (
        (
            await session.execute(
                select(CustomerOrder).where(
                    CustomerOrder.chef_phone == chef_phone,
                    CustomerOrder.meal_window == meal_window,
                    CustomerOrder.service_date == service_date,
                    CustomerOrder.status.in_(("BATCHED", "COOKING", "CONFIRMED")),
                )
            )
        )
        .scalars()
        .all()
    )
    if not orders:
        return {"status": "NO_BATCH", "message": "No orders to pack for this window."}
    driver_phone = None
    for order in orders:
        order.status = "PACKED"
        await execute_order_readiness_record(
            session,
            order_id=order.order_id,
            chef_phone=chef_phone,
        )
        so = (
            await session.execute(
                select(SystemDeliveryStopOrder).where(SystemDeliveryStopOrder.order_id == order.order_id)
            )
        ).scalars().first()
        if so:
            stop = await session.get(SystemDeliveryStop, so.stop_id)
            if stop:
                route = await session.get(SystemDeliveryRoute, stop.route_id)
                if route:
                    driver_phone = route.driver_phone
        await notify_order(
            session,
            order,
            message=f"{order.kitchen_name} packed your {order.meal_window.lower()} tiffin. Rider is heading to pickup.",
        )
    await issue_otps_for_orders(session, orders)
    if driver_phone:
        chef = await session.get(ChefProfile, chef_phone)
        await execute_outbound_whatsapp_enqueue(
            session,
            recipient_phone=driver_phone,
            recipient_role="DRIVER",
            message_text=f"Kitchen ready: {chef.kitchen_name if chef else chef_phone} marked the batch packed.",
        )
        trip = (
            await session.execute(
                select(DriverTripStatus).where(
                    DriverTripStatus.driver_phone == driver_phone,
                    DriverTripStatus.status != "COMPLETED",
                )
            )
        ).scalars().first()
        if trip:
            await execute_driver_trip_phase_update(
                session,
                driver_phone=driver_phone,
                route_id=trip.route_id,
                target_status="EN_ROUTE_PICKUP",
            )
    return {"status": "PACKED", "orders": len(orders), "message": f"Packed {len(orders)} order(s)."}
