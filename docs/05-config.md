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
| | `GATEWAY_ASK_USER` | `false` | `true` 时向引擎注册 `ask_user` 反问工具；关闭时模型无法反问，`/question` 接口仍可用 |
| | `MODEL_EXTRA_BODY` | 无 | JSON，覆盖 `gateway.json` 的 `model.extra_body` |
| | `GATEWAY_PERMISSION_MODE` | `auto` | `auto` 直接放行，不发事件；`ask` 挂起等待回复，超时按 `once` 放行 |
| | `GATEWAY_MAX_STEPS` | `50` | 单轮 LLM 调用上限 |
| | `GATEWAY_SHELL_SANDBOX` | `on` | `off` 关闭 shell 沙箱 |
| | `GATEWAY_LOG_LEVEL` | `INFO` | |

参数优先级：命令行 > 环境变量 > `.env` > 默认值。

## gateway.json

```json
{
  "model": {
    "extra_body": {"chat_template_kwargs": {"enable_thinking": false}}
  },
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
| `model.extra_body` | 附加到每次 chat completions 请求体的字段。缺省 `{"chat_template_kwargs": {"enable_thinking": false}}`，用于关闭自部署模型的 chat template thinking；显式设为 `{}` 可清空 |
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

自主执行、用工具核实结果、完成后用中文简短汇报、工作目录为会话 `directory`、写入只允许在工作目录内。反问规则随 `GATEWAY_ASK_USER` 切换：关闭时不向用户提问；开启时仅在缺少关键信息时用 `ask_user` 提问，用户明确要求时必须调用。自定义提示词可用占位符 `{directory}`、`{interaction_rule}`。

## 权限标识

`permission` = `tool.<工具名>`，`patterns` = `[<参数 JSON>]`。

## 健康检查

```
GET /health
{"engine": "deepagents", "model": "…", "tools": ["get_magic_number"], "skills": ["outlook"], "sessions": 0, "permission_mode": "auto"}
```

## 权限模型：workspace-write

| 操作 | 工作目录内 | 工作目录外 |
| --- | --- | --- |
| 读取、列举、搜索 | 允许 | 允许（skill 目录、系统文件） |
| 创建、写入、编辑、删除文件 | 允许 | 拒绝，工具返回错误文本 |
| shell 命令 | cwd 为工作目录 | 见下表，按平台做系统级限制 |

### shell 沙箱

| 平台 | 机制 | 可写范围 | 限制 |
| --- | --- | --- | --- |
| macOS | `sandbox-exec` Seatbelt 策略 | 工作目录、`TMPDIR`、`/tmp`、`/dev` | 读不限 |
| Windows | 受限令牌：`CreateRestrictedToken(WRITE_RESTRICTED \| LUA_TOKEN \| DISABLE_MAX_PRIVILEGE)`，限制 SID = [能力 SID, Logon SID, Everyone]；工作目录与 `%TEMP%` 加能力 SID 的允许写 ACE；`CreateProcessAsUser` + Job Object（kill-on-close） | 工作目录、`%TEMP%`，以及 Everyone 可写的位置 | 读不限；网络不限；需要 NTFS；不需要管理员 |
| Linux | 无 | 不限 | 仅系统提示词约束 |

能力 SID 为随机生成的 `S-1-5-21-…`，持久化在 `~/.agent-gateway/cap_sid`，首次运行生成。工作目录的 ACE 在每次会话创建时补齐，带容器与对象继承。

`GATEWAY_SHELL_SANDBOX=off` 关闭 shell 沙箱。开启时若平台不支持或初始化失败（缺 pywin32、非 NTFS、令牌创建失败），记录 ERROR 后退回无沙箱执行，`/health` 的 `shell_sandbox` 报告实际状态。

实现：`tools/workspace.py` 做路径判定；`tools/shell.py` 的 `ShellRunner` 按平台选 `PosixShellRunner`（macOS 包 `sandbox-exec`）或 `WindowsShellRunner`（`tools/windows_sandbox.py`）；deepagents 的 `WorkspaceShellBackend.execute` 与 openai-agents 的 `LocalTools.run_command` 调同一个 runner。
