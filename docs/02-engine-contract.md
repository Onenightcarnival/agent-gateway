# 引擎契约

## AgentEngine

```python
class AgentEngine(Protocol):
    name: str

    async def start(self) -> EngineInfo: ...
    async def stop(self) -> None: ...
    async def open_session(self, session: SessionContext) -> None: ...
    async def close_session(self, session_id: str) -> None: ...
    def run(self, session: SessionContext, prompt: str, model: ModelRef,
            interaction: InteractionPort) -> AsyncIterator[EngineEvent]: ...
```

| 方法 | 时机 | 约束 |
| --- | --- | --- |
| `start` | 网关启动 | 连接 MCP、扫描 skill、构建模型客户端；失败即抛错，网关拒绝启动 |
| `stop` | 网关关闭 | 关闭 MCP 子进程 |
| `open_session` | `POST /session` | 绑定工作目录，准备引擎内会话 |
| `close_session` | `DELETE /session/{id}` | 释放引擎内会话 |
| `run` | 每轮 | 异步迭代器；被取消时必须清理并退出，不得吞掉 `CancelledError` |

`EngineInfo`：`name`、`model`、`tool_names`、`skill_names`。

`SessionContext`：`id`、`directory`、`title`。

`ModelRef`：`provider_id`、`model_id`。适配器解析顺序：`Settings.model_name` → `ModelRef.model_id`。

## EngineEvent

| 事件 | 字段 | 含义 |
| --- | --- | --- |
| `TextDelta` | `text` | assistant 文本增量 |
| `ToolCallStart` | `call_id`, `name`, `arguments: dict` | 模型发起工具调用 |
| `ToolCallEnd` | `call_id`, `name`, `output: str`, `is_error: bool` | 工具返回 |
| `StepFinish` | `finish: "stop" \| "tool-calls"` | 一次 LLM 调用结束 |

顺序约束：一轮 = `(TextDelta* ToolCallStart* StepFinish(tool-calls) ToolCallEnd+)* TextDelta* StepFinish(stop)`。
适配器保证最后一个事件是 `StepFinish(stop)`；未保证时由 TurnRunner 补齐。

## InteractionPort

```python
class InteractionPort(Protocol):
    async def ask_question(self, session_id: str, questions: list[Question]) -> list[list[str]]: ...
    async def ask_permission(self, session_id: str, permission: str, patterns: list[str]) -> PermissionReply: ...
```

引擎通过它把反问和权限请求交给网关，网关挂起直到客户端回复或超时。

## 适配器映射

### deepagents

| 网关概念 | 实现 |
| --- | --- |
| 模型 | `ChatOpenAI(base_url, api_key, model)` |
| 会话目录 | 每会话 `LocalShellBackend(root_dir=directory, virtual_mode=False, inherit_env=True)` |
| 会话记忆 | `InMemorySaver` checkpointer，`thread_id = session.id` |
| MCP | `MultiServerMCPClient.get_tools()`，启动时装载一次 |
| skill | `create_deep_agent(skills=[dirs])` |
| 反问 | 函数工具 `ask_user` → `InteractionPort.ask_question` |
| 权限 | `PermissionMiddleware.awrap_tool_call` → `PermissionGuard`；拒绝时返回错误 ToolMessage，并把 `ToolCallEnd(is_error)` 直接注入事件流 |
| 事件流 | `graph.astream_events(v2)`：`on_chat_model_stream`→TextDelta，`on_chat_model_end`→ToolCallStart*+StepFinish，`on_tool_end`→ToolCallEnd |
| 层级过滤 | 只取首个 `on_chat_model_start` / `on_tool_start` 所在深度的事件；子代理（`task` 工具）内部的模型与工具事件不进入轨迹，只保留 `task` 的调用与结果 |
| 工具输出 | ToolMessage 取 content；`Command(update={"messages": [...]})` 取末条消息 content；其他值 JSON 序列化 |
| 中止 | 取消迭代 Task；`execute` 中正在运行的子进程不受影响，自行结束 |

### openai-agents

| 网关概念 | 实现 |
| --- | --- |
| 模型 | `OpenAIChatCompletionsModel(model, AsyncOpenAI(base_url, api_key))`，`set_tracing_disabled(True)` |
| 会话目录 | 本地工具 `read_file` / `write_file` / `list_directory` / `run_command` 以 `directory` 为根；写入 instructions |
| 会话记忆 | 自实现内存 `Session`（`get_items/add_items/pop_item/clear_session`） |
| MCP | `MCPServerStdio` / `MCPServerStreamableHttp`，启动时 `connect()` 一次 |
| skill | instructions 注入 skill 清单（名称、描述、绝对路径）+ 本地工具 `read_file` |
| 反问 | 函数工具 `ask_user` |
| 权限 | `GuardedAgent.get_all_tools` 复制每个 `FunctionTool` 并替换 `on_invoke_tool`（MCP 工具在此阶段已转成 FunctionTool，一并覆盖）；拒绝时返回 `Permission denied by user.` |
| 事件流 | `Runner.run_streamed`：`response.output_text.delta`→TextDelta；`response.completed`→按 `response.output` 中的 `function_call` 产出 ToolCallStart* 再 StepFinish；`tool_output`→ToolCallEnd |
| 中止 | `result.cancel()` + 取消迭代 Task；`run_command` 的子进程随取消被 kill |
