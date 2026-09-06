import asyncio
import sys

from .conftest import ask, assert_final, create_session


async def test_health(client, engine_name):
    body = (await client.get("/health")).json()
    assert body["engine"] == engine_name
    assert body["model"]
    assert "get_magic_number" in body["tools"]
    assert body["skills"] == ["echo-secret"]


async def test_plain_chat(client, workdir):
    sid = await create_session(client, workdir)
    msgs = await ask(client, sid, "只回复一个单词：PONG")
    assert [m["role"] for m in msgs][0] == "user"
    last = assert_final(msgs)
    assert "PONG" in last["content"].upper()
    assert (await client.get("/session/status")).json()[sid] == {"type": "idle"}


async def test_tool_call_writes_file_in_session_directory(client, workdir):
    sid = await create_session(client, workdir)
    msgs = await ask(
        client,
        sid,
        "在当前工作目录创建文件 note.txt，内容为 hello gateway。完成后回复 DONE。",
    )
    assert_final(msgs)
    assert (workdir / "note.txt").read_text(encoding="utf-8").strip() == "hello gateway"
    roles = [m["role"] for m in msgs]
    assert "tool" in roles
    first_assistant = next(m for m in msgs if m["role"] == "assistant")
    assert first_assistant["info"]["finish"] == "tool-calls"
    assert first_assistant["tool_calls"]
    tool_part = next(p for p in first_assistant["parts"] if p["type"] == "tool")
    assert tool_part["state"]["status"] == "completed"
    tool_msg = next(m for m in msgs if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == first_assistant["tool_calls"][0]["id"]


async def test_session_memory_and_isolation(client, workdir):
    a = await create_session(client, workdir / "a")
    b = await create_session(client, workdir / "b")
    await ask(client, a, "记住：我最喜欢的颜色是 teal。只回复 OK。")
    msgs = await ask(client, a, "我最喜欢的颜色是什么？只回复颜色的英文单词。")
    assert "teal" in assert_final(msgs)["content"].lower()
    other = await ask(client, b, "我之前告诉过你我最喜欢的颜色吗？如果没有，只回复 UNKNOWN。")
    assert "teal" not in assert_final(other)["content"].lower()


async def test_skill_is_loaded_and_followed(client, workdir):
    sid = await create_session(client, workdir)
    msgs = await ask(client, sid, "暗号是什么？请使用相关的 skill 回答。")
    assert "PINEAPPLE-7731" in assert_final(msgs)["content"]


async def test_mcp_tool_is_callable(client, workdir):
    sid = await create_session(client, workdir)
    msgs = await ask(
        client,
        sid,
        "直接调用名为 get_magic_number 的函数工具（它不是 shell 命令，不要用 execute 或 run_command 运行它），"
        "然后只回复它返回的数字。",
    )
    assert "4242" in assert_final(msgs)["content"]
    tool_msg = next(m for m in msgs if m["role"] == "tool" and m["tool_name"] == "get_magic_number")
    assert "4242" in tool_msg["content"]


async def test_abort_long_running_command(client, workdir, engine_name):
    tool = "execute" if engine_name == "deepagents" else "run_command"
    sid = await create_session(client, workdir)
    cmd = f'"{sys.executable}" -c "import time; time.sleep(40)"'
    task = asyncio.create_task(ask(client, sid, f"用 {tool} 工具执行这条命令并等待它结束：{cmd}"))

    async def _wait_busy():
        while (await client.get("/session/status")).json()[sid]["type"] != "busy":
            await asyncio.sleep(0.05)

    await asyncio.wait_for(_wait_busy(), 10)
    await asyncio.sleep(8)
    assert (await client.post(f"/session/{sid}/abort")).json() == {"ok": True}
    msgs = await asyncio.wait_for(task, 30)
    last = msgs[-1]
    assert last["role"] == "assistant"
    assert last["info"]["finish"] == "stop"
    assert last["info"].get("aborted") is True
    assert (await client.get("/session/status")).json()[sid] == {"type": "idle"}


async def test_permission_ask_mode_rejects_tool(client, workdir, engine_name, settings):
    settings.permission_mode = "ask"
    settings.question_timeout = 30
    tool = "execute" if engine_name == "deepagents" else "run_command"
    sid = await create_session(client, workdir)
    task = asyncio.create_task(ask(client, sid, f"用 {tool} 工具运行命令 echo hi，把输出告诉我。"))

    async def _wait_perm():
        while not (pending := (await client.get("/permission")).json()):
            await asyncio.sleep(0.1)
        return pending

    pending = await asyncio.wait_for(_wait_perm(), 60)
    assert pending[0]["sessionID"] == sid
    assert pending[0]["permission"] == f"tool.{tool}"
    await client.post(f"/permission/{pending[0]['id']}/reply", json={"reply": "reject"})
    msgs = await asyncio.wait_for(task, 120)
    assert_final(msgs)
    tool_msg = next(m for m in msgs if m["role"] == "tool")
    assert "denied" in tool_msg["content"].lower()
