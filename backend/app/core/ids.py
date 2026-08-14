"""Prefixed-UUID primary key generator (Data-Integrity Guard: ID standard).

Format: <3-4 letter prefix>_<12 hex chars>  e.g. "ord_a1b2c3d4e5f6".
Prevents ID-enumeration attacks and LLM/developer entity confusion.
"""

from __future__ import annotations

import uuid

# Canonical prefix per table (see data_integrity_and_security_guards.md).
PREFIXES: dict[str, str] = {
    "chef_menu_items": "itm",
    "chef_daily_inventory": "inv",
    "chef_order_readiness": "red",
    "customer_orders": "ord",
    "customer_order_items": "ori",
    "customer_payments": "pay",
    "customer_reviews": "rev",
    "driver_trip_status": "trp",
    "system_meal_windows": "win",
    "system_delivery_routes": "rt",
    "system_delivery_stops": "stp",
    "system_agent_logs": "log",
    "system_outbound_queue": "out",
    "system_hitl_sessions": "hitl",
    "system_payment_webhook_events": "evt",
    "system_route_optimization_runs": "run",
    "conversation_messages": "msg",
    "admin_users": "adm",
    "admin_activity_log": "act",
    "admin_ai_queries": "aiq",
}


def new_id(prefix: str) -> str:
    """Return a new prefixed id, e.g. new_id('ord') -> 'ord_a1b2c3d4e5f6'."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


generate_id = new_id


def id_factory(prefix: str):
    """Return a zero-arg callable for use as a SQLAlchemy column default."""
    return lambda: new_id(prefix)

