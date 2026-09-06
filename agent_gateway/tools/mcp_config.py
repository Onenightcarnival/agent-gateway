"""mcpServers 配置 → 各引擎的连接描述。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal

from .http import mcp_client_factory

Transport = Literal["stdio", "streamable_http", "sse"]


@dataclass(frozen=True)
class McpServerSpec:
    name: str
    transport: Transport
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    def full_env(self) -> dict[str, str]:
        return {**os.environ, **self.env}


def parse_mcp_servers(raw: dict[str, dict[str, Any]]) -> list[McpServerSpec]:
    specs: list[McpServerSpec] = []
    for name, cfg in raw.items():
        if cfg.get("disabled"):
            continue
        if cfg.get("command"):
            specs.append(
                McpServerSpec(
                    name=name,
                    transport="stdio",
                    command=cfg["command"],
                    args=list(cfg.get("args", [])),
                    env=dict(cfg.get("env", {})),
                    cwd=cfg.get("cwd"),
                )
            )
        elif cfg.get("url"):
            kind = cfg.get("type") or cfg.get("transport") or "streamable-http"
            transport: Transport = "sse" if kind == "sse" else "streamable_http"
            specs.append(
                McpServerSpec(
                    name=name,
                    transport=transport,
                    url=cfg["url"],
                    headers=dict(cfg.get("headers", {})),
                )
            )
        else:
            raise ValueError(f"mcpServers.{name}: need 'command' or 'url'")
    return specs


def to_langchain_connection(spec: McpServerSpec) -> dict[str, Any]:
    if spec.transport == "stdio":
        return {
            "transport": "stdio",
            "command": spec.command,
            "args": spec.args,
            "env": spec.full_env(),
            "cwd": spec.cwd,
        }
    return {
        "transport": spec.transport,
        "url": spec.url,
        "headers": spec.headers,
        "httpx_client_factory": mcp_client_factory,
    }


def to_openai_agents_server(spec: McpServerSpec):
    from agents.mcp import MCPServerSse, MCPServerStdio, MCPServerStreamableHttp

    common = {"name": spec.name, "cache_tools_list": True, "client_session_timeout_seconds": 120}
    if spec.transport == "stdio":
        return MCPServerStdio(
            params={
                "command": spec.command,
                "args": spec.args,
                "env": spec.full_env(),
                "cwd": spec.cwd,
            },
            **common,
        )
    params = {"url": spec.url, "headers": spec.headers, "httpx_client_factory": mcp_client_factory}
    if spec.transport == "sse":
        return MCPServerSse(params=params, **common)
    return MCPServerStreamableHttp(params=params, **common)


def normalize_tool_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """补齐 OpenAI 兼容代理稳定识别所需的字段：properties、required、additionalProperties。"""
    out = dict(schema)
    out.setdefault("type", "object")
    out["properties"] = dict(out.get("properties") or {})
    out.setdefault("required", [])
    out.setdefault("additionalProperties", False)
    return out
