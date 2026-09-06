# 架构

## 分层

```
裁判 / 客户端
      │  HTTP + SSE（网关接口规范 1.2）
┌─────▼──────────────────────────────────────────────┐
│ api/        路由、请求校验、错误格式、SSE 推送           │
├────────────────────────────────────────────────────┤
│ core/       会话状态机、轮次执行、消息归一化、事件总线、  │
│             反问/权限队列                             │
├────────────────────────────────────────────────────┤
│ engines/    AgentEngine 契约 + 各引擎适配器            │
│             deepagents_engine  openai_agents_engine  │
├────────────────────────────────────────────────────┤
│ tools/      MCP 配置装载、skill 发现、本地工具          │
└────────────────────────────────────────────────────┘
```

## 模块

| 模块 | 职责 | 依赖 |
| --- | --- | --- |
| `agent_gateway/__main__.py` | 解析 `--engine/--port/--host`，加载 `.env`，启动 uvicorn | config, app, engines.registry |
| `agent_gateway/config.py` | `Settings`（环境变量）与 `GatewayFile`（`gateway.json`）、内置系统提示词 | 无 |
| `agent_gateway/app.py` | FastAPI 工厂：生命周期、错误处理器、路由 | api, gateway |
| `agent_gateway/gateway.py` | `Gateway`：装配 store / bus / hub / engine，管理进行中的轮次 | core, engines.base |
| `agent_gateway/api/routes.py` | 全部路由与 SSE 流 | gateway, core |
| `agent_gateway/api/schemas.py` | 宽松的请求体模型 | 无 |
| `agent_gateway/api/errors.py` | `{code, message}` 错误格式与异常映射 | 无 |
| `agent_gateway/core/models.py` | Session、Message、Part、ToolCall | 无 |
| `agent_gateway/core/store.py` | SQLite 会话仓库：内存工作集 + 写穿，启动时装载 | models |
| `agent_gateway/core/events.py` | EventBus：多订阅者扇出、心跳 | 无 |
| `agent_gateway/core/turn.py` | TurnRunner：消费 EngineEvent，写消息、发 SSE、状态切换、超时、中止、收尾 | models, events, engines.base |
| `agent_gateway/core/interaction.py` | InteractionHub：反问与权限的挂起、回复、超时默认 | events |
| `agent_gateway/engines/base.py` | `AgentEngine` 协议、`EngineEvent`、`InteractionPort` | 无 |
| `agent_gateway/engines/registry.py` | 引擎名 → 工厂 | engines |
| `agent_gateway/engines/deepagents_engine.py` | deepagents 适配器 | tools |
| `agent_gateway/engines/openai_agents_engine.py` | openai-agents 适配器 | tools |
| `agent_gateway/tools/mcp_config.py` | `mcpServers` → 各引擎连接描述 | 无 |
| `agent_gateway/tools/skills.py` | 扫描 skill 目录，解析 `SKILL.md`，生成 skill 清单提示 | 无 |
| `agent_gateway/tools/local.py` | 文件读写、目录列举、命令执行（会话目录为根） | 无 |
| `agent_gateway/tools/permissions.py` | `PermissionGuard`：工具调用前的审批 | engines.base |
| `agent_gateway/tools/ask_user.py` | `ask_user` 工具工厂 | engines.base |
| `agent_gateway/tools/http.py` | httpx 客户端工厂：`trust_env=False, verify=False` | 无 |
| `agent_gateway/static/index.html` | 调试页面，单文件，无外部依赖 | 无 |

## 运行时对象

启动一次，全局单例：`Settings`、`EventBus`、`SessionStore`、`InteractionHub`、`AgentEngine`。

引擎 `start` 失败不终止进程：网关记录错误，`/health`、`POST /session`、`prompt_async` 返回 503，其余只读接口照常。

每个会话：`Session`（状态、消息列表、目录）+ 引擎内部的会话句柄（deepagents 的 thread、openai-agents 的 memory session）。

每轮 `prompt_async`：一个 `TurnRunner` 实例，持有 asyncio Task；中止 = 取消该 Task。

## 一轮请求的流转

```
POST /session/{id}/prompt_async
  → store.begin_turn(id)            状态 idle→busy，追加 user 消息
  → bus.publish(session.status busy)
  → TurnRunner.run()
      engine.run(session, prompt, model, interaction) 产出 EngineEvent 流
      TextDelta      → 当前 assistant 消息 text part 累加，发 message.part.updated
      ToolCallStart  → tool part(running)，发 message.part.updated
      ToolCallEnd    → tool part(completed)，追加 tool 消息
      StepFinish     → 当前 assistant 消息 info.finish，追加 step-finish part
  → 收尾：最后一条 assistant finish=stop，状态 busy→idle
  → bus.publish(session.status idle, session.idle)
  → 204
异常   → 同样收尾（错误文本写入 assistant 消息）+ session.error + 502
排队   → 会话 busy 时新请求等待上一轮结束，按到达顺序逐个执行
中止   → 同样收尾（finish=stop，info.aborted=true）+ 204
超时   → 视为中止，info.aborted_reason=timeout
客户端断连 → 轮次继续执行，结果留在消息列表
```

## 持久化

SQLite 单文件（`GATEWAY_DB`，默认 `gateway.db`）。内存中的 `Session` 对象是工作集，仓库在下列时机写穿：创建/删除会话、状态变更、user 消息追加、tool 消息追加、每次 step-finish、轮次收尾。

启动时装载全部会话，状态一律置为 `idle`，并把 user/assistant 文本历史交给 `engine.open_session(session, history)` 回灌引擎记忆。

## 出站 HTTP

所有出站 httpx 客户端由 `tools/http.py` 创建：`trust_env=False`（忽略系统代理环境变量）、`verify=False`。注入点：`ChatOpenAI(http_client, http_async_client)`、`AsyncOpenAI(http_client)`、MCP `streamable_http` / `sse` 的 `httpx_client_factory`。

## 引擎切换

`--engine` 参数优先，其次环境变量 `AGENT_ENGINE`。一次启动只装配一个引擎。两个引擎共享同一份 MCP、skill、系统提示词配置，由各自适配器翻译成本引擎的接入方式。
