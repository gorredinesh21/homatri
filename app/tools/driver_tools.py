"""Driver-domain tools (Flow 7 core loop): profile, register, duty, route, pickup, delivery.

The driver never types a stop_id — tools resolve the current stop from the trip/route.
"Next leg" = the earliest stop whose status isn't COMPLETED (robust to out-of-order
delivery). pickup/delivery RETURN the next leg's full details in their message
(guard-then-guide), so the agent shows the driver where to go next in one turn.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionFactory, transaction
from app.executors.driver import execute_driver_profile_upsert, execute_driver_trip_phase_update
from app.executors.master import execute_outbound_whatsapp_enqueue
from app.models.customer import CustomerOrder, CustomerOrderItem, CustomerProfile
from app.models.driver import DriverProfile, DriverTripStatus
from app.models.system import SystemDeliveryStop, SystemDeliveryStopOrder
from app.tools.customer_tools import _fuzzy_match
from app.tools.delegate import delegate_write


# ---- shared helpers --------------------------------------------------------
async def _active_trip(session: AsyncSession, driver_phone: str) -> DriverTripStatus | None:
    return (
        await session.execute(
            select(DriverTripStatus).where(
                DriverTripStatus.driver_phone == driver_phone,
                DriverTripStatus.status != "COMPLETED",
            ).order_by(DriverTripStatus.created_at.desc())
        )
    ).scalars().first()


async def _route_stops(session: AsyncSession, route_id: str) -> list[SystemDeliveryStop]:
    return (
        await session.execute(
            select(SystemDeliveryStop).where(SystemDeliveryStop.route_id == route_id)
            .order_by(SystemDeliveryStop.stop_index)
        )
    ).scalars().all()


def _next_pending(stops: list[SystemDeliveryStop]) -> SystemDeliveryStop | None:
    for s in stops:
        if s.status != "COMPLETED":
            return s
    return None


async def _stop_order_ids(session: AsyncSession, stop_id: str) -> list[str]:
    return [
        so.order_id for so in (
            await session.execute(
                select(SystemDeliveryStopOrder).where(SystemDeliveryStopOrder.stop_id == stop_id)
            )
        ).scalars().all()
    ]


def _maps_url(stop: SystemDeliveryStop) -> str:
    return stop.single_leg_maps_url or (
        f"https://www.google.com/maps/dir/?api=1&destination="
        f"{float(stop.latitude)},{float(stop.longitude)}&travelmode=driving"
    )


async def _format_leg(session: AsyncSession, stop: SystemDeliveryStop) -> str:
    """Human-readable next-leg card: type, address, maps link, and (dropoffs) the orders."""
    if stop.stop_type == "PICKUP":
        return (f"🍴 PICKUP · {stop.location_name}\n   {stop.address}\n   🗺️ {_maps_url(stop)}")
    lines = []
    for oid in await _stop_order_ids(session, stop.stop_id):
        o = await session.get(CustomerOrder, oid)
        cust = await session.get(CustomerProfile, o.customer_phone) if o else None
        items = (
            await session.execute(select(CustomerOrderItem).where(CustomerOrderItem.order_id == oid))
        ).scalars().all()
        itemstr = ", ".join(f"{it.quantity}× {it.dish_name}" for it in items) or "(items)"
        note = f"  ⚠️ {o.special_instructions}" if (o and o.special_instructions) else ""
        lines.append(f"      • {oid} — {cust.name if cust else (o.customer_phone if o else '?')}: {itemstr}{note}")
    return (f"🏠 DROP · {stop.location_name} (stop {stop.stop_index})\n   {stop.address}\n"
            f"   🗺️ {_maps_url(stop)}\n   Orders:\n" + "\n".join(lines))


async def _advance_trip(session: AsyncSession, *, trip: DriverTripStatus, stops: list[SystemDeliveryStop]) -> SystemDeliveryStop | None:
    """Recompute progress after a stop is completed; set trip phase + return the next leg (or None)."""
    completed = sum(1 for s in stops if s.status == "COMPLETED")
    nxt = _next_pending(stops)
    if nxt is None:
        await execute_driver_trip_phase_update(
            session, driver_phone=trip.driver_phone, route_id=trip.route_id,
            target_status="COMPLETED", completed_stops=completed)
    else:
        await execute_driver_trip_phase_update(
            session, driver_phone=trip.driver_phone, route_id=trip.route_id,
            target_status="EN_ROUTE_DELIVERY", current_stop_index=nxt.stop_index, completed_stops=completed)
    return nxt


# =============================================================================
# TOOL: get_driver_profile / register_driver / update_duty_status
# =============================================================================
class GetDriverProfileInput(BaseModel):
    driver_phone: str = Field(..., description="Normalized 10-digit driver phone.")


async def _get_driver_profile(session: AsyncSession, *, driver_phone: str) -> dict[str, Any]:
    d = await session.get(DriverProfile, driver_phone)
    if d is None:
        return {"status": "NOT_FOUND", "message": f"No driver registered for {driver_phone}."}
    duty = "on shift" if d.is_on_shift else "off shift"
    return {"status": "FOUND", "message": f"Driver {d.driver_name} ({d.vehicle_type} {d.vehicle_number}) — {duty}."}


@tool("get_driver_profile", args_schema=GetDriverProfileInput)
async def get_driver_profile(driver_phone: str) -> str:
    """Identify a driver by phone on an inbound message."""
    async with SessionFactory() as session:
        return (await _get_driver_profile(session, driver_phone=driver_phone))["message"]


# NOTE: drivers (like chefs) are onboarded by the admin/seed — there is deliberately
# no register_driver self-onboarding tool. update_duty_status only toggles shift.
class UpdateDutyStatusInput(BaseModel):
    driver_phone: str = Field(..., description="Normalized 10-digit driver phone.")
    on_duty: bool = Field(..., description="True = on shift, False = off shift.")


async def _update_duty_status(session: AsyncSession, *, driver_phone: str, on_duty: bool) -> dict[str, Any]:
    d = await session.get(DriverProfile, driver_phone)
    if d is None:
        return {"status": "NOT_FOUND", "message": f"No driver registered for {driver_phone}."}
    await execute_driver_profile_upsert(
        session, driver_phone=driver_phone, driver_name=d.driver_name, vehicle_type=d.vehicle_type,
        vehicle_number=d.vehicle_number, is_on_shift=on_duty)
    return {"status": "ON_DUTY" if on_duty else "OFF_DUTY",
            "message": "You're on shift ✅" if on_duty else "You're off shift. See you next time!"}


@tool("update_duty_status", args_schema=UpdateDutyStatusInput)
async def update_duty_status(driver_phone: str, on_duty: bool) -> str:
    """Set a driver on or off shift."""
    async with transaction() as session:
        res = await _update_duty_status(session, driver_phone=driver_phone, on_duty=on_duty)
        return res["message"]


# =============================================================================
# TOOL: get_driver_route  (READ — next leg only)
# =============================================================================
class GetDriverRouteInput(BaseModel):
    driver_phone: str = Field(..., description="Normalized 10-digit driver phone.")


async def _get_driver_route(session: AsyncSession, *, driver_phone: str) -> dict[str, Any]:
    trip = await _active_trip(session, driver_phone)
    if trip is None:
        return {"status": "NO_ROUTE", "message": "You don't have a route assigned yet."}
    stops = await _route_stops(session, trip.route_id)
    nxt = _next_pending(stops)
    completed = sum(1 for s in stops if s.status == "COMPLETED")
    if nxt is None:
        return {"status": "OK", "message": f"🎉 Route complete — all {len(stops)} stops done."}
    leg = await _format_leg(session, nxt)
    return {"status": "OK",
            "message": f"Progress {completed}/{len(stops)} stops. Next:\n{leg}"}


@tool("get_driver_route", args_schema=GetDriverRouteInput)
async def get_driver_route(driver_phone: str) -> str:
    """Show the driver's next stop only (address, maps link, and the orders there)."""
    async with SessionFactory() as session:
        return (await _get_driver_route(session, driver_phone=driver_phone))["message"]


