import asyncio
import sys
from pathlib import Path

import pytest

from agent_gateway.tools.shell import (
    PosixShellRunner,
    ShellRunner,
    WindowsShellRunner,
    make_shell_runner,
)
from agent_gateway.tools.workspace import Workspace


def test_runner_selection_by_platform(tmp_path: Path, monkeypatch):
    ws = Workspace(str(tmp_path))
    runner = make_shell_runner(ws, enabled=True)
    if sys.platform == "win32":
        assert isinstance(runner, WindowsShellRunner)
    else:
        assert isinstance(runner, PosixShellRunner)
    assert isinstance(runner, ShellRunner)


def test_runner_disabled_is_never_sandboxed(tmp_path: Path):
    runner = make_shell_runner(Workspace(str(tmp_path)), enabled=False)
    assert runner.sandboxed is False
    if sys.platform == "darwin":
        assert make_shell_runner(Workspace(str(tmp_path)), enabled=True).sandboxed is True


def test_sync_and_async_run_agree(tmp_path: Path):
    runner = make_shell_runner(Workspace(str(tmp_path)), enabled=True)
    cmd = f'"{sys.executable}" -c "import os; print(os.getcwd()); raise SystemExit(4)"'
    sync = runner.run(cmd, timeout_seconds=30)
    result = asyncio.run(runner.arun(cmd, timeout_seconds=30))
    assert sync.exit_code == 4 and result.exit_code == 4
    assert str(tmp_path.resolve()) in sync.output
    assert sync.output.strip() == result.output.strip()


def test_timeout_reports_and_kills(tmp_path: Path):
    runner = make_shell_runner(Workspace(str(tmp_path)), enabled=True)
    cmd = f'"{sys.executable}" -c "import time; time.sleep(30)"'
    result = runner.run(cmd, timeout_seconds=1)
    assert result.timed_out is True
    assert result.exit_code != 0


async def test_async_cancel_kills(tmp_path: Path):
    runner = make_shell_runner(Workspace(str(tmp_path)), enabled=True)
    cmd = f'"{sys.executable}" -c "import time; time.sleep(30)"'
    task = asyncio.create_task(runner.arun(cmd, timeout_seconds=60))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_output_truncation(tmp_path: Path):
    runner = make_shell_runner(Workspace(str(tmp_path)), enabled=True, max_output_chars=50)
    cmd = f'"{sys.executable}" -c "print(\'x\' * 500)"'
    result = runner.run(cmd, timeout_seconds=30)
    assert result.truncated is True
    assert len(result.output) < 200
