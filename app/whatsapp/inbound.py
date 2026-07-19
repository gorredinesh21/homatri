"""Parse a Meta Cloud API inbound webhook body into ``InboundMessage`` objects.

Handles text, interactive (button_reply / list_reply), and location messages.
Status-only webhooks (``value.statuses``) yield no messages. Inbound numbers
arrive without a leading '+', which we add for internal consistency.
"""
from __future__ import annotations

from typing import Any

from app.whatsapp.base import InboundMessage


def _e164(num: str) -> str:
    return num if num.startswith("+") else f"+{num}"


def parse_inbound(payload: dict[str, Any]) -> list[InboundMessage]:
    out: list[InboundMessage] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            contacts = value.get("contacts", [])
            profile_name = None
            if contacts:
                profile_name = contacts[0].get("profile", {}).get("name")
            for msg in value.get("messages", []):
                out.append(_parse_message(msg, profile_name))
    return out


def _parse_message(msg: dict[str, Any], profile_name: str | None) -> InboundMessage:
    mtype = msg.get("type", "unknown")
    base = dict(
        from_phone=_e164(msg.get("from", "")),
        wamid=msg.get("id", ""),
        timestamp=msg.get("timestamp"),
        profile_name=profile_name,
    )
    if mtype == "text":
        return InboundMessage(type="text", text=msg["text"]["body"], **base)
    if mtype == "interactive":
        inter = msg.get("interactive", {})
        itype = inter.get("type")
        reply = inter.get(itype, {}) if itype else {}
        return InboundMessage(
            type="interactive",
            reply_id=reply.get("id"),
            reply_title=reply.get("title"),
            **base,
        )
    if mtype == "location":
        loc = msg.get("location", {})
        return InboundMessage(
            type="location",
            latitude=_to_float(loc.get("latitude")),
            longitude=_to_float(loc.get("longitude")),
            **base,
        )
    if mtype == "button":  # template quick-reply
        return InboundMessage(
            type="interactive",
            reply_id=msg.get("button", {}).get("payload"),
            reply_title=msg.get("button", {}).get("text"),
            **base,
        )
    return InboundMessage(type="unknown", **base)


def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
