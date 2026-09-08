"""Windows 原生 shell 沙箱：受限令牌 + 工作目录 ACE + Job Object。

写入检查：`WRITE_RESTRICTED` 令牌对写访问额外核对限制 SID 列表 [能力 SID, Logon SID, Everyone]，
因此只有显式授予能力 SID 写权限的目录（工作目录、%TEMP%）以及 Everyone 可写的位置可写。
读取不受限；不需要管理员权限。
"""

from __future__ import annotations

import os
import secrets
import tempfile
import threading
from pathlib import Path

CAP_SID_FILE = Path.home() / ".agent-gateway" / "cap_sid"

# CreateRestrictedToken flags
DISABLE_MAX_PRIVILEGE = 0x1
LUA_TOKEN = 0x4
WRITE_RESTRICTED = 0x8

SE_GROUP_LOGON_ID = 0xC0000000
EVERYONE_SID = "S-1-1-0"
FILE_ALL_ACCESS = 0x1F01FF
OBJECT_INHERIT_ACE = 0x1
CONTAINER_INHERIT_ACE = 0x2
INHERITED_ACE = 0x10
ACCESS_ALLOWED_ACE_TYPE = 0
GENERIC_ALL = 0x10000000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
CREATE_NO_WINDOW = 0x08000000
ERROR_BROKEN_PIPE = 109
STDIN_DEVICE = "NUL"


def capability_sid_string(path: Path = CAP_SID_FILE) -> str:
    """随机能力 SID，首次生成后持久化，供目录 ACE 与令牌复用。"""
    if path.is_file():
        text = path.read_text(encoding="utf-8").strip()
        if text.startswith("S-1-5-21-"):
            return text
    subs = "-".join(str(secrets.randbelow(2**32)) for _ in range(4))
    sid = f"S-1-5-21-{subs}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sid + "\n", encoding="utf-8")
    return sid


