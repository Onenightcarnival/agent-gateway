"""启动配置：命令行 > 环境变量 > .env > 默认值。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

PermissionMode = Literal["auto", "ask"]

NO_ASK_RULE = "自主完成任务，不向用户提问，不请求确认；信息不足时按最合理的假设执行。"
ASK_RULE = (
    "自主完成任务，不请求确认；仅在缺少关键信息且无法合理假设时，"
    "用 ask_user 工具向用户提问；用户明确要求提问时必须调用它。"
)

DEFAULT_EXTRA_BODY: dict[str, Any] = {"chat_template_kwargs": {"enable_thinking": False}}

DEFAULT_SYSTEM_PROMPT = """你是一个运行在用户电脑上的办公自动化助手。

工作方式：
- {interaction_rule}
- 优先使用可用的工具和 skill 完成实际操作，而不是只给出建议。
- 每一步操作后用工具核实结果（进程是否启动、文件是否生成、内容是否正确）。
- 任务完成后用中文简短汇报：做了什么、结果如何、未完成的部分及原因。
- 当前工作目录：{directory}。文件读写默认发生在该目录。
- 写入权限限定在工作目录内：只能在该目录及其子目录创建、修改、删除文件；其他位置只读。
"""


@dataclass
class GatewayFile:
    """gateway.json 的解析结果。"""

    base_dir: Path
    system_prompt: str | None = None
    model_extra_body: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_EXTRA_BODY))
    mcp_servers: dict[str, dict[str, Any]] = field(default_factory=dict)
    skill_dirs: list[Path] = field(default_factory=list)
    permission_ask_tools: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: Path) -> GatewayFile:
        base = path.resolve().parent
        if not path.is_file():
            return cls(base_dir=base)
        raw = json.loads(path.read_text(encoding="utf-8"))
        prompt: str | None = raw.get("system_prompt")
        prompt_file = raw.get("system_prompt_file")
        if prompt_file:
            prompt = (base / prompt_file).read_text(encoding="utf-8")
        model = raw.get("model", {})
        return cls(
            base_dir=base,
            system_prompt=prompt,
            model_extra_body=dict(model.get("extra_body", DEFAULT_EXTRA_BODY)),
            mcp_servers=dict(raw.get("mcpServers", {})),
            skill_dirs=[(base / d).resolve() for d in raw.get("skills", [])],
            permission_ask_tools=set(raw.get("permissions", {}).get("ask", [])),
        )


def _db_path(value: str) -> Path | None:
    return None if value.strip() in ("", ":memory:") else Path(value)


@dataclass
class Settings:
    engine: str
    model_base_url: str
    model_api_key: str
    model_name: str | None = None
    host: str = "localhost"
    port: int = 6217
    config_path: Path = Path("gateway.json")
    db_path: Path | None = Path("gateway.db")
    turn_timeout: float = 900.0
    question_timeout: float = 120.0
    permission_mode: PermissionMode = "auto"
    ask_user: bool = False
    shell_sandbox: bool = True
    max_steps: int = 50
    log_level: str = "INFO"
    model_extra_body_override: dict[str, Any] | None = None
    _gateway_file: GatewayFile | None = field(default=None, repr=False)

    @property
    def gateway_file(self) -> GatewayFile:
        if self._gateway_file is None:
            self._gateway_file = GatewayFile.load(self.config_path)
        return self._gateway_file

    @property
    def model_extra_body(self) -> dict[str, Any]:
        if self.model_extra_body_override is not None:
            return self.model_extra_body_override
        return self.gateway_file.model_extra_body

    def system_prompt(self, directory: str) -> str:
        template = self.gateway_file.system_prompt or DEFAULT_SYSTEM_PROMPT
        rule = ASK_RULE if self.ask_user else NO_ASK_RULE
        return template.replace("{directory}", directory).replace("{interaction_rule}", rule)

    @classmethod
    def from_env(
        cls,
        env: dict[str, str] | None = None,
        *,
        engine: str | None = None,
        host: str | None = None,
        port: int | None = None,
    ) -> Settings:
        e = os.environ if env is None else env
        chosen_engine = engine or e.get("AGENT_ENGINE")
        if not chosen_engine:
            raise ValueError("engine is required: pass --engine or set AGENT_ENGINE")
        base_url = e.get("MODEL_BASE_URL")
        api_key = e.get("MODEL_API_KEY")
        if not base_url or not api_key:
            raise ValueError("MODEL_BASE_URL and MODEL_API_KEY are required")
        return cls(
            engine=chosen_engine,
            model_base_url=base_url.rstrip("/"),
            model_api_key=api_key,
            model_name=e.get("MODEL_NAME") or None,
            host=host or e.get("GATEWAY_HOST", "localhost"),
            port=port or int(e.get("GATEWAY_PORT", "6217")),
            config_path=Path(e.get("GATEWAY_CONFIG", "gateway.json")),
            db_path=_db_path(e.get("GATEWAY_DB", "gateway.db")),
            turn_timeout=float(e.get("GATEWAY_TURN_TIMEOUT", "900")),
            question_timeout=float(e.get("GATEWAY_QUESTION_TIMEOUT", "120")),
            permission_mode="ask" if e.get("GATEWAY_PERMISSION_MODE") == "ask" else "auto",
            ask_user=e.get("GATEWAY_ASK_USER", "").strip().lower() in ("1", "true", "yes", "on"),
            shell_sandbox=e.get("GATEWAY_SHELL_SANDBOX", "on").strip().lower()
            not in ("0", "false", "no", "off"),
            model_extra_body_override=(
                json.loads(e["MODEL_EXTRA_BODY"]) if e.get("MODEL_EXTRA_BODY") else None
            ),
            max_steps=int(e.get("GATEWAY_MAX_STEPS", "50")),
            log_level=e.get("GATEWAY_LOG_LEVEL", "INFO"),
        )
