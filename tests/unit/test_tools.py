import asyncio
import os
import sys
from pathlib import Path

import pytest

from agent_gateway.tools.http import mcp_client_factory
from agent_gateway.tools.local import LocalTools
from agent_gateway.tools.mcp_config import (
    parse_mcp_servers,
    to_langchain_connection,
    to_openai_agents_server,
)
from agent_gateway.tools.skills import discover_skills, skills_prompt

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


# ---- skills ----


def test_discover_skills_reads_frontmatter():
    skills = discover_skills([FIXTURES / "skills", FIXTURES / "missing"])
    assert [s.name for s in skills] == ["echo-secret"]
    assert "暗号" in skills[0].description
    assert skills[0].file == (FIXTURES / "skills" / "echo-secret" / "SKILL.md").resolve()


def test_discover_skills_falls_back_to_dir_name_and_later_roots_override(tmp_path: Path):
    for root, body in (("r1", "---\ndescription: one\n---\n"), ("r2", "no frontmatter")):
        d = tmp_path / root / "alpha"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(body, encoding="utf-8")
    skills = discover_skills([tmp_path / "r1", tmp_path / "r2"])
    assert len(skills) == 1
    assert skills[0].name == "alpha"
    assert skills[0].description == ""
    assert skills[0].directory == (tmp_path / "r2" / "alpha").resolve()


def test_skills_prompt_lists_paths():
    skills = discover_skills([FIXTURES / "skills"])
    text = skills_prompt(skills)
    assert "echo-secret" in text
    assert str(skills[0].file) in text
    assert skills_prompt([]) == ""


# ---- mcp config ----


def test_parse_mcp_servers_stdio_and_http():
    specs = parse_mcp_servers(
        {
            "local": {"command": "uv", "args": ["run", "srv"], "env": {"A": "1"}, "cwd": "/w"},
            "remote": {"url": "http://h/mcp", "headers": {"X": "y"}},
            "events": {"url": "http://h/sse", "type": "sse"},
            "off": {"command": "x", "disabled": True},
        }
    )
    assert [s.name for s in specs] == ["local", "remote", "events"]
    local, remote, events = specs
    assert local.transport == "stdio"
    assert local.full_env()["A"] == "1"
    assert "PATH" in local.full_env()
    assert remote.transport == "streamable_http"
    assert events.transport == "sse"

    lc = to_langchain_connection(local)
    assert lc["transport"] == "stdio"
    assert lc["command"] == "uv"
    assert lc["args"] == ["run", "srv"]
    assert lc["cwd"] == "/w"
    remote_conn = to_langchain_connection(remote)
    assert remote_conn["transport"] == "streamable_http"
    assert remote_conn["url"] == "http://h/mcp"
    assert remote_conn["headers"] == {"X": "y"}
    assert remote_conn["httpx_client_factory"] is mcp_client_factory
    oa_remote = to_openai_agents_server(remote)
    assert oa_remote.params["httpx_client_factory"] is mcp_client_factory
    assert type(to_openai_agents_server(local)).__name__ == "MCPServerStdio"
    assert type(to_openai_agents_server(remote)).__name__ == "MCPServerStreamableHttp"
    assert type(to_openai_agents_server(events)).__name__ == "MCPServerSse"


def test_parse_mcp_servers_rejects_incomplete_entry():
    with pytest.raises(ValueError, match="mcpServers.bad"):
        parse_mcp_servers({"bad": {"args": []}})


# ---- local tools ----


async def test_local_file_tools_are_rooted_at_session_directory(tmp_path: Path):
    tools = LocalTools(str(tmp_path))
    assert (await tools.write_file("sub/a.txt", "hello")).endswith(str(tmp_path / "sub" / "a.txt"))
    assert await tools.read_file("sub/a.txt") == "hello"
    assert await tools.read_file(str(tmp_path / "sub" / "a.txt")) == "hello"
    assert await tools.list_directory(".") == "sub/"
    assert await tools.list_directory("sub") == "a.txt"


async def test_run_command_runs_in_session_directory(tmp_path: Path):
    tools = LocalTools(str(tmp_path))
    out = await tools.run_command(f'"{sys.executable}" -c "import os; print(os.getcwd())"')
    assert str(tmp_path.resolve()) in out
    assert "[exit code 0]" in out
    assert "[exit code 3]" in await tools.run_command(
        f'"{sys.executable}" -c "raise SystemExit(3)"'
    )


async def test_run_command_timeout_and_cancel_kill_the_process(tmp_path: Path):
    tools = LocalTools(str(tmp_path))
    sleep = f'"{sys.executable}" -c "import time; time.sleep(30)"'
    assert "[timeout after 1s]" in await tools.run_command(sleep, timeout_seconds=1)

    task = asyncio.create_task(tools.run_command(sleep))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_windows_env_flags_are_set_for_children():
    assert os.environ.get("PYTHONUTF8") in (None, "1")


# ---- ask_user ----


def test_ask_user_langchain_schema_has_no_anyof_or_null():
    import json

    from langchain_core.utils.function_calling import convert_to_openai_tool

    from agent_gateway.tools.ask_user import make_ask_user_langchain_tool

    class Port:
        async def ask_question(self, *a):
            return [[]]

        async def ask_permission(self, *a):
            return "always"

    spec = convert_to_openai_tool(make_ask_user_langchain_tool("s", Port()))
    text = json.dumps(spec)
    assert "anyOf" not in text
    assert "null" not in text
    params = spec["function"]["parameters"]
    assert params["required"] == ["question"]
    assert params["properties"]["options"] == {
        "description": "可选项；为空表示自由回答",
        "items": {"type": "string"},
        "type": "array",
    }
