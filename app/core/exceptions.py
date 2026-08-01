"""Homaatri core exceptions and HITL interrupt signals."""

from typing import Any


class LocationInterrupt(Exception):
    """Signal raised inside registration function to pause thread and request WhatsApp Location Pin."""

    def __init__(self, message: str, payload: dict[str, Any]):
        super().__init__(message)
        self.message = message
        self.payload = payload
