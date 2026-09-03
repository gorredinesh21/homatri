"""Persist rider GPS and fan out to live order streams."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from backend.app.core.ids import generate_id
from backend.app.db.session import SessionFactory
from backend.app.models.driver import DriverLocationPing
from backend.app.models.system import SystemDeliveryRoute, SystemDeliveryStop, SystemDeliveryStopOrder
from backend.app.services.live_hub import hub


async def record_rider_fix(driver_phone: str, latitude: float, longitude: float, heading: float | None = None) -> dict:
    now = datetime.now()
    payload = {
        "driver_phone": driver_phone,
        "latitude": latitude,
        "longitude": longitude,
        "heading": heading,
        "timestamp": now.isoformat(),
    }
    hub.remember_rider(driver_phone, payload)
    async with SessionFactory() as db:
        db.add(
            DriverLocationPing(
                ping_id=generate_id("gps"),
                driver_phone=driver_phone,
                latitude=Decimal(str(latitude)),
                longitude=Decimal(str(longitude)),
                heading=Decimal(str(heading)) if heading is not None else None,
                recorded_at=now,
            )
        )
        routes = (
            (
                await db.execute(
                    select(SystemDeliveryRoute).where(
                        SystemDeliveryRoute.driver_phone == driver_phone,
                        SystemDeliveryRoute.status != "COMPLETED",
                    )
                )
            )
            .scalars()
            .all()
        )
        order_ids: list[str] = []
        for route in routes:
            stops = (
                (
                    await db.execute(
                        select(SystemDeliveryStop.stop_id).where(SystemDeliveryStop.route_id == route.route_id)
                    )
                )
                .scalars()
                .all()
            )
            if not stops:
                continue
            links = (
                (
                    await db.execute(
                        select(SystemDeliveryStopOrder.order_id).where(SystemDeliveryStopOrder.stop_id.in_(stops))
                    )
                )
                .scalars()
                .all()
            )
            order_ids.extend(links)
        await db.commit()
    for oid in order_ids:
        await hub.publish_order(oid, {"order_id": oid, "rider_location": payload, "event": "gps"})
    return payload