# =============================================================================
# TOOL: confirm_pickup  (cross-domain)
# =============================================================================
class ConfirmPickupInput(BaseModel):
    driver_phone: str = Field(..., description="Normalized 10-digit driver phone.")


async def _confirm_pickup(session: AsyncSession, *, driver_phone: str) -> dict[str, Any]:
    trip = await _active_trip(session, driver_phone)
    if trip is None:
        return {"status": "NO_ROUTE", "message": "You don't have a route assigned yet."}
    stops = await _route_stops(session, trip.route_id)
    pickup = next((s for s in stops if s.stop_type == "PICKUP"), None)
    if pickup is None:
        return {"status": "WRONG_STOP", "message": "This route has no pickup stop."}
    if pickup.status == "COMPLETED":
        return {"status": "ALREADY_DONE", "message": "You've already picked up — head to your next stop (get_driver_route)."}

    # D2: all the route's orders must be PACKED (chef marked ready) before pickup.
    all_order_ids: list[str] = []
    for s in stops:
        all_order_ids += await _stop_order_ids(session, s.stop_id)
    orders = [await session.get(CustomerOrder, oid) for oid in all_order_ids]
    not_ready = [o.order_id for o in orders if o and o.status != "PACKED"]
    if not_ready:
        return {"status": "NOT_READY",
                "message": "The kitchen hasn't packed all orders yet. Ask them with ask_chef_status, then try again."}

    for o in orders:
        await delegate_write(session, requesting_role="DRIVER", capability="ORDER_STATUS",
                             order_id=o.order_id, target_status="PICKED_UP", actor_role="DRIVER")
    await delegate_write(session, requesting_role="DRIVER", capability="STOP_STATUS",
                         stop_id=pickup.stop_id, target_status="COMPLETED")
    stops = await _route_stops(session, trip.route_id)   # refresh statuses
    nxt = await _advance_trip(session, trip=trip, stops=stops)

    head = f"✅ Picked up {len(orders)} order(s) from {pickup.location_name}."
    if nxt is None:
        return {"status": "PICKED_UP", "message": head + " No delivery stops on this route."}
    return {"status": "PICKED_UP", "message": head + "\n\nNext stop:\n" + await _format_leg(session, nxt)}


