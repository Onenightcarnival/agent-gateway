# Windows 沙箱：受限令牌 workspace-write

参考实现：deepseek-harness `packages/sandbox/sandbox-windows-acl`（原理来自 `huoyaoyuan/windows-acl-restrict-poc`）。

## 原理

`WRITE_RESTRICTED` 受限令牌让内核对写类访问做两次检查：一次用进程的正常 SID，一次只用"限制 SID 列表"。读不经过第二次检查，因此不需要给任何可读路径授权；只要限制列表里的 SID 在目标目录上持有写 ACE，写就能通过。

```
workspace 路径 ──sha256──▶ S-1-4-a-b（workspace SID）
私有 temp 路径 ──sha256──▶ S-1-4-c-d（temp SID）

workspace 目录  += ACE(workspace SID, Modify, OI|CI)
私有 temp 目录  += ACE(temp SID,      Modify, OI|CI)

当前用户令牌 ──CreateRestrictedToken(WRITE_RESTRICTED|LUA_TOKEN|DISABLE_MAX_PRIVILEGE,
                 restricting=[logon SID, Everyone, workspace SID, temp SID])──▶ 受限令牌
受限令牌.DefaultDacl += ALLOW(workspace SID, GENERIC_ALL), ALLOW(temp SID, GENERIC_ALL)

CreateProcessAsUser(受限令牌, cmd.exe /d /s /c "<command>", CREATE_SUSPENDED, TMP/TEMP=私有 temp)
  → Job Object(KILL_ON_JOB_CLOSE) → AssignProcessToJobObject → ResumeThread
```

## 模块

`agent_gateway/tools/windows_sandbox.py`，只依赖 `pywin32`（`sys_platform == "win32"` 时安装）。

| 函数 | 作用 |
| --- | --- |
| `available()` | `(bool, reason)`：平台是否为 win32 且 pywin32 可导入 |
| `workspace_sid(path)` / `temp_sid(path)` | sha256 派生 `S-1-4-a-b`，每个子权限 30 位；temp 加域分隔并多一个 `-1` 后缀 |
| `grant_write(directory, sid)` | 已有同样的显式 ACE 则返回 `False`，否则 `SetNamedSecurityInfo` 追加并返回 `True` |
| `create_restricted_token(write_sids)` | 打开当前进程令牌 → 找 logon SID → `CreateRestrictedToken` → 修补 DefaultDacl |
| `spawn(command, workspace, env)` | 返回 `WindowsSandboxProcess`（`wait(timeout)` / `kill()`） |

`Workspace.spawn` 在 win32 且沙箱可用时走这里；否则走 `subprocess.Popen(shell=True)`（macOS 先用 `sandbox-exec` 包装）。

## 约束（来自参考实现的实测）

- 限制列表必须含 logon SID 与 Everyone，否则 DLL 初始化失败（`0xC0000142`）、PowerShell 崩溃。
- 必须修补受限令牌的 DefaultDacl，否则受限进程内再创建带管道的子进程会 `EPERM`。
- 不能用 `CREATE_NO_WINDOW`。
- 工作目录必须归当前用户所有（owner 隐含 `WRITE_DAC`），不需要管理员。
- 首次给大目录打 ACE 会全树传播，耗时随文件数增长；之后命中"已存在"跳过。
- ACE 永久残留在工作目录上，`icacls` 无法删除（`ERROR_NONE_MAPPED`）。

## 已知绕过

只限写，不限读、不限网络。对 Everyone 可写的对象、NTFS 硬链接、FAT 卷无效。`/health` 报告 `shell_sandbox: true, shell_sandbox_detail: "windows-restricted-token (partial)"`。

## 开关

`GATEWAY_SHELL_SANDBOX=off` 关闭系统级沙箱（所有平台），shell 退回 `subprocess.Popen`，文件工具层的 workspace 校验不受影响。沙箱初始化失败时不降级：命令返回退出码 127 与 `[sandbox unavailable] <原因>`。

## 真机验证清单

```
uv sync
uv run pytest tests/unit/test_windows_sandbox.py -v
uv run python -m agent_gateway --engine deepagents --port 6217
curl http://localhost:6217/health          # shell_sandbox 应为 true
```

再用调试页面建一个会话，分别让 agent：在工作目录写文件（应成功）、用 `execute` 把文件写到桌面（应失败，输出含 Access is denied）、读桌面上一个已有文件（应成功）、运行 `python -c "import subprocess; print(subprocess.run(['cmd','/c','echo ok'], capture_output=True, text=True).stdout)"`（应输出 ok）。
