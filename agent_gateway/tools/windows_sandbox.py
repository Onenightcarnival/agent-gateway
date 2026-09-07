"""Windows 受限令牌沙箱（docs/08-windows-sandbox.md）。只在 win32 且安装了 pywin32 时可用。"""

from __future__ import annotations

import hashlib
import locale
import os
import sys
import tempfile
import threading
from pathlib import Path

# ---- 常量（Win32） ----
DISABLE_MAX_PRIVILEGE = 0x1
LUA_TOKEN = 0x4
WRITE_RESTRICTED = 0x8
SE_GROUP_LOGON_ID = 0xC0000000
CREATE_SUSPENDED = 0x4
CREATE_UNICODE_ENVIRONMENT = 0x400
STARTF_USESTDHANDLES = 0x100
HANDLE_FLAG_INHERIT = 0x1
ERROR_BROKEN_PIPE = 109
ERROR_INVALID_PARAMETER = 87
WAIT_TIMEOUT = 0x102
EVERYONE = "S-1-1-0"
TEMP_ROOT_NAME = "agent-gateway-sandbox"
DETAIL = "windows-restricted-token (partial)"


class SandboxUnavailable(RuntimeError):
    pass


def available() -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, f"platform is {sys.platform}, not win32"
    try:
        import win32security  # noqa: F401
    except ImportError as exc:
        return False, f"pywin32 not importable: {exc}"
    return True, DETAIL


# ---- SID 派生 ----


def _sid_from_digest(data: bytes, suffix: str = "") -> str:
    digest = hashlib.sha256(data).digest()
    first = int.from_bytes(digest[0:4], "big") & (2**30 - 1)
    second = int.from_bytes(digest[4:8], "big") & (2**30 - 1)
    return f"S-1-4-{first}-{second}{suffix}"


def workspace_sid(workspace_root: str) -> str:
    return _sid_from_digest(_canonical(workspace_root).encode("utf-8"))


def temp_sid(workspace_root: str) -> str:
    return _sid_from_digest(b"temp\0" + _canonical(workspace_root).encode("utf-8"), suffix="-1")


def _canonical(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def private_temp_dir(workspace_root: str) -> Path:
    tag = hashlib.sha256(_canonical(workspace_root).encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / TEMP_ROOT_NAME / tag


# ---- ACL ----


def _grant_mask() -> int:
    import ntsecuritycon as con

    return (
        con.FILE_GENERIC_WRITE | con.DELETE | con.FILE_DELETE_CHILD
    ) & ~con.STANDARD_RIGHTS_WRITE


def grant_write(directory: str, sid_string: str) -> bool:
    """给目录追加可继承的写 ACE。已存在完全相同的显式 ACE 时返回 False。"""
    import ntsecuritycon as con
    import win32security

    sid = win32security.ConvertStringSidToSid(sid_string)
    mask = _grant_mask()
    inherit = con.OBJECT_INHERIT_ACE | con.CONTAINER_INHERIT_ACE
    sd = win32security.GetNamedSecurityInfo(
        directory, win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION
    )
    dacl = sd.GetSecurityDescriptorDacl()
    if dacl is None:
        dacl = win32security.ACL()
    for i in range(dacl.GetAceCount()):
        (ace_type, ace_flags), ace_mask, ace_sid = dacl.GetAce(i)
        if (
            ace_type == con.ACCESS_ALLOWED_ACE_TYPE
            and ace_sid == sid
            and (ace_mask & mask) == mask
            and (ace_flags & inherit) == inherit
            and not (ace_flags & con.INHERITED_ACE)
        ):
            return False
    dacl.AddAccessAllowedAceEx(win32security.ACL_REVISION_DS, inherit, mask, sid)
    win32security.SetNamedSecurityInfo(
        directory,
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION,
        None,
        None,
        dacl,
        None,
    )
    return True


# ---- 令牌 ----


def _logon_sid(token):
    import win32security

    for sid, attrs in win32security.GetTokenInformation(token, win32security.TokenGroups):
        if (attrs & 0xFFFFFFFF) & SE_GROUP_LOGON_ID == SE_GROUP_LOGON_ID:
            return sid
    raise SandboxUnavailable("logon SID not found in current token")


def create_restricted_token(write_sids: list[str]):
    """返回 WRITE_RESTRICTED 受限令牌；限制列表 = logon SID + Everyone + write_sids。"""
    import ntsecuritycon as con
    import win32api
    import win32security

    current = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(),
        con.TOKEN_DUPLICATE | con.TOKEN_QUERY | con.TOKEN_ASSIGN_PRIMARY | con.TOKEN_ADJUST_DEFAULT,
    )
    restricting = [(_logon_sid(current), 0), (win32security.ConvertStringSidToSid(EVERYONE), 0)]
    restricting += [(win32security.ConvertStringSidToSid(s), 0) for s in write_sids]
    token = win32security.CreateRestrictedToken(
        current, DISABLE_MAX_PRIVILEGE | LUA_TOKEN | WRITE_RESTRICTED, None, None, restricting
    )
    dacl = win32security.GetTokenInformation(token, win32security.TokenDefaultDacl)
    if dacl is None:
        dacl = win32security.ACL()
    for s in write_sids:
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION, con.GENERIC_ALL, win32security.ConvertStringSidToSid(s)
        )
    win32security.SetTokenInformation(token, win32security.TokenDefaultDacl, dacl)
    return token


