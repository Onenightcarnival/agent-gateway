# agent-gateway

Agent 网关：统一的 HTTP + SSE 接口，底层 Agent 引擎可替换。当前接入 `deepagents` 与 `openai-agents`，两者共享同一份 MCP、skill 与系统提示词配置。

## 运行

```bash
uv sync
cp .env.example .env            # 填入模型地址、密钥、模型名
uv run python -m agent_gateway --engine deepagents --port 6217
uv run python -m agent_gateway --engine openai-agents --port 6217
```

引擎也可由环境变量 `AGENT_ENGINE` 指定；命令行参数优先。

```bash
curl http://localhost:6217/health
```

浏览器打开 `http://localhost:6217/` 进入调试页面。会话与消息存于 `gateway.db`（SQLite）。

## 配置

| 来源 | 内容 |
| --- | --- |
| `.env` / 环境变量 | 模型、引擎、端口、超时、权限模式（见 `.env.example`） |
| `gateway.json` | MCP 服务、skill 目录、系统提示词、需审批的工具（见 `gateway.example.json`） |

## 测试

```bash
uv run pytest tests/unit          # 不依赖模型
uv run pytest tests/integration   # 使用 .env 指向的真实模型
```

## 文档

| 文件 | 内容 |
| --- | --- |
| [docs/01-architecture.md](docs/01-architecture.md) | 分层、模块、一轮请求的流转 |
| [docs/02-engine-contract.md](docs/02-engine-contract.md) | `AgentEngine` 契约、`EngineEvent`、两个适配器的映射 |
| [docs/03-message-model.md](docs/03-message-model.md) | 会话、消息、part、SSE 事件 |
| [docs/04-http-api.md](docs/04-http-api.md) | 路由、宽松度、错误码 |
| [docs/05-config.md](docs/05-config.md) | 启动参数、环境变量、`gateway.json` |
| [docs/06-test-strategy.md](docs/06-test-strategy.md) | 测试分层与用例清单 |
| [docs/07-debug-ui.md](docs/07-debug-ui.md) | 调试页面布局与行为 |
| [docs/08-windows-sandbox.md](docs/08-windows-sandbox.md) | Windows 受限令牌沙箱与真机验证清单 |

赛题原文见 `quest/`。
