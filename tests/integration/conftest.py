from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from dotenv import load_dotenv

from agent_gateway.app import create_app
from agent_gateway.config import Settings
from agent_gateway.engines.registry import ENGINES, create_engine

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"

load_dotenv(ROOT / ".env")

pytestmark = pytest.mark.integration


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(params=sorted(ENGINES), ids=sorted(ENGINES))
def engine_name(request) -> str:
    return request.param


@pytest.fixture
def gateway_config(tmp_path: Path) -> Path:
    path = tmp_path / "gateway.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "test": {
                        "command": sys.executable,
                        "args": [str(FIXTURES / "mcp_server.py")],
                    }
                },
                "skills": [str(FIXTURES / "skills")],
                "permissions": {"ask": ["execute", "run_command"]},
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def settings(engine_name: str, gateway_config: Path) -> Settings:
    if not os.environ.get("MODEL_BASE_URL"):
        pytest.skip("MODEL_BASE_URL not configured")
    s = Settings.from_env(engine=engine_name)
    s.config_path = gateway_config
    s.turn_timeout = 180
    s.db_path = None
    s.question_timeout = 1
    return s


@pytest.fixture
async def server(settings: Settings) -> AsyncIterator[str]:
    app = create_app(settings, create_engine(settings))
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    srv = uvicorn.Server(config)
    task = asyncio.create_task(srv.serve())
    while not srv.started:
        if task.done():
            task.result()
        await asyncio.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    await task


@pytest.fixture
async def client(server: str) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=server, timeout=200, trust_env=False) as c:
        yield c


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    return tmp_path / "work"


async def create_session(client: httpx.AsyncClient, directory: Path) -> str:
    r = await client.post("/session", json={"directory": str(directory)})
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def ask(client: httpx.AsyncClient, session_id: str, text: str) -> list[dict]:
    r = await client.post(
        f"/session/{session_id}/prompt_async",
        json={"parts": [{"type": "text", "text": text}]},
    )
    assert r.status_code == 204, r.text
    return (await client.get(f"/session/{session_id}/message")).json()


def assert_final(messages: list[dict]) -> dict:
    last = messages[-1]
    assert last["role"] == "assistant"
    assert last["info"]["finish"] == "stop"
    assert last["parts"][-1]["type"] == "step-finish"
    assert "error" not in last["info"]
    return last