# ---- 进程 ----


class WindowsSandboxProcess:
    def __init__(self, job, process, thread, read_handle) -> None:
        self._job = job
        self._process = process
        self._thread = thread
        self._read = read_handle
        self._chunks: list[bytes] = []
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        import pywintypes
        import win32file

        try:
            while True:
                _, data = win32file.ReadFile(self._read, 65536)
                if not data:
                    break
                self._chunks.append(data)
        except pywintypes.error as exc:
            if exc.winerror != ERROR_BROKEN_PIPE:
                self._chunks.append(f"[read error] {exc}".encode())
        finally:
            self._read.Close()

    def wait(self, timeout: float | None):
        import win32event
        import win32process

        from .workspace import ShellResult

        ms = win32event.INFINITE if timeout is None else int(timeout * 1000)
        timed_out = win32event.WaitForSingleObject(self._process, ms) == WAIT_TIMEOUT
        if timed_out:
            self.kill()
            win32event.WaitForSingleObject(self._process, 5000)
        self._reader.join(5)
        code = win32process.GetExitCodeProcess(self._process)
        return ShellResult(
            output=_decode(b"".join(self._chunks)), exit_code=code, timed_out=timed_out
        )

    def kill(self) -> None:
        import pywintypes
        import win32job

        try:
            win32job.TerminateJobObject(self._job, 1)
        except pywintypes.error:
            pass


def spawn(
    command: str, workspace_root: str, env: dict[str, str] | None = None
) -> WindowsSandboxProcess:
    import pywintypes
    import win32api
    import win32con
    import win32file
    import win32job
    import win32pipe
    import win32process
    import win32security

    root = _canonical(workspace_root)
    tmp = private_temp_dir(root)
    tmp.mkdir(parents=True, exist_ok=True)
    ws_sid, tmp_sid = workspace_sid(root), temp_sid(root)
    grant_write(root, ws_sid)
    grant_write(str(tmp), tmp_sid)
    token = create_restricted_token([ws_sid, tmp_sid])

    child_env = {**os.environ, **(env or {})}
    child_env["TMP"] = child_env["TEMP"] = str(tmp)

    sa = win32security.SECURITY_ATTRIBUTES()
    sa.bInheritHandle = True
    read_h, write_h = win32pipe.CreatePipe(sa, 0)
    win32api.SetHandleInformation(read_h, HANDLE_FLAG_INHERIT, 0)
    stdin_h = win32file.CreateFile(
        "NUL", win32con.GENERIC_READ, win32con.FILE_SHARE_READ, sa, win32con.OPEN_EXISTING, 0, None
    )
    si = win32process.STARTUPINFO()
    si.dwFlags = STARTF_USESTDHANDLES
    si.hStdInput = stdin_h
    si.hStdOutput = write_h
    si.hStdError = write_h

    comspec = os.environ.get("COMSPEC", "cmd.exe")
    cmdline = f'"{comspec}" /d /s /c "{command}"'
    flags = CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT
    try:
        try:
            process, thread, _, _ = win32process.CreateProcessAsUser(
                token, None, cmdline, None, None, True, flags, child_env, root, si
            )
        except pywintypes.error as exc:
            if exc.winerror != ERROR_INVALID_PARAMETER:
                raise
            for key in ("TMP", "TEMP"):
                win32api.SetEnvironmentVariable(key, str(tmp))
            process, thread, _, _ = win32process.CreateProcessAsUser(
                token, None, cmdline, None, None, True, flags, None, root, si
            )
    except pywintypes.error as exc:
        write_h.Close()
        read_h.Close()
        stdin_h.Close()
        raise SandboxUnavailable(f"CreateProcessAsUser failed: {exc}") from exc
    finally:
        token.Close()
    write_h.Close()
    stdin_h.Close()

    job = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    win32job.AssignProcessToJobObject(job, process)
    win32process.ResumeThread(thread)
    return WindowsSandboxProcess(job, process, thread, read_h)


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False), errors="replace")
