"""Shared tool helpers — pure functions (no DB writes, no LLM, no side effects)."""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta
from typing import Any

# Meal cutoffs. v1 constants — TODO: move to system_settings for runtime flexibility.
LUNCH_CUTOFF = time(11, 30)
DINNER_CUTOFF = time(18, 30)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lng points."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def resolve_time_pool(now: datetime | None = None) -> dict[str, Any]:
    """Map the current time to the orderable meal window.

    Returns {window, service_date, message}:
      - before 11:30        -> today's LUNCH
      - 11:30 .. 18:30      -> today's DINNER
      - after 18:30         -> tomorrow's LUNCH
    """
    now = now or datetime.now()
    t = now.time()
    if t < LUNCH_CUTOFF:
        return {
            "window": "LUNCH",
            "service_date": now.date(),
            "message": f"Ordering for today's lunch (before {LUNCH_CUTOFF.strftime('%I:%M %p')} cutoff).",
        }
    if t < DINNER_CUTOFF:
        return {
            "window": "DINNER",
            "service_date": now.date(),
            "message": f"Lunch is closed — ordering for tonight's dinner (before {DINNER_CUTOFF.strftime('%I:%M %p')} cutoff).",
        }
    return {
        "window": "LUNCH",
        "service_date": now.date() + timedelta(days=1),
        "message": "Today's orders are closed — ordering for tomorrow's lunch.",
    }
