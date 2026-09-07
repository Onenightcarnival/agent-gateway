"""workspace-write：写入限定在会话目录内，读取不限；shell 进程经平台沙箱启动。"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import windows_sandbox

MAC_WRITABLE_EXTRA = ("/private/tmp", "/tmp", "/dev")
SANDBOX_UNAVAILABLE_EXIT = 127


class WorkspaceViolation(PermissionError):
    pass


@dataclass(frozen=True)
class ShellResult:
    output: str
    exit_code: int | None
    timed_out: bool = False


class ShellProcess(Protocol):
    def wait(self, timeout: float | None) -> ShellResult: ...

    def kill(self) -> None: ...


class _PopenProcess:
    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self._proc = proc

    def wait(self, timeout: float | None) -> ShellResult:
        try:
            data, _ = self._proc.communicate(timeout=timeout)
            return ShellResult(output=_decode(data), exit_code=self._proc.returncode)
        except subprocess.TimeoutExpired:
            self.kill()
            data, _ = self._proc.communicate()
            return ShellResult(
                output=_decode(data), exit_code=self._proc.returncode, timed_out=True
            )

    def kill(self) -> None:
        if self._proc.poll() is None:
            self._proc.kill()


class _FailedProcess:
    def __init__(self, reason: str) -> None:
        self._reason = reason

    def wait(self, timeout: float | None) -> ShellResult:
        return ShellResult(
            output=f"[sandbox unavailable] {self._reason}", exit_code=SANDBOX_UNAVAILABLE_EXIT
        )

    def kill(self) -> None:
        pass


class Workspace:
    def __init__(self, root: str, *, sandbox: bool = True) -> None:
        self.root = Path(root).expanduser().resolve()
        self.sandbox = sandbox

    # ---- 路径 ----

    def resolve(self, path: str | None) -> Path:
        p = Path(path or ".").expanduser()
        p = p if p.is_absolute() else self.root / p
        return Path(_normalize(p))

    def contains(self, path: str) -> bool:
        target = self.resolve(path)
        return target == self.root or self.root in target.parents

    def check_write(self, path: str) -> Path:
        target = self.resolve(path)
        if not self.contains(path):
            raise WorkspaceViolation(
                f"write outside workspace is not allowed: {target} (workspace: {self.root})"
            )
        return target

    # ---- shell ----

    @property
    def shell_detail(self) -> str:
        if not self.sandbox:
            return "none"
        if sys.platform == "darwin" and shutil.which("sandbox-exec"):
            return "macos-sandbox-exec"
        if sys.platform == "win32":
            ok, reason = windows_sandbox.available()
            return reason if ok else "none"
        return "none"

    @property
    def shell_enforced(self) -> bool:
        return self.shell_detail != "none"

    def sandbox_command(self, command: str) -> tuple[str, bool]:
        if self.shell_detail != "macos-sandbox-exec":
            return command, False
        tmp = os.path.realpath(tempfile.gettempdir())
        allowed = [str(self.root), tmp, *MAC_WRITABLE_EXTRA]
        subpaths = " ".join(f'(subpath "{p}")' for p in allowed)
        profile = f"(version 1)(allow default)(deny file-write*)(allow file-write* {subpaths})"
        return f"sandbox-exec -p {shlex.quote(profile)} /bin/sh -c {shlex.quote(command)}", True

    def spawn(self, command: str, env: dict[str, str] | None = None) -> ShellProcess:
        child_env = {**os.environ, **(env or {})}
        if self.shell_detail == windows_sandbox.DETAIL:
            try:
                return windows_sandbox.spawn(command, str(self.root), env)
            except windows_sandbox.SandboxUnavailable as exc:
                return _FailedProcess(str(exc))
        wrapped, _ = self.sandbox_command(command)
        proc = subprocess.Popen(
            wrapped,
            shell=True,
            cwd=str(self.root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=child_env,
        )
        return _PopenProcess(proc)

    def run(
        self, command: str, timeout: float | None = None, env: dict[str, str] | None = None
    ) -> ShellResult:
        return self.spawn(command, env).wait(timeout)


def _normalize(p: Path) -> str:
    """解析 `..` 与符号链接；目标不存在时按最近的存在祖先解析。"""
    try:
        return str(p.resolve())
    except OSError:
        return str(p.absolute())


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(errors="replace")
