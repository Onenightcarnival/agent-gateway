"""ShellRunner：在工作目录内执行命令，按平台施加系统级写入限制。"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from dataclasses import dataclass

from .workspace import Workspace

log = logging.getLogger(__name__)

DEFAULT_MAX_OUTPUT = 100_000
CHILD_ENV = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


@dataclass(frozen=True)
class CommandResult:
    output: str
    exit_code: int
    truncated: bool = False
    timed_out: bool = False


class ShellRunner:
    sandboxed: bool = False

    def __init__(self, workspace: Workspace, *, max_output_chars: int = DEFAULT_MAX_OUTPUT) -> None:
        self.workspace = workspace
        self.max_output_chars = max_output_chars

    def env(self) -> dict[str, str]:
        return {**os.environ, **CHILD_ENV}

    def finish(self, raw: bytes, exit_code: int, timed_out: bool = False) -> CommandResult:
        text = raw.decode("utf-8", errors="replace")
        truncated = len(text) > self.max_output_chars
        if truncated:
            text = text[: self.max_output_chars] + "\n...[truncated]"
        return CommandResult(text, exit_code, truncated, timed_out)

    def run(self, command: str, timeout_seconds: int) -> CommandResult:
        raise NotImplementedError

    async def arun(self, command: str, timeout_seconds: int) -> CommandResult:
        raise NotImplementedError


class PosixShellRunner(ShellRunner):
    """`/bin/sh -c`；macOS 下经 `sandbox-exec` 限制写入。"""

    def __init__(self, workspace: Workspace, *, enabled: bool, **kw) -> None:
        super().__init__(workspace, **kw)
        self.enabled = enabled
        self.sandboxed = enabled and workspace.shell_enforced

    def _command(self, command: str) -> str:
        if not self.sandboxed:
            return command
        wrapped, _ = self.workspace.sandbox_command(command)
        return wrapped

    def run(self, command: str, timeout_seconds: int) -> CommandResult:
        try:
            proc = subprocess.run(
                self._command(command),
                shell=True,
                cwd=str(self.workspace.root),
                env=self.env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
                start_new_session=True,
            )
        except subprocess.TimeoutExpired as exc:
            return self.finish(exc.stdout or b"", -1, timed_out=True)
        return self.finish(proc.stdout, proc.returncode)

    async def arun(self, command: str, timeout_seconds: int) -> CommandResult:
        proc = await asyncio.create_subprocess_shell(
            self._command(command),
            cwd=str(self.workspace.root),
            env=self.env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            data, _ = await asyncio.wait_for(proc.communicate(), timeout_seconds)
        except TimeoutError:
            _kill_group(proc)
            await proc.wait()
            return self.finish(b"", -1, timed_out=True)
        except asyncio.CancelledError:
            _kill_group(proc)
            raise
        return self.finish(data, proc.returncode or 0)


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    import signal

    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, AttributeError):
        proc.kill()


class WindowsShellRunner(ShellRunner):
    """受限令牌 + Job Object；初始化失败时退回普通执行。"""

    def __init__(self, workspace: Workspace, *, enabled: bool, **kw) -> None:
        super().__init__(workspace, **kw)
        self._sandbox = None
        if enabled:
            try:
                from .windows_sandbox import WindowsSandbox

                self._sandbox = WindowsSandbox(workspace.root)
                self._sandbox.prepare()
            except Exception as exc:  # noqa: BLE001 - 沙箱不可用时降级并报告
                log.error("windows shell sandbox unavailable, running unsandboxed: %s", exc)
                self._sandbox = None
        self.sandboxed = self._sandbox is not None

    def run(self, command: str, timeout_seconds: int) -> CommandResult:
        if self._sandbox is None:
            return _plain_windows_run(self, command, timeout_seconds)
        proc = self._sandbox.spawn(command, cwd=str(self.workspace.root), env=self.env())
        raw, code, timed_out = proc.communicate(timeout_seconds)
        return self.finish(raw, code, timed_out)

    async def arun(self, command: str, timeout_seconds: int) -> CommandResult:
        if self._sandbox is None:
            return await asyncio.to_thread(_plain_windows_run, self, command, timeout_seconds)
        proc = self._sandbox.spawn(command, cwd=str(self.workspace.root), env=self.env())
        try:
            raw, code, timed_out = await asyncio.to_thread(proc.communicate, timeout_seconds)
        except asyncio.CancelledError:
            proc.kill()
            raise
        return self.finish(raw, code, timed_out)


def _plain_windows_run(runner: ShellRunner, command: str, timeout_seconds: int) -> CommandResult:
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(runner.workspace.root),
            env=runner.env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return runner.finish(exc.stdout or b"", -1, timed_out=True)
    return runner.finish(proc.stdout, proc.returncode)


def make_shell_runner(
    workspace: Workspace, *, enabled: bool = True, max_output_chars: int = DEFAULT_MAX_OUTPUT
) -> ShellRunner:
    if sys.platform == "win32":
        return WindowsShellRunner(workspace, enabled=enabled, max_output_chars=max_output_chars)
    return PosixShellRunner(workspace, enabled=enabled, max_output_chars=max_output_chars)
