"""进程级冒烟：用环境变量选择引擎启动真实网关。"""

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from agent_gateway.engines.registry import ENGINES

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("engine_name", sorted(ENGINES))
@pytest.mark.parametrize("via", ["flag", "env"])
async def test_gateway_starts_with_selected_engine(engine_name: str, via: str, tmp_path: Path):
    if not os.environ.get("MODEL_BASE_URL"):
        pytest.skip("MODEL_BASE_URL not configured")
    port = _free_port()
    env = {
        **{k: v for k, v in os.environ.items() if k != "AGENT_ENGINE"},
        "GATEWAY_CONFIG": str(tmp_path / "absent.json"),
        "GATEWAY_DB": str(tmp_path / "gateway.db"),
        "PYTHONUTF8": "1",
    }
    other = next(n for n in sorted(ENGINES) if n != engine_name)
    if via == "flag":
        env["AGENT_ENGINE"] = other
        env["GATEWAY_PORT"] = str(_free_port())
        argv = ["--engine", engine_name, "--port", str(port), "--host", "127.0.0.1"]
    else:
        env["AGENT_ENGINE"] = engine_name
        env["GATEWAY_PORT"] = str(port)
        argv = []
    proc = subprocess.Popen(
        [sys.executable, "-m", "agent_gateway", *argv],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", timeout=60, trust_env=False
        ) as client:
            deadline = time.monotonic() + 60
            while True:
                try:
                    health = (await client.get("/health")).json()
                    break
                except httpx.HTTPError:
                    assert proc.poll() is None, proc.stdout.read()
                    assert time.monotonic() < deadline, "gateway did not start"
                    await asyncio.sleep(0.2)
            assert health["engine"] == engine_name
            sid = (await client.post("/session", json={"directory": str(tmp_path / "w")})).json()[
                "id"
            ]
            r = await client.post(
                f"/session/{sid}/prompt_async",
                json={"parts": [{"type": "text", "text": "只回复：READY"}]},
            )
            assert r.status_code == 204
            msgs = (await client.get(f"/session/{sid}/message")).json()
            assert "READY" in msgs[-1]["content"].upper()
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_cli_rejects_unknown_engine():
    r = subprocess.run(
        [sys.executable, "-m", "agent_gateway", "--engine", "nope"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 2
    assert "invalid choice" in r.stderr


def test_cli_requires_engine():
    env = {k: v for k, v in os.environ.items() if k != "AGENT_ENGINE"}
    env.update({"MODEL_BASE_URL": "http://x/v1", "MODEL_API_KEY": "k"})
    r = subprocess.run(
        [sys.executable, "-m", "agent_gateway"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode == 2
    assert "engine is required" in r.stderr
