"""Lunch 11:30 / dinner 18:30 cutoffs (IST — Cloud Run runs UTC, the business is Mumbai)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

LUNCH_CUTOFF = time(11, 30)
DINNER_CUTOFF = time(18, 30)

_IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    """Naive IST clock — every cutoff comparison must use this, not datetime.now()."""
    return datetime.now(_IST).replace(tzinfo=None)


def active_window(now: datetime | None = None) -> dict:
    now = now or now_ist()
    minutes = now.hour * 60 + now.minute
    lunch_m = 11 * 60 + 30
    dinner_m = 18 * 60 + 30
    if minutes < lunch_m:
        return {
            "meal_window": "LUNCH",
            "cutoff_time": "11:30 AM",
            "label": "LUNCH · 11:30 AM cutoff",
            "is_past_cutoff": False,
            "service_date": now.date().isoformat(),
        }
    if minutes < dinner_m:
        return {
            "meal_window": "DINNER",
            "cutoff_time": "6:30 PM",
            "label": "DINNER · 6:30 PM cutoff",
            "is_past_cutoff": False,
            "service_date": now.date().isoformat(),
        }
    nxt = now.date() + timedelta(days=1)
    return {
        "meal_window": "LUNCH",
        "cutoff_time": "11:30 AM",
        "label": "LUNCH · next window (today's dinner cutoff passed)",
        "is_past_cutoff": True,
        "service_date": nxt.isoformat(),
    }


def cutoff_datetime(service_date: date, meal_window: str) -> datetime:
    stamp = LUNCH_CUTOFF if meal_window.upper() == "LUNCH" else DINNER_CUTOFF
    return datetime.combine(service_date, stamp)


def is_past_cutoff(meal_window: str, service_date: date, now: datetime | None = None) -> bool:
    now = now or now_ist()
    return now >= cutoff_datetime(service_date, meal_window)
