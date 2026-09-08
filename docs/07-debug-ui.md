# 调试页面

路径 `GET /`。单个 HTML 文件，原生 JS，无外部资源。主题跟随系统，可手动切换并记忆。

## 布局

```
┌ 顶栏：产品名 · 引擎/模型/工具/skill 芯片 · SSE 状态 · 主题 ─────────────────────┐
├ 侧栏 280px ────────┬ 会话区 ─────────────────────────┬ 检查器 380px（可收起）┤
│ [＋ 新建会话]       │ 会话头：标题 · id⧉ · 目录⧉ · 状态  │ 页签：待处理⑵ 事件 信息 │
│ 搜索               │ 线程                              │ 待处理：反问/权限卡片    │
│ 会话列表            │  user 气泡                        │ 事件：过滤 · 暂停 · 清空 │
│  标题 · 相对时间    │  assistant 卡片：Markdown 文本     │   type 芯片 · 时间 · 摘要 │
│  状态点 · 悬停删除  │   └ 工具卡片：状态 · 参数 · 输出    │   点击展开 JSON         │
│                    │  反问 / 权限 内嵌卡片               │ 信息：id、目录、时间、   │
│                    │  错误条                            │   消息数、引擎          │
│                    │ 输入区：自动增高 · 发送⇄停止        │                        │
└────────────────────┴───────────────────────────────────┴────────────────────────┘
```

## 组件

| 组件 | 说明 |
| --- | --- |
| 会话项 | 标题、`message_count`、相对时间、状态点（idle 绿 / busy 琥珀带脉冲）；悬停显示删除，删除弹确认 |
| 会话头 | 标题；id 与目录各带复制按钮；状态徽标 |
| 助手卡片 | 文本经轻量 Markdown（标题、列表、粗体、行内代码、代码块、链接）；页脚显示 `finish`、`aborted`、`error` |
| 工具卡片 | 工具名、状态芯片（running 脉冲 / completed / error）、参数（扁平对象按键值表，否则 JSON）、输出（折叠，超 40 行显示"展开"）；对应的 tool 消息并入卡片，不单独渲染 |
| 反问卡片 | 问题、选项按钮、自定义输入；回复后卡片移除 |
| 权限卡片 | 权限名、patterns、once / always / reject |
| 输入区 | Enter 发送，Shift+Enter 换行；busy 时按钮变"停止"调 `/abort` |
| 事件行 | type 芯片按类别着色（server / session / message / question / permission）、时间、会话短 id、摘要；点击展开完整 JSON；可暂停、按 type 过滤、仅当前会话 |
| 新建会话对话框 | 目录输入 + 浏览、标题；最近使用目录（localStorage，最多 8 条） |
| 目录浏览对话框 | 根目录下拉、路径面包屑可点、子目录列表（单击选中、双击进入）、新建文件夹、选择 |
| Toast | 操作结果与错误提示，3 秒消失 |

## 数据流

| 触发 | 调用 |
| --- | --- |
| 加载 | `GET /health`、`GET /session`、`GET /question`、`GET /permission`、`GET /event`（断开 3 秒重连） |
| 新建会话 | `POST /session`；成功后选中并记录目录 |
| 选中会话 | `GET /session/{id}/message` |
| 发送 | `POST /session/{id}/prompt_async`（后台 fetch）；本地先插入 user 气泡 |
| 停止 | `POST /session/{id}/abort` |
| 删除 | `DELETE /session/{id}` |
| 反问 / 权限回复 | `POST /question/{id}/reply`、`POST /permission/{id}/reply` |
| `message.part.updated` | 当前会话时就地更新对应消息节点 |
| `session.status` / `session.idle` | 更新状态点与按钮；idle 时重新拉取消息 |
| `question.asked` / `permission.asked` | 加入待处理与线程内嵌卡片 |
| `session.error` | 线程末尾错误条 + toast |

`prompt_async` 的 `model` 取 `/health` 的模型名，`providerID` 固定 `gateway`。
