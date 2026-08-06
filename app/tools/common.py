"""Shared tool helpers — pure functions (no DB writes, no LLM, no side effects)."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from app.core.geo import haversine_km  # re-exported for existing tool imports

# Meal cutoffs. v1 constants — TODO: move to system_settings for runtime flexibility.
LUNCH_CUTOFF = time(11, 30)
DINNER_CUTOFF = time(18, 30)

__all__ = ["haversine_km", "resolve_time_pool", "describe_meal_window", "LUNCH_CUTOFF", "DINNER_CUTOFF"]


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


def describe_meal_window(now: datetime | None = None) -> str:
    """Human phrase for the current orderable window, WITH day context.

    e.g. "today's lunch", "tonight's dinner", or
    "tomorrow's lunch (today's dinner ordering has closed)". Used so customer-facing
    messages don't just say a bare "lunch" at 7 PM.
    """
    now = now or datetime.now()
    pool = resolve_time_pool(now)
    win = pool["window"].lower()
    if pool["service_date"] > now.date():
        return f"tomorrow's {win} (today's dinner ordering has closed)"
    if pool["window"] == "DINNER":
        return "tonight's dinner"
    return f"today's {win}"
