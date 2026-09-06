import asyncio

import httpx

from agent_gateway.engines.base import StepFinish, TextDelta, ToolCallEnd, ToolCallStart

from .conftest import SseCollector, create_session, prompt
from .scripted_engine import Raise, ScriptedEngine, Sleep, plain_reply


async def test_plain_reply_message_sequence(client, workdir, engine: ScriptedEngine):
    engine.script = [TextDelta(text="你好"), TextDelta(text="，世界"), StepFinish(finish="stop")]
    sid = await create_session(client, workdir)
    r = await prompt(client, sid, "hi")
    assert r.status_code == 204
    assert engine.prompts == [(sid, "hi")]

    msgs = (await client.get(f"/session/{sid}/message")).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "hi"
    a = msgs[1]
    assert a["content"] == "你好，世界"
    assert a["info"] == {"role": "assistant", "finish": "stop"}
    assert a["parts"] == [{"type": "text", "content": "你好，世界"}, {"type": "step-finish"}]
    assert (await client.get(f"/session/{sid}")).json()["message_count"] == 2


async def test_tool_call_round_trip(client, workdir, engine: ScriptedEngine):
    engine.script = [
        TextDelta(text="我来查一下"),
        ToolCallStart(call_id="call_1", name="search", arguments={"query": "天气"}),
        StepFinish(finish="tool-calls"),
        ToolCallEnd(call_id="call_1", name="search", output="晴"),
        TextDelta(text="今天晴"),
        StepFinish(finish="stop"),
    ]
    sid = await create_session(client, workdir)
    assert (await prompt(client, sid, "天气")).status_code == 204

    msgs = (await client.get(f"/session/{sid}/message")).json()
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "assistant"]
    step1 = msgs[1]
    assert step1["info"]["finish"] == "tool-calls"
    assert step1["tool_calls"] == [
        {"id": "call_1", "name": "search", "arguments": {"query": "天气"}}
    ]
    assert [p["type"] for p in step1["parts"]] == ["text", "tool", "step-finish"]
    tool_part = step1["parts"][1]
    assert tool_part["tool"] == "search"
    assert tool_part["state"]["status"] == "completed"
    assert tool_part["state"]["title"] == "search 完成"
    assert msgs[2] == {
        "id": msgs[2]["id"],
        "role": "tool",
        "tool_call_id": "call_1",
        "tool_name": "search",
        "content": "晴",
        "created_at": msgs[2]["created_at"],
    }
    final = msgs[3]
    assert final["info"]["finish"] == "stop"
    assert final["content"] == "今天晴"
    assert final["parts"][-1] == {"type": "step-finish"}


async def test_sse_events_for_a_turn(client, workdir, engine: ScriptedEngine):
    engine.script = [
        ToolCallStart(call_id="c", name="search", arguments={}),
        StepFinish(finish="tool-calls"),
        ToolCallEnd(call_id="c", name="search", output="ok"),
        TextDelta(text="done"),
        StepFinish(finish="stop"),
    ]
    sid = await create_session(client, workdir)
    async with SseCollector(client) as sse:
        await prompt(client, sid, "go")
        await sse.wait_for("session.idle", sid)
    types = sse.types(sid)
    assert types[0] == "session.status"
    assert sse.events[[e["type"] for e in sse.events].index("session.status")]["properties"][
        "status"
    ] == {"type": "busy"}
    assert types[-2:] == ["session.status", "session.idle"]
    parts = [e["properties"]["part"] for e in sse.events if e["type"] == "message.part.updated"]
    assert [p["type"] for p in parts] == ["tool", "step-finish", "tool", "text", "step-finish"]
    assert parts[0]["state"]["status"] == "running"
    assert parts[0]["state"]["title"] == "正在执行 search"
    assert parts[2]["state"]["status"] == "completed"
    assert parts[3]["content"] == "done"
    assert all(
        "messageID" in e["properties"] for e in sse.events if e["type"] == "message.part.updated"
    )


async def test_sse_connected_and_heartbeat_headers(client):
    async with client.stream("GET", "/event", timeout=None) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["cache-control"] == "no-cache"
        assert r.headers["x-accel-buffering"] == "no"
        async for line in r.aiter_lines():
            if line.startswith("data:"):
                assert '"server.connected"' in line
                break


async def test_busy_session_queues_second_prompt(client, workdir, engine: ScriptedEngine):
    engine.script = [Sleep(0.4), *plain_reply("reply")]
    sid = await create_session(client, workdir)
    first = asyncio.create_task(prompt(client, sid, "one"))
    await asyncio.sleep(0.1)
    assert (await client.get("/session/status")).json()[sid] == {"type": "busy"}
    second = asyncio.create_task(prompt(client, sid, "two"))
    await asyncio.sleep(0.1)
    assert engine.prompts == [(sid, "one")]
    assert (await first).status_code == 204
    assert (await second).status_code == 204
    assert engine.prompts == [(sid, "one"), (sid, "two")]
    msgs = (await client.get(f"/session/{sid}/message")).json()
    assert [m["content"] for m in msgs] == ["one", "reply", "two", "reply"]
    assert (await client.get("/session/status")).json()[sid] == {"type": "idle"}


async def test_empty_parts_is_400(client, workdir):
    sid = await create_session(client, workdir)
    r = await client.post(f"/session/{sid}/prompt_async", json={"parts": [{"type": "image"}]})
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"


async def test_model_from_request_when_env_unset(client, workdir, engine: ScriptedEngine, settings):
    settings.model_name = None
    engine.script = plain_reply("x")
    sid = await create_session(client, workdir)
    await client.post(
        f"/session/{sid}/prompt_async",
        json={
            "parts": [{"type": "text", "text": "hi"}],
            "model": {"providerID": "p", "modelID": "m-1"},
            "agent": "assistant",
        },
    )
    assert engine.models[-1].provider_id == "p"
    assert engine.models[-1].model_id == "m-1"


