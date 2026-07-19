"""Builders for outbound WhatsApp Cloud API message payloads.

These produce the exact JSON the real ``/messages`` endpoint accepts, and they
enforce Meta's documented constraints (button/list limits, title lengths) so
the mock rejects the same payloads Meta would. Shared by both providers.
"""
from __future__ import annotations

from typing import Any


class MessageValidationError(ValueError):
    pass


def _norm_to(to: str) -> str:
    return to if to.startswith("+") else f"+{to}"


def text_message(to: str, body: str, preview_url: bool = False) -> dict[str, Any]:
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _norm_to(to),
        "type": "text",
        "text": {"preview_url": preview_url, "body": body},
    }


def button_message(
    to: str,
    body: str,
    buttons: list[tuple[str, str]],
    header: str | None = None,
    footer: str | None = None,
) -> dict[str, Any]:
    if not 1 <= len(buttons) <= 3:
        raise MessageValidationError("reply buttons must number 1..3")
    seen = set()
    reply_buttons = []
    for bid, title in buttons:
        if len(title) > 20:
            raise MessageValidationError(f"button title >20 chars: {title!r}")
        if bid in seen:
            raise MessageValidationError(f"duplicate button id: {bid}")
        seen.add(bid)
        reply_buttons.append({"type": "reply", "reply": {"id": bid, "title": title}})
    interactive: dict[str, Any] = {
        "type": "button",
        "body": {"text": body},
        "action": {"buttons": reply_buttons},
    }
    if header:
        interactive["header"] = {"type": "text", "text": header}
    if footer:
        interactive["footer"] = {"text": footer}
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _norm_to(to),
        "type": "interactive",
        "interactive": interactive,
    }


def list_message(
    to: str,
    body: str,
    button_label: str,
    sections: list[dict[str, Any]],
    header: str | None = None,
    footer: str | None = None,
) -> dict[str, Any]:
    if len(button_label) > 20:
        raise MessageValidationError("list button label >20 chars")
    total_rows = sum(len(s.get("rows", [])) for s in sections)
    if not 1 <= total_rows <= 10:
        raise MessageValidationError("list must have 1..10 rows total")
    seen = set()
    for s in sections:
        for row in s.get("rows", []):
            if len(row.get("title", "")) > 24:
                raise MessageValidationError("row title >24 chars")
            if len(row.get("description", "")) > 72:
                raise MessageValidationError("row description >72 chars")
            if row["id"] in seen:
                raise MessageValidationError(f"duplicate row id: {row['id']}")
            seen.add(row["id"])
    interactive: dict[str, Any] = {
        "type": "list",
        "body": {"text": body},
        "action": {"button": button_label, "sections": sections},
    }
    if header:
        interactive["header"] = {"type": "text", "text": header}
    if footer:
        interactive["footer"] = {"text": footer}
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _norm_to(to),
        "type": "interactive",
        "interactive": interactive,
    }


def location_message(
    to: str,
    latitude: float | str,
    longitude: float | str,
    name: str | None = None,
    address: str | None = None,
) -> dict[str, Any]:
    location: dict[str, Any] = {
        "latitude": str(latitude),
        "longitude": str(longitude),
    }
    if name:
        location["name"] = name
    if address:
        location["address"] = address
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _norm_to(to),
        "type": "location",
        "location": location,
    }
