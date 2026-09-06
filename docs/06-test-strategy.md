# 测试策略

## 分层

| 层 | 目录 | 引擎 | 模型 | 运行 |
| --- | --- | --- | --- | --- |
| 单元 | `tests/unit/` | 测试内 `ScriptedEngine`（按脚本产出 EngineEvent） | 无 | `uv run pytest tests/unit` |
| 集成 | `tests/integration/` | 真实 deepagents / openai-agents | `.env` 指向的真实服务，chat completions | `uv run pytest -m integration` |

`ScriptedEngine` 只用于验证网关本身：状态机、消息归一化、SSE、中止、超时、错误收尾、反问与权限。模型不做 mock。

## 单元用例

- 会话 CRUD、`/session/status` 路由优先级、404/400 格式
- 一轮正常对话：消息序列、parts、finish、SSE 事件顺序
- 多步工具调用：assistant(tool-calls) → tool → assistant(stop)
- busy 期间再次 prompt → 409
- abort：任务被取消，最后消息 `aborted=true`，状态回 idle
- 超时：同 abort，`aborted_reason=timeout`
- 引擎异常：502，`session.error`，最后消息 finish=stop
- 客户端断连：轮次继续完成
- 反问：`question.asked` → `GET /question` → reply → 引擎收到答案；超时取默认
- 权限：`ask` 模式挂起，reply `reject` 时工具收到拒绝

## 集成用例（每个引擎一份，同一测试参数化）

- `/health` 报告引擎名、模型、工具数
- 纯对话：回复非空，finish=stop，step-finish 存在
- 工具调用：写文件到会话目录，轨迹含 tool 消息，文件实际存在
- 会话记忆：第二轮引用第一轮内容
- 会话隔离：两个会话互不可见
- skill：读取测试 skill 并按其指令行动
- MCP：连接测试用 stdio MCP 服务并调用其工具
- 中止：长任务中途 abort 后状态回 idle

## 夹具

- `tests/fixtures/skills/echo-secret/SKILL.md`：指令中含一个暗号，用例验证暗号出现在回复里
- `tests/fixtures/mcp_server.py`：基于 `mcp` FastMCP 的 stdio 服务，提供 `get_magic_number` 工具
