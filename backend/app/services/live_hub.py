"""In-process pub/sub for order SSE streams and rider GPS fans."""

from __future__ import annotations

import asyncio
from typing import Any


class LiveHub:
    def __init__(self) -> None:
        self._order_subs: dict[str, list[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()
        self.last_rider: dict[str, dict[str, Any]] = {}

    async def subscribe_order(self, order_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        async with self._lock:
            self._order_subs.setdefault(order_id, []).append(queue)
        return queue

    async def unsubscribe_order(self, order_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            listeners = self._order_subs.get(order_id) or []
            if queue in listeners:
                listeners.remove(queue)
            if not listeners:
                self._order_subs.pop(order_id, None)

    async def publish_order(self, order_id: str, event: dict[str, Any]) -> None:
        async with self._lock:
            listeners = list(self._order_subs.get(order_id) or [])
        for queue in listeners:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                continue

    def remember_rider(self, driver_phone: str, payload: dict[str, Any]) -> None:
        self.last_rider[driver_phone] = payload


hub = LiveHub()
