# 调试页面

路径 `GET /`。单个 HTML 文件，原生 JS，无外部资源，深浅色随系统。

## 布局

```
┌ 顶栏：引擎 · 模型 · 工具数 · skill 数 · SSE 连接状态 ───────────────┐
├ 左栏 260px ────────┬ 中栏 ──────────────────────┬ 右栏 360px ─────┤
│ 新建会话 · 浏览…   │ 消息列表                     │ 待处理反问/权限   │
│  directory, title  │  user / assistant / tool     │  选项按钮、放行/拒绝│
│ 会话列表           │  tool part 折叠：参数、输出    │ 事件流            │
│  状态徽标 idle/busy│  step-finish 分隔线           │  type、sessionID  │
│  删除              │ 输入框 · 发送 · 中止           │  按当前会话过滤    │
└────────────────────┴──────────────────────────────┴──────────────────┘
```

## 行为

| 动作 | 调用 |
| --- | --- |
| 页面加载 | `GET /health`、`GET /session`、`GET /event`（SSE，断开后 3 秒重连） |
| 新建会话 | `POST /session` |
| 浏览目录 | `GET /fs/dirs?path=`：弹窗列出根（Windows 为盘符，其他为 `/`）、上级、子目录；双击进入，`选择此目录` 回填 directory 输入框；`新建文件夹` 调 `POST /fs/dirs` |
| 选中会话 | `GET /session/{id}/message` |
| 发送 | `POST /session/{id}/prompt_async`（后台 fetch，不阻塞界面） |
| 中止 | `POST /session/{id}/abort` |
| 删除 | `DELETE /session/{id}` |
| 反问回复 | `POST /question/{id}/reply` |
| 权限回复 | `POST /permission/{id}/reply` |
| `message.part.updated` | 当前会话时增量刷新对应消息，否则忽略 |
| `session.status` / `session.idle` | 更新会话徽标；空闲时重新拉取消息列表 |
| `question.asked` / `permission.asked` | 加入待处理面板；回复后移除 |
| `session.error` | 在消息列表末尾显示错误条 |

## 请求参数

`prompt_async` 的 `model` 取顶栏显示的模型名（`providerID` 固定 `gateway`）。