class WindowsSandbox:
    def __init__(self, workspace_root: Path, cap_sid_file: Path = CAP_SID_FILE) -> None:
        self.root = Path(workspace_root).resolve()
        self.cap_sid_file = cap_sid_file
        self._token = None
        self._cap_sid = None
        self._lock = threading.Lock()

    # ---- 准备 ----

    def prepare(self) -> None:
        import win32security

        with self._lock:
            if self._token is not None:
                return
            self._cap_sid = win32security.ConvertStringSidToSid(
                capability_sid_string(self.cap_sid_file)
            )
            self._grant_write(self.root)
            self._grant_write(Path(tempfile.gettempdir()))
            self._token = self._restricted_token()

    def _grant_write(self, directory: Path) -> None:
        import win32security

        sd = win32security.GetNamedSecurityInfo(
            str(directory), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION
        )
        dacl = sd.GetSecurityDescriptorDacl() or win32security.ACL()
        for i in range(dacl.GetAceCount()):
            (ace_type, ace_flags), _mask, sid = dacl.GetAce(i)
            if (
                ace_type == ACCESS_ALLOWED_ACE_TYPE
                and sid == self._cap_sid
                and not (ace_flags & INHERITED_ACE)
            ):
                return
        dacl.AddAccessAllowedAceEx(
            win32security.ACL_REVISION_DS,
            OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE,
            FILE_ALL_ACCESS,
            self._cap_sid,
        )
        win32security.SetNamedSecurityInfo(
            str(directory),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION
            | win32security.UNPROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            dacl,
            None,
        )

    def _restricted_token(self):
        import win32api
        import win32security

        source = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(),
            win32security.TOKEN_DUPLICATE
            | win32security.TOKEN_QUERY
            | win32security.TOKEN_ASSIGN_PRIMARY
            | win32security.TOKEN_ADJUST_DEFAULT,
        )
        logon_sid = None
        for sid, attrs in win32security.GetTokenInformation(source, win32security.TokenGroups):
            if attrs & SE_GROUP_LOGON_ID == SE_GROUP_LOGON_ID:
                logon_sid = sid
                break
        user_sid = win32security.GetTokenInformation(source, win32security.TokenUser)[0]
        everyone = win32security.ConvertStringSidToSid(EVERYONE_SID)
        restrict = [(self._cap_sid, 0), (everyone, 0)]
        if logon_sid is not None:
            restrict.insert(1, (logon_sid, 0))
        token = win32security.CreateRestrictedToken(
            source, DISABLE_MAX_PRIVILEGE | LUA_TOKEN | WRITE_RESTRICTED, None, None, restrict
        )
        dacl = win32security.ACL()
        for sid in (user_sid, self._cap_sid, win32security.ConvertStringSidToSid("S-1-5-18")):
            dacl.AddAccessAllowedAce(win32security.ACL_REVISION, GENERIC_ALL, sid)
        win32security.SetTokenInformation(token, win32security.TokenDefaultDacl, dacl)
        return token

    # ---- 执行 ----

    def spawn(self, command: str, *, cwd: str, env: dict[str, str]) -> SandboxedProcess:
        import win32api
        import win32con
        import win32file
        import win32job
        import win32pipe
        import win32process
        import win32security

        self.prepare()
        sa = win32security.SECURITY_ATTRIBUTES()
        sa.bInheritHandle = True
        read_end, write_end = win32pipe.CreatePipe(sa, 0)
        win32api.SetHandleInformation(read_end, win32con.HANDLE_FLAG_INHERIT, 0)
        stdin = win32file.CreateFile(
            STDIN_DEVICE,
            win32con.GENERIC_READ,
            win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
            sa,
            win32con.OPEN_EXISTING,
            0,
            None,
        )
        si = win32process.STARTUPINFO()
        si.dwFlags = win32con.STARTF_USESTDHANDLES
        si.hStdInput = stdin
        si.hStdOutput = write_end
        si.hStdError = write_end

        job = win32job.CreateJobObject(None, "")
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] |= JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)

        comspec = env.get("COMSPEC") or os.environ.get("COMSPEC") or "cmd.exe"
        cmdline = f'"{comspec}" /d /s /c "{command}"'
        flags = win32con.CREATE_SUSPENDED | CREATE_NO_WINDOW
        try:
            h_process, h_thread, _pid, _tid = win32process.CreateProcessAsUser(
                self._token, None, cmdline, None, None, True, flags, env, cwd, si
            )
        except Exception:
            read_end.Close()
            raise
        finally:
            write_end.Close()
            stdin.Close()
        win32job.AssignProcessToJobObject(job, h_process)
        win32process.ResumeThread(h_thread)
        h_thread.Close()
        return SandboxedProcess(h_process, job, read_end)


class SandboxedProcess:
    def __init__(self, h_process, job, read_end) -> None:
        self._process = h_process
        self._job = job
        self._read_end = read_end
        self._chunks: list[bytes] = []
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        import pywintypes
        import win32file

        try:
            while True:
                _rc, data = win32file.ReadFile(self._read_end, 65536)
                if not data:
                    break
                self._chunks.append(data)
        except pywintypes.error:
            pass
        finally:
            self._read_end.Close()

    def communicate(self, timeout_seconds: int) -> tuple[bytes, int, bool]:
        import win32event
        import win32process

        rc = win32event.WaitForSingleObject(self._process, int(timeout_seconds * 1000))
        timed_out = rc == win32event.WAIT_TIMEOUT
        if timed_out:
            self.kill()
            win32event.WaitForSingleObject(self._process, 5000)
        self._reader.join(timeout=5)
        code = win32process.GetExitCodeProcess(self._process)
        self._close()
        return b"".join(self._chunks), (-1 if timed_out else code), timed_out

    def kill(self) -> None:
        import pywintypes
        import win32job

        try:
            win32job.TerminateJobObject(self._job, 1)
        except pywintypes.error:
            pass

    def _close(self) -> None:
        for handle in (self._process, self._job):
            try:
                handle.Close()
            except Exception:  # noqa: BLE001 - 句柄已关闭
                pass
