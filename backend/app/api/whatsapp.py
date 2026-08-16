"""WhatsApp Cloud API webhook adapter.

Turns Meta's inbound webhook payload into a normalized internal message, and
handles the GET verification handshake. The same parser is used by the dev
harness (which POSTs Meta-shaped payloads) and by real WhatsApp — so the router
downstream never sees WhatsApp-specific JSON.
"""

from __future__ import annotations

import re
from typing import Any


def normalize_phone(raw: str) -> str:
    """Canonical 10-digit Indian phone: strip non-digits, drop country code, keep last 10.

    '917416767453' / '+91 74167 67453' / '7416767453'  ->  '7416767453'
    """
    digits = re.sub(r"\D", "", raw or "")
    return digits[-10:] if len(digits) >= 10 else digits


def parse_webhook(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Parse a Meta Cloud API webhook payload into `{phone, type, text?|location?}`.

    Returns None for non-message callbacks (delivery statuses, etc.).
    """
    try:
        value = payload["entry"][0]["changes"][0]["value"]
        msg = value["messages"][0]
    except (KeyError, IndexError, TypeError):
        return None  # e.g. a status callback — nothing to route

    phone = normalize_phone(msg.get("from", ""))
    mtype = msg.get("type")
    wamid = msg.get("id")

    if mtype == "text":
        return {"phone": phone, "type": "text", "text": msg["text"]["body"], "wamid": wamid}
    if mtype == "location":
        loc = msg["location"]
        return {
            "phone": phone,
            "type": "location",
            "location": {"latitude": float(loc["latitude"]), "longitude": float(loc["longitude"])},
            "wamid": wamid,
        }
    # images/audio/etc. — surface as text so the agent can respond gracefully
    return {"phone": phone, "type": "text", "text": f"[unsupported message type: {mtype}]", "wamid": wamid}


def verify_challenge(mode: str, token: str, challenge: str, expected_token: str) -> str | None:
    """Meta GET webhook verification. Returns the challenge string if the token matches."""
    if mode == "subscribe" and token == expected_token:
        return challenge
    return None