@tool("confirm_pickup", args_schema=ConfirmPickupInput)
async def confirm_pickup(driver_phone: str) -> str:
    """Driver picked up at the kitchen — marks orders PICKED_UP and reveals the first delivery."""
    async with transaction() as session:
        res = await _confirm_pickup(session, driver_phone=driver_phone)
        return res["message"]


# =============================================================================
# TOOL: confirm_delivery  (cross-domain)
# =============================================================================
class ConfirmDeliveryInput(BaseModel):
    driver_phone: str = Field(..., description="Normalized 10-digit driver phone.")
    location: str | None = Field(default=None, description="Apartment/address name IF delivering out of order; else omit for the current stop.")
    undelivered_ids: list[str] | None = Field(default=None, description="order ids at this stop that could NOT be delivered.")


async def _confirm_delivery(
    session: AsyncSession, *, driver_phone: str, location: str | None = None,
    undelivered_ids: list[str] | None = None,
) -> dict[str, Any]:
    trip = await _active_trip(session, driver_phone)
    if trip is None:
        return {"status": "NO_ROUTE", "message": "You don't have a route assigned yet."}
    stops = await _route_stops(session, trip.route_id)
    pending_drops = [s for s in stops if s.status != "COMPLETED" and s.stop_type != "PICKUP"]

    if location:
        stop, err = _fuzzy_match(location, pending_drops, [lambda s: s.location_name, lambda s: s.address])
        if stop is None:
            return {"status": "WRONG_STOP", "message": f"No pending stop matches '{location}'. Say the apartment/area name from your route."}
    else:
        nxt = _next_pending(stops)
        if nxt is not None and nxt.stop_type == "PICKUP":
            return {"status": "WRONG_STOP", "message": "Confirm the pickup at the kitchen first (confirm_pickup)."}
        if nxt is None:
            return {"status": "WRONG_STOP", "message": "All stops are already done."}
        stop = nxt

    undelivered = set(undelivered_ids or [])
    order_ids = await _stop_order_ids(session, stop.stop_id)
    delivered = [oid for oid in order_ids if oid not in undelivered]
    for oid in delivered:
        await delegate_write(session, requesting_role="DRIVER", capability="ORDER_STATUS",
                             order_id=oid, target_status="DELIVERED", actor_role="DRIVER")
    await delegate_write(session, requesting_role="DRIVER", capability="STOP_STATUS",
                         stop_id=stop.stop_id, target_status="COMPLETED")

    # relay_delivery_completed_to_customer: notify each delivered customer
    for oid in delivered:
        o = await session.get(CustomerOrder, oid)
        if o:
            await execute_outbound_whatsapp_enqueue(
                session, recipient_phone=o.customer_phone, recipient_role="CUSTOMER", related_order_id=oid,
                message_text=f"🎉 Your order {oid} from {o.kitchen_name} has been delivered. Enjoy! (You can leave a review.)")

    stops = await _route_stops(session, trip.route_id)
    nxt = await _advance_trip(session, trip=trip, stops=stops)

    head = f"✅ Delivered at {stop.location_name} ({len(delivered)} order(s))."
    if undelivered:
        head += f" ⚠️ Not delivered: {', '.join(sorted(undelivered))}."
    status = "PARTIAL" if undelivered else "DELIVERED"
    if nxt is None:
        return {"status": status, "delivered_ids": delivered, "message": head + "\n\n🎉 Route complete — all deliveries done!"}
    return {"status": status, "delivered_ids": delivered,
            "message": head + "\n\nNext stop:\n" + await _format_leg(session, nxt)}


