# 消息模型

## Session

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `ses_` + 12 位随机 | |
| `title` | string | 缺省 `"Session <时间>"` |
| `directory` | string | 绝对路径，创建时若不存在则创建 |
| `created_at` | ISO 8601 UTC | |
| `status` | `idle` / `busy` | |
| `messages` | `list[Message]` | |

## Message

```
UserMessage       id, role="user", content, created_at
AssistantMessage  id, role="assistant", content, tool_calls[], created_at, info{role, finish, aborted?, error?}, parts[]
ToolMessage       id, role="tool", tool_call_id, tool_name, content, created_at, is_error
```

`id`：`msg_` + 12 位随机。`content` 为该消息全部文本 part 的拼接。

## Part

| type | 字段 | 生命周期 |
| --- | --- | --- |
| `text` | `content` | 每次 TextDelta 累加，整段推送 |
| `tool` | `tool`, `call_id`, `arguments`, `state{status, title, output?}` | `running` → `completed` / `error` |
| `step-finish` | 无 | StepFinish 时追加 |

`state.title`：`running` 时为 `"正在执行 <tool>"`，完成时为 `"<tool> 完成"`，出错时为 `"<tool> 失败"`。

## 一轮的消息序列

一次 LLM 调用对应一条 assistant 消息。工具结果作为独立 tool 消息紧随其后。

```
user
assistant  finish=tool-calls  parts=[text?, tool(completed), step-finish]  tool_calls=[…]
tool
assistant  finish=stop        parts=[text, step-finish]
```

不变量：轮次结束后，`messages[-1].role == "assistant"`，`info.finish == "stop"`，`parts` 含 `step-finish`。

## 终止形态

| 情形 | 最后一条 assistant | HTTP |
| --- | --- | --- |
| 正常 | `finish=stop` | 204 |
| 引擎异常 | `finish=stop`，`info.error=<message>`，text part 追加错误说明 | 502 |
| 中止 | `finish=stop`，`info.aborted=true` | 204 |
| 超时 | `finish=stop`，`info.aborted=true`，`info.aborted_reason="timeout"` | 204 |

## 事件（SSE）

`data: {"type": …, "properties": …}`，每 15 秒 `server.heartbeat`。

| type | properties |
| --- | --- |
| `server.connected` | `{}` |
| `server.heartbeat` | `{}` |
| `session.status` | `sessionID`, `status{type}` |
| `session.idle` | `sessionID` |
| `session.error` | `sessionID`, `error{message, data}` |
| `message.part.updated` | `sessionID`, `messageID`, `part` |
| `question.asked` | `sessionID`, `id`, `questions[]` |
| `permission.asked` | `sessionID`, `id`, `permission`, `patterns[]` |

SSE 不重放历史。消息列表是事实来源。

## 存储

```
sessions(id PK, title, directory, created_at, status)
messages(id PK, session_id → sessions.id ON DELETE CASCADE, seq, role, body JSON)
```

`body` 为消息的完整 JSON；`seq` 为消息在会话内的序号。装载时按 `seq` 排序还原为模型对象。
