import asyncio
import socket
from collections.abc import AsyncIterator

import httpx
import pytest
import uvicorn

from agent_gateway.app import create_app
from agent_gateway.config import Settings

from .scripted_engine import ScriptedEngine


class BrokenEngine(ScriptedEngine):
    async def start(self):
        raise RuntimeError("mcp server exited")


@pytest.fixture
async def broken_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, BrokenEngine())
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    task = asyncio.create_task(srv.serve())
    while not srv.started:
        await asyncio.sleep(0.01)
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False) as c:
        yield c
    srv.should_exit = True
    await task


async def test_engine_start_failure_yields_503(broken_client, workdir):
    r = await broken_client.get("/health")
    assert r.status_code == 503
    assert r.json() == {
        "code": "SERVICE_UNAVAILABLE",
        "message": "engine failed to start: mcp server exited",
    }
    r = await broken_client.post("/session", json={"directory": workdir})
    assert r.status_code == 503
    assert r.json()["code"] == "SERVICE_UNAVAILABLE"
    assert (await broken_client.get("/session/status")).json() == {}
    assert (await broken_client.get("/session")).json() == []
