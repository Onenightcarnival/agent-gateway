import asyncio
import socket
from pathlib import Path

import httpx
import uvicorn

from agent_gateway.app import create_app
from agent_gateway.config import Settings

from .conftest import create_session, prompt
from .scripted_engine import ScriptedEngine, plain_reply


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _serve(settings: Settings, engine: ScriptedEngine):
    app = create_app(settings, engine)
    port = _free_port()
    srv = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    task = asyncio.create_task(srv.serve())
    while not srv.started:
        await asyncio.sleep(0.01)
    return srv, task, f"http://127.0.0.1:{port}"


async def test_sessions_survive_restart_and_prime_engine(settings: Settings, tmp_path: Path):
    settings.db_path = tmp_path / "g.db"
    first = ScriptedEngine(script=plain_reply("first answer"))
    srv, task, base = await _serve(settings, first)
    async with httpx.AsyncClient(base_url=base, timeout=10, trust_env=False) as client:
        sid = await create_session(client, str(tmp_path / "w"), "kept")
        assert (await prompt(client, sid, "q1")).status_code == 204
    srv.should_exit = True
    await task

    second = ScriptedEngine(script=plain_reply("second answer"))
    srv, task, base = await _serve(settings, second)
    try:
        async with httpx.AsyncClient(base_url=base, timeout=10, trust_env=False) as client:
            listed = (await client.get("/session")).json()
            assert [s["id"] for s in listed] == [sid]
            assert listed[0]["title"] == "kept"
            assert (await client.get("/session/status")).json() == {sid: {"type": "idle"}}
            msgs = (await client.get(f"/session/{sid}/message")).json()
            assert [m["role"] for m in msgs] == ["user", "assistant"]
            assert msgs[1]["content"] == "first answer"
            assert (await prompt(client, sid, "q2")).status_code == 204
            msgs = (await client.get(f"/session/{sid}/message")).json()
            assert [m["content"] for m in msgs] == ["q1", "first answer", "q2", "second answer"]
    finally:
        srv.should_exit = True
        await task
    assert second.opened[0].id == sid
    assert [(h.role, h.content) for h in second.histories[0]] == [
        ("user", "q1"),
        ("assistant", "first answer"),
    ]
    assert first.histories[0] == []
