"""Faithful in-memory mock of the WhatsApp Cloud API.

Outbound sends build the *exact* Meta ``/messages`` payload, mint a ``wamid``,
publish the message to the SSE bus (so the simulator phone renders it), then
emit ``sent``/``delivered`` status events — mirroring the real API's lifecycle.
An inbound helper builds a signed, Meta-shaped webhook body so the simulator's
"device" hits our real ``/webhook`` path exactly as Meta would.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import make_meta_signature
from app.services.events import bus
from app.whatsapp import messages
from app.whatsapp.base import WhatsAppProvider

log = get_logger("wa.mock")

# Homaatri business number the simulator "sends to" (our WABA line).
BUSINESS_NUMBER = "+911140000000"
BUSINESS_DISPLAY = "911140000000"
PHONE_NUMBER_ID = "MOCK_PHONE_NUMBER_ID"
WABA_ID = "MOCK_WABA_ID"


def _mint_wamid() -> str:
    return "wamid.MOCK" + uuid.uuid4().hex.upper()


class MockWhatsAppProvider(WhatsAppProvider):
    name = "mock"

    def __init__(self) -> None:
        # outbound log, for tests / admin inspection
        self.sent: list[dict[str, Any]] = []

    async def _dispatch(self, to: str, payload: dict[str, Any]) -> str:
        wamid = _mint_wamid()
        record = {"wamid": wamid, "to": payload["to"], "payload": payload}
        self.sent.append(record)
        # Deliver to the recipient phone's screen.
        await bus.publish(
            {
                "kind": "wa_message",
                "target": payload["to"],
                "wamid": wamid,
                "message": payload,
                "ts": time.time(),
            }
        )
        # Emit realistic delivery receipts (sent -> delivered).
        for status in ("sent", "delivered"):
            await bus.publish(
                {
                    "kind": "wa_status",
                    "target": payload["to"],
                    "wamid": wamid,
                    "status": status,
                    "ts": time.time(),
                }
            )
        log.info("mock send %s -> %s", payload.get("type"), payload["to"])
        return wamid

    async def send_text(self, to: str, body: str, preview_url: bool = False) -> str:
        return await self._dispatch(to, messages.text_message(to, body, preview_url))

    async def send_buttons(
        self, to, body, buttons, header=None, footer=None
    ) -> str:
        return await self._dispatch(
            to, messages.button_message(to, body, buttons, header, footer)
        )

    async def send_list(
        self, to, body, button_label, sections, header=None, footer=None
    ) -> str:
        return await self._dispatch(
            to, messages.list_message(to, body, button_label, sections, header, footer)
        )

    async def send_location(
        self, to, latitude, longitude, name=None, address=None
    ) -> str:
        return await self._dispatch(
            to, messages.location_message(to, latitude, longitude, name, address)
        )

    # ── Inbound simulation (the "device" typing to us) ──────────────────────
    @staticmethod
    def build_inbound_webhook(
        from_phone: str,
        *,
        text: str | None = None,
        reply_id: str | None = None,
        reply_title: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        profile_name: str = "Customer",
    ) -> dict[str, Any]:
        """Construct a Meta-shaped inbound webhook body (no leading '+')."""
        wa_id = from_phone.lstrip("+")
        msg: dict[str, Any] = {
            "from": wa_id,
            "id": _mint_wamid(),
            "timestamp": str(int(time.time())),
        }
        if reply_id is not None:
            msg["type"] = "interactive"
            msg["interactive"] = {
                "type": "button_reply",
                "button_reply": {"id": reply_id, "title": reply_title or reply_id},
            }
        elif latitude is not None and longitude is not None:
            msg["type"] = "location"
            msg["location"] = {"latitude": str(latitude), "longitude": str(longitude)}
        else:
            msg["type"] = "text"
            msg["text"] = {"body": text or ""}
        return {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": WABA_ID,
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": BUSINESS_DISPLAY,
                                    "phone_number_id": PHONE_NUMBER_ID,
                                },
                                "contacts": [
                                    {
                                        "profile": {"name": profile_name},
                                        "wa_id": wa_id,
                                    }
                                ],
                                "messages": [msg],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }

    @staticmethod
    def sign(raw_body: bytes) -> str:
        """Produce the X-Hub-Signature-256 the mock would attach (app secret)."""
        return make_meta_signature(settings.meta_app_secret, raw_body)
