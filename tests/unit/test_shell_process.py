"""Workspace.spawn / run 的跨平台行为。"""

import sys
from pathlib import Path

from agent_gateway.tools.workspace import Workspace


def test_run_captures_output_and_exit_code(tmp_path: Path):
    ws = Workspace(str(tmp_path))
    r = ws.run(
        f'"{sys.executable}" -c "import os; print(os.getcwd()); raise SystemExit(3)"', timeout=30
    )
    assert str(tmp_path.resolve()) in r.output
    assert r.exit_code == 3
    assert r.timed_out is False


def test_run_timeout_kills(tmp_path: Path):
    ws = Workspace(str(tmp_path))
    r = ws.run(f'"{sys.executable}" -c "import time; time.sleep(30)"', timeout=1)
    assert r.timed_out is True
    assert r.exit_code != 0


def test_spawn_kill(tmp_path: Path):
    ws = Workspace(str(tmp_path))
    proc = ws.spawn(f'"{sys.executable}" -c "import time; time.sleep(30)"')
    proc.kill()
    assert proc.wait(10).exit_code != 0


def test_sandbox_can_be_disabled(tmp_path: Path):
    ws = Workspace(str(tmp_path), sandbox=False)
    assert ws.shell_enforced is False
    assert ws.shell_detail == "none"
    assert ws.run("echo off", timeout=10).exit_code == 0
