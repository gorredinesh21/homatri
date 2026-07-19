"""In-process async pub/sub used to push realtime updates to the simulator UI.

Every outbound WhatsApp message (in mock mode) and every order-state change is
published here; the SSE endpoint fans events out to connected browsers. This is
the in-process equivalent of Redis pub/sub — the ``EventBus`` interface is what
the app depends on, so swapping to Redis later is contained to this file.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator

from app.core.logging import get_logger

log = get_logger("events")


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    async def publish(self, event: dict[str, Any]) -> None:
        dead = []
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover
                dead.append(q)
        for q in dead:
            self._subscribers.discard(q)

    async def subscribe(self) -> AsyncGenerator[dict[str, Any], None]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


bus = EventBus()
