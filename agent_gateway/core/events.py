"""SSE 事件总线：发布即扇出到每个订阅者队列。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

HEARTBEAT_SECONDS = 15.0


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def publish(self, type_: str, properties: dict[str, Any] | None = None) -> None:
        event = {"type": type_, "properties": properties or {}}
        for queue in list(self._subscribers):
            queue.put_nowait(event)

    async def subscribe(
        self, heartbeat: float = HEARTBEAT_SECONDS
    ) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            yield {"type": "server.connected", "properties": {}}
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=heartbeat)
                except TimeoutError:
                    yield {"type": "server.heartbeat", "properties": {}}
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
