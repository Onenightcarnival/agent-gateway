import json
from pathlib import Path

import pytest

from agent_gateway.config import DEFAULT_SYSTEM_PROMPT, GatewayFile, Settings

BASE_ENV = {"MODEL_BASE_URL": "http://h/v1/", "MODEL_API_KEY": "k"}


def test_from_env_defaults():
    s = Settings.from_env({**BASE_ENV, "AGENT_ENGINE": "deepagents"})
    assert s.engine == "deepagents"
    assert s.model_base_url == "http://h/v1"
    assert s.model_name is None
    assert s.host == "localhost"
    assert s.port == 6217
    assert s.turn_timeout == 900
    assert s.question_timeout == 120
    assert s.permission_mode == "auto"
    assert s.max_steps == 50
    assert s.config_path == Path("gateway.json")
    assert s.ask_user is False
    assert s.shell_sandbox is True
    assert s.model_extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


def test_cli_overrides_env():
    env = {
        **BASE_ENV,
        "AGENT_ENGINE": "deepagents",
        "GATEWAY_PORT": "7000",
        "GATEWAY_HOST": "0.0.0.0",
    }
    s = Settings.from_env(env, engine="openai-agents", port=8000, host="127.0.0.1")
    assert (s.engine, s.port, s.host) == ("openai-agents", 8000, "127.0.0.1")


def test_env_values_are_parsed():
    env = {
        **BASE_ENV,
        "AGENT_ENGINE": "x",
        "MODEL_NAME": "m",
        "GATEWAY_TURN_TIMEOUT": "12.5",
        "GATEWAY_QUESTION_TIMEOUT": "3",
        "GATEWAY_PERMISSION_MODE": "ask",
        "GATEWAY_MAX_STEPS": "7",
        "GATEWAY_CONFIG": "/etc/gw.json",
        "GATEWAY_ASK_USER": "true",
        "GATEWAY_SHELL_SANDBOX": "off",
        "MODEL_EXTRA_BODY": '{"top_k": 5}',
    }
    s = Settings.from_env(env)
    assert s.ask_user is True
    assert s.shell_sandbox is False
    assert s.model_extra_body == {"top_k": 5}
    assert s.model_name == "m"
    assert s.turn_timeout == 12.5
    assert s.question_timeout == 3
    assert s.permission_mode == "ask"
    assert s.max_steps == 7
    assert s.config_path == Path("/etc/gw.json")


def test_missing_engine_or_model_is_an_error():
    with pytest.raises(ValueError, match="engine"):
        Settings.from_env(BASE_ENV)
    with pytest.raises(ValueError, match="MODEL_BASE_URL"):
        Settings.from_env({"AGENT_ENGINE": "x"})


def test_gateway_file_missing_uses_defaults(tmp_path: Path):
    gf = GatewayFile.load(tmp_path / "none.json")
    assert gf.system_prompt is None
    assert gf.mcp_servers == {}
    assert gf.skill_dirs == []
    assert gf.permission_ask_tools == set()


def test_gateway_file_parses_and_resolves_paths(tmp_path: Path):
    (tmp_path / "prompt.md").write_text("PROMPT for {directory}", encoding="utf-8")
    (tmp_path / "gateway.json").write_text(
        json.dumps(
            {
                "system_prompt": "ignored",
                "system_prompt_file": "prompt.md",
                "mcpServers": {"a": {"command": "x"}},
                "skills": ["skills", "/abs/skills"],
                "permissions": {"ask": ["execute"]},
            }
        ),
        encoding="utf-8",
    )
    s = Settings(
        engine="e", model_base_url="u", model_api_key="k", config_path=tmp_path / "gateway.json"
    )
    gf = s.gateway_file
    assert gf.system_prompt == "PROMPT for {directory}"
    assert gf.mcp_servers == {"a": {"command": "x"}}
    assert gf.skill_dirs == [(tmp_path / "skills").resolve(), Path("/abs/skills").resolve()]
    assert gf.permission_ask_tools == {"execute"}
    assert s.system_prompt("D:/work") == "PROMPT for D:/work"


def test_default_system_prompt_mentions_directory_and_ask_rule(tmp_path: Path):
    s = Settings(engine="e", model_base_url="u", model_api_key="k", config_path=tmp_path / "x.json")
    assert "D:/work" in s.system_prompt("D:/work")
    assert "{directory}" in DEFAULT_SYSTEM_PROMPT
    assert "不向用户提问" in s.system_prompt("D:/work")
    assert "ask_user" not in s.system_prompt("D:/work")
    s.ask_user = True
    assert "ask_user" in s.system_prompt("D:/work")
    assert "不向用户提问" not in s.system_prompt("D:/work")


def test_extra_body_from_gateway_file_and_env_override(tmp_path: Path):
    (tmp_path / "gateway.json").write_text(
        json.dumps({"model": {"extra_body": {"chat_template_kwargs": {"enable_thinking": True}}}}),
        encoding="utf-8",
    )
    s = Settings(
        engine="e", model_base_url="u", model_api_key="k", config_path=tmp_path / "gateway.json"
    )
    assert s.model_extra_body == {"chat_template_kwargs": {"enable_thinking": True}}
    s.model_extra_body_override = {}
    assert s.model_extra_body == {}
    (tmp_path / "gateway.json").write_text(
        json.dumps({"model": {"extra_body": {}}}), encoding="utf-8"
    )
    s2 = Settings(
        engine="e", model_base_url="u", model_api_key="k", config_path=tmp_path / "gateway.json"
    )
    assert s2.model_extra_body == {}
