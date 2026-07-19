"""Real Meta WhatsApp Cloud API provider.

Production transport. Activated with ``WHATSAPP_PROVIDER=meta`` plus
``META_ACCESS_TOKEN`` / ``META_PHONE_NUMBER_ID``. Same interface as the mock,
so no business logic changes when switching. Posts to
``graph.facebook.com/<version>/<phone_number_id>/messages``.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.whatsapp import messages
from app.whatsapp.base import WhatsAppProvider

log = get_logger("wa.meta")


class MetaCloudProvider(WhatsAppProvider):
    name = "meta"

    def __init__(self) -> None:
        if not (settings.meta_access_token and settings.meta_phone_number_id):
            raise RuntimeError(
                "WHATSAPP_PROVIDER=meta requires META_ACCESS_TOKEN and "
                "META_PHONE_NUMBER_ID"
            )
        self._base = (
            f"https://graph.facebook.com/{settings.meta_graph_version}/"
            f"{settings.meta_phone_number_id}/messages"
        )
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, payload: dict[str, Any]) -> str:
        resp = await self._client.post(
            self._base,
            headers={
                "Authorization": f"Bearer {settings.meta_access_token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        wamid = data.get("messages", [{}])[0].get("id", "")
        log.info("meta send %s -> %s [%s]", payload.get("type"), payload["to"], wamid)
        return wamid

    async def send_text(self, to: str, body: str, preview_url: bool = False) -> str:
        return await self._post(messages.text_message(to, body, preview_url))

    async def send_buttons(self, to, body, buttons, header=None, footer=None) -> str:
        return await self._post(
            messages.button_message(to, body, buttons, header, footer)
        )

    async def send_list(
        self, to, body, button_label, sections, header=None, footer=None
    ) -> str:
        return await self._post(
            messages.list_message(to, body, button_label, sections, header, footer)
        )

    async def send_location(self, to, latitude, longitude, name=None, address=None) -> str:
        return await self._post(
            messages.location_message(to, latitude, longitude, name, address)
        )
