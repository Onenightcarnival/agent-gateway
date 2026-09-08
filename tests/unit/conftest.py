from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import uvicorn

from agent_gateway.app import create_app
from agent_gateway.config import Settings
from tests.sse import SseCollector

from .scripted_engine import ScriptedEngine

__all__ = ["SseCollector"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        engine="scripted",
        model_base_url="http://127.0.0.1:1/v1",
        model_api_key="x",
        model_name="scripted-model",
        turn_timeout=5.0,
        question_timeout=0.5,
        permission_mode="ask",
        config_path=tmp_path / "gateway.json",
        db_path=None,
        ask_user=True,
        shell_sandbox=True,
    )


@pytest.fixture
def engine() -> ScriptedEngine:
    return ScriptedEngine()


@pytest.fixture
async def server(settings: Settings, engine: ScriptedEngine) -> AsyncIterator[str]:
    app = create_app(settings, engine)
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    srv = uvicorn.Server(config)
    task = asyncio.create_task(srv.serve())
    while not srv.started:
        await asyncio.sleep(0.01)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    await task


@pytest.fixture
async def client(server: str) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=server, timeout=10, trust_env=False) as c:
        yield c


@pytest.fixture
def workdir(tmp_path: Path) -> str:
    return str(tmp_path / "work")


async def create_session(
    client: httpx.AsyncClient, directory: str, title: str | None = None
) -> str:
    body: dict = {"directory": directory}
    if title:
        body["title"] = title
    r = await client.post("/session", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def prompt(client: httpx.AsyncClient, session_id: str, text: str) -> httpx.Response:
    return await client.post(
        f"/session/{session_id}/prompt_async",
        json={"parts": [{"type": "text", "text": text}]},
    )
