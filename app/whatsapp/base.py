"""Abstract WhatsApp provider + normalized inbound message type.

Application code depends only on this interface. ``MockWhatsAppProvider`` and
``MetaCloudProvider`` implement it identically, so flipping ``WHATSAPP_PROVIDER``
swaps the transport with zero changes to business logic.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any


@dataclass
class InboundMessage:
    """Normalized view of one inbound WhatsApp message."""

    from_phone: str          # E.164 with leading '+'
    wamid: str
    type: str                # "text" | "interactive" | "location" | "unknown"
    text: str | None = None
    reply_id: str | None = None      # button_reply.id / list_reply.id
    reply_title: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    timestamp: str | None = None
    profile_name: str | None = None


class WhatsAppProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    async def send_text(self, to: str, body: str, preview_url: bool = False) -> str:
        ...

    @abc.abstractmethod
    async def send_buttons(
        self,
        to: str,
        body: str,
        buttons: list[tuple[str, str]],
        header: str | None = None,
        footer: str | None = None,
    ) -> str:
        ...

    @abc.abstractmethod
    async def send_list(
        self,
        to: str,
        body: str,
        button_label: str,
        sections: list[dict[str, Any]],
        header: str | None = None,
        footer: str | None = None,
    ) -> str:
        ...

    @abc.abstractmethod
    async def send_location(
        self,
        to: str,
        latitude: float | str,
        longitude: float | str,
        name: str | None = None,
        address: str | None = None,
    ) -> str:
        ...
