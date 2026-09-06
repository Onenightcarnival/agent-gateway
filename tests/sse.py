"""后台收集 /event 事件的测试辅助。"""

from __future__ import annotations

import asyncio
import json

import httpx


class SseCollector:
    """后台收集 /event 事件。"""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.events: list[dict] = []
        self._task: asyncio.Task | None = None
        self._connected = asyncio.Event()

    async def _run(self) -> None:
        async with self.client.stream("GET", "/event", timeout=None) as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            async for line in r.aiter_lines():
                if line.startswith("data:"):
                    ev = json.loads(line[5:].strip())
                    self.events.append(ev)
                    if ev["type"] == "server.connected":
                        self._connected.set()

    async def __aenter__(self) -> SseCollector:
        self._task = asyncio.create_task(self._run())
        await asyncio.wait_for(self._connected.wait(), 5)
        return self

    async def __aexit__(self, *exc) -> None:
        assert self._task
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, httpx.HTTPError):
            pass

    def types(self, session_id: str | None = None) -> list[str]:
        return [
            e["type"]
            for e in self.events
            if session_id is None or e.get("properties", {}).get("sessionID") == session_id
        ]

    async def wait_for(self, type_: str, session_id: str | None = None, timeout: float = 5) -> dict:
        async def _find():
            while True:
                for e in self.events:
                    if e["type"] == type_ and (
                        session_id is None or e["properties"].get("sessionID") == session_id
                    ):
                        return e
                await asyncio.sleep(0.02)

        return await asyncio.wait_for(_find(), timeout)