@tool("confirm_delivery", args_schema=ConfirmDeliveryInput)
async def confirm_delivery(driver_phone: str, location: str | None = None, undelivered_ids: list | None = None) -> str:
    """Driver delivered at a gate — marks those orders DELIVERED (bulk, minus any exceptions) and reveals the next stop."""
    ids = [str(x) for x in undelivered_ids] if undelivered_ids else None
    async with transaction() as session:
        res = await _confirm_delivery(session, driver_phone=driver_phone, location=location, undelivered_ids=ids)
        return res["message"]


# =============================================================================
# ask_chef_status (Driver) + respond_to_driver_query (Chef) — a 2-turn HITL:
# "is it ready?" -> if already PACKED, answer instantly; else ask the kitchen and
# relay the chef's free-text reply back to the driver.
# =============================================================================
READY_STATUSES = ("PACKED", "PICKED_UP", "DELIVERED")

# In-memory driver-query store, keyed by driver_phone.
_driver_queries: dict[str, dict[str, Any]] = {}


def _open_query_for_chef(chef_phone: str) -> dict | None:
    for q in _driver_queries.values():
        if q["chef_phone"] == chef_phone and q["status"] == "WAITING":
            return q
    return None


def clear_driver_query(phone: str) -> None:
    _driver_queries.pop(phone, None)


class AskChefStatusInput(BaseModel):
    driver_phone: str = Field(..., description="Normalized 10-digit driver phone.")


async def _ask_chef_status(session: AsyncSession, *, driver_phone: str) -> dict[str, Any]:
    """Is the batch packed? Instant READY from status; else ask the chef (await their reply).

    Guard: no active route -> NO_ROUTE.
    """
    trip = await _active_trip(session, driver_phone)
    if trip is None:
        return {"status": "NO_ROUTE", "message": "You don't have a route assigned yet."}
    stops = await _route_stops(session, trip.route_id)
    orders = []
    for s in stops:
        for oid in await _stop_order_ids(session, s.stop_id):
            o = await session.get(CustomerOrder, oid)
            if o:
                orders.append(o)
    if not orders:
        return {"status": "NO_ROUTE", "message": "There are no orders on your route."}

    if all(o.status in READY_STATUSES for o in orders):
        return {"status": "READY", "message": "✅ Your order is packed and ready — head in and pick it up!"}

    pending = [o.order_id for o in orders if o.status not in READY_STATUSES]
    chef_phone = orders[0].chef_phone
    _driver_queries[driver_phone] = {"driver_phone": driver_phone, "chef_phone": chef_phone,
                                     "orders": pending, "status": "WAITING"}
    await execute_outbound_whatsapp_enqueue(
        session, recipient_phone=chef_phone, recipient_role="CHEF",
        message_text=(f"🛵 Driver {driver_phone} is waiting at pickup for: {', '.join(pending)}. "
                      f"How long? Reply and I'll pass it on."))
    return {"status": "ASKED",
            "message": "⏳ Not packed yet — I've asked the kitchen how long. Their reply will show up here."}


@tool("ask_chef_status", args_schema=AskChefStatusInput)
async def ask_chef_status(driver_phone: str) -> str:
    """Ask the kitchen if the driver's order is ready; relays their reply back to the driver."""
    async with transaction() as session:
        res = await _ask_chef_status(session, driver_phone=driver_phone)
        return res["message"]


class RespondToDriverQueryInput(BaseModel):
    chef_phone: str = Field(..., description="Normalized 10-digit chef phone.")
    reply: str = Field(..., description="Free-text reply to the driver, e.g. '5 more minutes'.")


async def _respond_to_driver_query(session: AsyncSession, *, chef_phone: str, reply: str) -> dict[str, Any]:
    """Chef's free-text reply to a waiting driver; pushes it to the driver. Guard: no open query."""
    q = _open_query_for_chef(chef_phone)
    if q is None:
        return {"status": "NO_QUERY", "message": "No driver is waiting on a reply from you."}
    q["status"] = "RESOLVED"
    await execute_outbound_whatsapp_enqueue(
        session, recipient_phone=q["driver_phone"], recipient_role="DRIVER",
        message_text=f"🍴 Kitchen says: {reply}")
    return {"status": "SENT", "message": "Reply sent to the driver 👍"}


@tool("respond_to_driver_query", args_schema=RespondToDriverQueryInput)
async def respond_to_driver_query(chef_phone: str, reply: str) -> str:
    """Chef: reply to a driver waiting on 'is it ready?' — the reply is relayed to them."""
    async with transaction() as session:
        res = await _respond_to_driver_query(session, chef_phone=chef_phone, reply=reply)
        return res["message"]
