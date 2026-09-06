# 配置

## 启动

```
uv run python -m agent_gateway --engine <deepagents|openai-agents> [--port 6217] [--host localhost]
```

项目不安装为包（`[tool.uv] package = false`），入口固定为模块方式。

## 启动参数与环境变量

| 参数 | 环境变量 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--engine` | `AGENT_ENGINE` | 无，必填 | `deepagents` / `openai-agents` |
| `--port` | `GATEWAY_PORT` | `6217` | |
| `--host` | `GATEWAY_HOST` | `localhost` | |
| | `MODEL_BASE_URL` | 无，必填 | OpenAI 兼容地址，以 `/v1` 结尾 |
| | `MODEL_API_KEY` | 无，必填 | |
| | `MODEL_NAME` | 无 | 为空时使用请求里的 `model.modelID` |
| | `GATEWAY_CONFIG` | `./gateway.json` | 引擎共享配置文件；不存在则全部使用默认 |
| | `GATEWAY_DB` | `./gateway.db` | SQLite 文件；`:memory:` 表示不落盘 |
| | `GATEWAY_TURN_TIMEOUT` | `900` | 秒，单轮上限 |
| | `GATEWAY_QUESTION_TIMEOUT` | `120` | 秒，反问无人回复则取默认答案 |
| | `GATEWAY_PERMISSION_MODE` | `auto` | `auto` 直接放行，不发事件；`ask` 挂起等待回复，超时按 `once` 放行 |
| | `GATEWAY_MAX_STEPS` | `50` | 单轮 LLM 调用上限 |
| | `GATEWAY_LOG_LEVEL` | `INFO` | |

参数优先级：命令行 > 环境变量 > `.env` > 默认值。

## gateway.json

```json
{
  "system_prompt": "你是办公助手……",
  "system_prompt_file": "./prompts/system.md",
  "mcpServers": {
    "office": {"command": "uv", "args": ["run", "office-mcp"], "env": {}},
    "remote": {"url": "http://127.0.0.1:8000/mcp", "type": "streamable-http"}
  },
  "skills": ["./skills"],
  "permissions": {
    "ask": ["execute", "run_command", "write_file"]
  }
}
```

| 键 | 说明 |
| --- | --- |
| `system_prompt` / `system_prompt_file` | 二选一，`file` 优先；都缺省时使用内置提示词 |
| `mcpServers` | 与 Claude Desktop / Cursor 的 `mcpServers` 格式一致。有 `command` 为 stdio；有 `url` 按 `type` 取 `streamable-http`（默认）或 `sse` |
| `skills` | skill 根目录列表，相对路径基于配置文件所在目录；每个子目录含 `SKILL.md` |
| `permissions.ask` | 权限模式为 `ask` 时需要审批的工具名；为空表示所有工具都审批。回复 `always` 后同会话内该工具不再询问 |

## Skill 目录

```
skills/
  outlook/
    SKILL.md        # frontmatter: name, description
    scripts/...
```

## 内置系统提示词要点

自主执行、不反问、用工具核实结果、完成后用中文简短汇报、工作目录为会话 `directory`。

## 权限标识

`permission` = `tool.<工具名>`，`patterns` = `[<参数 JSON>]`。

## 健康检查

```
GET /health
{"engine": "deepagents", "model": "…", "tools": ["get_magic_number"], "skills": ["outlook"], "sessions": 0, "permission_mode": "auto"}
```