async def test_engine_error_finalizes_turn(client, workdir, engine: ScriptedEngine):
    engine.script = [TextDelta(text="开始"), Raise(RuntimeError("upstream exploded"))]
    sid = await create_session(client, workdir)
    async with SseCollector(client) as sse:
        r = await prompt(client, sid, "go")
        assert r.status_code == 502
        assert r.json() == {"code": "BAD_GATEWAY", "message": "upstream exploded"}
        err = await sse.wait_for("session.error", sid)
        await sse.wait_for("session.idle", sid)
    assert err["properties"]["error"]["message"] == "upstream exploded"
    msgs = (await client.get(f"/session/{sid}/message")).json()
    last = msgs[-1]
    assert last["role"] == "assistant"
    assert last["info"]["finish"] == "stop"
    assert last["info"]["error"] == "upstream exploded"
    assert last["parts"][-1] == {"type": "step-finish"}
    assert "upstream exploded" in last["content"]
    assert (await client.get("/session/status")).json()[sid] == {"type": "idle"}


async def test_abort_cancels_engine_and_finalizes(client, workdir, engine: ScriptedEngine):
    engine.script = [
        ToolCallStart(call_id="c", name="slow", arguments={}),
        StepFinish(finish="tool-calls"),
        Sleep(5),
        ToolCallEnd(call_id="c", name="slow", output="never"),
        StepFinish(finish="stop"),
    ]
    sid = await create_session(client, workdir)
    task = asyncio.create_task(prompt(client, sid, "go"))
    await asyncio.sleep(0.2)
    r = await client.post(f"/session/{sid}/abort")
    assert r.json() == {"ok": True}
    assert (await task).status_code == 204
    assert engine.cancelled == 1
    msgs = (await client.get(f"/session/{sid}/message")).json()
    last = msgs[-1]
    assert last["role"] == "assistant"
    assert last["info"]["finish"] == "stop"
    assert last["info"]["aborted"] is True
    assert last["parts"][-1] == {"type": "step-finish"}
    tool_part = next(p for m in msgs for p in m.get("parts", []) if p["type"] == "tool")
    assert tool_part["state"]["status"] == "error"
    assert (await client.get("/session/status")).json()[sid] == {"type": "idle"}


async def test_stop_alias_and_abort_on_idle(client, workdir):
    sid = await create_session(client, workdir)
    assert (await client.post(f"/session/{sid}/stop")).json() == {"ok": True}
    assert (await client.post(f"/session/{sid}/abort")).json() == {"ok": True}


async def test_turn_timeout_aborts(client, workdir, engine: ScriptedEngine, settings):
    settings.turn_timeout = 0.3
    engine.script = [TextDelta(text="working"), Sleep(5), StepFinish(finish="stop")]
    sid = await create_session(client, workdir)
    r = await prompt(client, sid, "go")
    assert r.status_code == 204
    last = (await client.get(f"/session/{sid}/message")).json()[-1]
    assert last["info"]["aborted"] is True
    assert last["info"]["aborted_reason"] == "timeout"
    assert last["info"]["finish"] == "stop"
    assert engine.cancelled == 1


async def test_client_disconnect_does_not_cancel_turn(
    client, server, workdir, engine: ScriptedEngine
):
    engine.script = [Sleep(0.6), *plain_reply("finished anyway")]
    sid = await create_session(client, workdir)
    async with httpx.AsyncClient(base_url=server, timeout=0.2, trust_env=False) as short:
        try:
            await prompt(short, sid, "go")
        except httpx.ReadTimeout:
            pass
    await asyncio.sleep(0.8)
    msgs = (await client.get(f"/session/{sid}/message")).json()
    assert msgs[-1]["content"] == "finished anyway"
    assert engine.cancelled == 0
    assert (await client.get("/session/status")).json()[sid] == {"type": "idle"}


async def test_delete_busy_session_aborts_first(client, workdir, engine: ScriptedEngine):
    engine.script = [Sleep(5), *plain_reply("x")]
    sid = await create_session(client, workdir)
    task = asyncio.create_task(prompt(client, sid, "go"))
    await asyncio.sleep(0.1)
    assert (await client.delete(f"/session/{sid}")).json() == {"ok": True}
    await task
    assert engine.cancelled == 1


async def test_missing_final_step_finish_is_completed_by_gateway(
    client, workdir, engine: ScriptedEngine
):
    engine.script = [TextDelta(text="no finish event")]
    sid = await create_session(client, workdir)
    assert (await prompt(client, sid, "go")).status_code == 204
    last = (await client.get(f"/session/{sid}/message")).json()[-1]
    assert last["info"]["finish"] == "stop"
    assert last["parts"] == [
        {"type": "text", "content": "no finish event"},
        {"type": "step-finish"},
    ]


async def test_sessions_are_isolated(client, workdir, engine: ScriptedEngine):
    engine.script = plain_reply("r")
    a = await create_session(client, workdir + "/a")
    b = await create_session(client, workdir + "/b")
    await prompt(client, a, "only a")
    assert len((await client.get(f"/session/{a}/message")).json()) == 2
    assert (await client.get(f"/session/{b}/message")).json() == []


async def test_unresolvable_model_is_400(client, workdir, engine: ScriptedEngine, settings):
    settings.model_name = None
    sid = await create_session(client, workdir)
    r = await prompt(client, sid, "hi")
    assert r.status_code == 400
    assert r.json() == {"code": "VALIDATION_ERROR", "message": "model.modelID is required"}
    assert engine.prompts == []
    assert (await client.get(f"/session/{sid}/message")).json() == []
