# HTTP 接口

规范：`quest/网关接口规范_通用-1.2.md`。本表记录实现取值与宽松度。

## 路由

| 方法 | 路径 | 响应 | 备注 |
| --- | --- | --- | --- |
| GET | `/health` | `{engine, model, tools, skills, sessions}` | 规范外，部署自检；引擎不可用时 503 |
| GET | `/`、`/ui` | 调试页面 HTML | 规范外 |
| GET | `/session` | `SessionSummary[]` | 规范外，调试页面用 |
| GET | `/fs/dirs?path=` | `{path, parent, roots[], entries[{name, path}]}` | 规范外，调试页面目录浏览；`path` 缺省为用户主目录，只列子目录，隐藏目录不列 |
| POST | `/fs/dirs` | `{path}` | 规范外，`{parent, name}` 新建子目录 |
| POST | `/session` | 200 Session 摘要 | `directory` 必填；`title` 可缺省 |
| GET | `/session/status` | `{id: {type}}` | 注册顺序先于 `/session/{id}` |
| GET | `/session/{id}` | Session 摘要 + `message_count` | |
| DELETE | `/session/{id}` | `{ok: true}` | busy 时先中止 |
| POST | `/session/{id}/prompt_async` | 204 | 阻塞到本轮结束；会话 busy 时排队，待上一轮结束后按到达顺序执行 |
| GET | `/session/{id}/message` | `Message[]` | |
| POST | `/session/{id}/abort` | `{ok: true}` | idle 时也返回 ok |
| POST | `/session/{id}/stop` | 同上 | 别名 |
| GET | `/question` | `PendingQuestion[]` | |
| POST | `/question/{id}/reply` | `{ok: true}` | `answers: list[list[str]]` |
| GET | `/permission` | `PendingPermission[]` | |
| POST | `/permission/{id}/reply` | `{ok: true}` | `reply: once/always/reject` |
| GET | `/event` | `text/event-stream` | |

## 请求宽松度

| 字段 | 缺省行为 |
| --- | --- |
| `POST /session` `title` | 自动生成 |
| `prompt_async` `model` | `MODEL_NAME` 优先；未设置时取 `model.modelID`；都没有则 400 |
| `prompt_async` `agent` | 忽略 |
| `prompt_async` `parts` 中非 `text` | 忽略；全部忽略后为空则 400 |
| 未知字段 | 忽略 |

## 错误

```json
{"code": "NOT_FOUND", "message": "Session not found"}
```

| 状态 | code | 场景 |
| --- | --- | --- |
| 400 | `VALIDATION_ERROR` | 请求体校验失败、`directory` 缺失、`parts` 为空、`MODEL_NAME` 与 `model.modelID` 同时缺失 |
| 404 | `NOT_FOUND` | 会话、问题、权限请求、目录不存在 |
| 502 | `BAD_GATEWAY` | 引擎执行异常 |
| 500 | `INTERNAL_ERROR` | 其他未捕获异常 |
| 503 | `SERVICE_UNAVAILABLE` | 引擎启动失败或网关正在关闭；影响 `/health`、`POST /session`、`prompt_async` |

## SSE 响应头

```
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```
