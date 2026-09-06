# 测试策略

## 分层

| 层 | 目录 | 引擎 | 模型 | 运行 |
| --- | --- | --- | --- | --- |
| 单元 | `tests/unit/` | 测试内 `ScriptedEngine`（按脚本产出 EngineEvent） | 无 | `uv run pytest tests/unit` |
| 集成 | `tests/integration/` | 真实 deepagents / openai-agents | `.env` 指向的真实服务，chat completions | `uv run pytest tests/integration` |

单元层用 uvicorn 在进程内起真实 HTTP 服务，覆盖 SSE 长连接和客户端断连。集成层每个用例独立起一个网关（含 MCP 子进程）；`test_cli.py` 以子进程方式启动 `python -m agent_gateway`，验证 `AGENT_ENGINE` 环境变量切换引擎。

`ScriptedEngine` 只用于验证网关本身：状态机、消息归一化、SSE、中止、超时、错误收尾、反问与权限。模型不做 mock。

## 单元用例

- 会话 CRUD、`/session/status` 路由优先级、404/400 格式
- 一轮正常对话：消息序列、parts、finish、SSE 事件顺序
- 多步工具调用：assistant(tool-calls) → tool → assistant(stop)
- busy 期间再次 prompt → 排队，两轮按序完成
- 引擎启动失败 → `/health`、建会话、prompt 返回 503，`GET /session/status` 仍可用
- `ask_user` 开关：默认关闭时引擎不注册反问工具
- abort：任务被取消，最后消息 `aborted=true`，状态回 idle
- 超时：同 abort，`aborted_reason=timeout`
- 引擎异常：502，`session.error`，最后消息 finish=stop
- 客户端断连：轮次继续完成
- 反问：`question.asked` → `GET /question` → reply → 引擎收到答案；超时取默认
- 权限：`ask` 模式挂起，reply `reject` 时工具收到拒绝；`auto` 模式不发事件
- 配置：环境变量解析与优先级、`gateway.json` 路径解析、`model.extra_body` 默认值与覆盖
- 存储：SQLite 往返、级联删除、重启后会话与消息可读、引擎收到历史回灌
- 出站 HTTP：工厂产出的客户端 `trust_env=False`、`verify=False`
- `ask_user` 的 OpenAI 工具 schema 不含 `anyOf` / `null`
- 调试页面：`GET /` 返回 HTML
- 工具：skill 扫描与覆盖规则、`mcpServers` 三种传输解析、本地工具的根目录约束、命令超时与取消时杀进程

## 集成用例（每个引擎一份，同一测试参数化）

- `/health` 报告引擎名、模型、工具数
- 纯对话：回复非空，finish=stop，step-finish 存在
- 工具调用：写文件到会话目录，轨迹含 tool 消息，文件实际存在
- 会话记忆：第二轮引用第一轮内容
- 会话隔离：两个会话互不可见
- skill：读取测试 skill 并按其指令行动
- MCP：连接测试用 stdio MCP 服务并调用其工具
- 中止：长任务中途 abort 后状态回 idle
- 权限：`ask` 模式下拒绝 `execute` / `run_command`，轨迹里出现拒绝结果
- 入口：子进程启动网关，`/health` 报告环境变量指定的引擎，一轮对话成功；未知引擎与缺失引擎退出码 2

## 夹具

- `tests/fixtures/skills/echo-secret/SKILL.md`：指令中含一个暗号，用例验证暗号出现在回复里
- `tests/fixtures/mcp_server.py`：基于 `mcp` FastMCP 的 stdio 服务，提供 `get_magic_number` 工具
