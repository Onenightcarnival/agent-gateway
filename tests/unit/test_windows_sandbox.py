"""Windows 受限令牌沙箱。非 win32 平台只跑纯函数用例。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_gateway.tools import windows_sandbox as ws
from agent_gateway.tools.workspace import Workspace

win_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_sid_derivation_is_deterministic_and_well_formed(tmp_path: Path):
    a = ws.workspace_sid(str(tmp_path))
    b = ws.workspace_sid(str(tmp_path))
    t = ws.temp_sid(str(tmp_path))
    assert a == b
    assert a != t
    assert a.startswith("S-1-4-") and t.startswith("S-1-4-")
    parts = [int(x) for x in a.split("-")[3:]]
    assert len(parts) == 2 and all(0 <= p < 2**30 for p in parts)
    assert t.endswith("-1") and len(t.split("-")) == 6
    assert ws.workspace_sid(str(tmp_path / "other")) != a


def test_available_reports_platform():
    ok, reason = ws.available()
    if sys.platform == "win32":
        assert ok, reason
    else:
        assert not ok
        assert "win32" in reason


@pytest.fixture
def sandbox_ws(tmp_path: Path) -> Workspace:
    root = tmp_path / "ws"
    root.mkdir()
    return Workspace(str(root))


@win_only
def test_health_style_detail(sandbox_ws: Workspace):
    assert sandbox_ws.shell_enforced
    assert "windows-restricted-token" in sandbox_ws.shell_detail


@win_only
def test_grant_write_is_idempotent(tmp_path: Path):
    d = tmp_path / "acl"
    d.mkdir()
    sid = ws.workspace_sid(str(d))
    assert ws.grant_write(str(d), sid) is True
    assert ws.grant_write(str(d), sid) is False


@win_only
def test_write_inside_workspace_succeeds(sandbox_ws: Workspace):
    r = sandbox_ws.run("echo hi > inside.txt", timeout=30)
    assert r.exit_code == 0, r.output
    assert (sandbox_ws.root / "inside.txt").read_text().strip() == "hi"
    r = sandbox_ws.run("mkdir sub && echo x > sub\\nested.txt", timeout=30)
    assert r.exit_code == 0, r.output
    assert (sandbox_ws.root / "sub" / "nested.txt").exists()


@win_only
def test_write_outside_workspace_is_denied(sandbox_ws: Workspace, tmp_path: Path):
    outside = tmp_path / "outside.txt"
    r = sandbox_ws.run(f'echo hi > "{outside}"', timeout=30)
    assert r.exit_code != 0
    assert not outside.exists()
    assert "denied" in r.output.lower() or "拒绝" in r.output
    home_probe = Path.home() / "agent-gateway-sandbox-probe.txt"
    r = sandbox_ws.run(f'echo hi > "{home_probe}"', timeout=30)
    assert not home_probe.exists()
    assert r.exit_code != 0


@win_only
def test_read_outside_workspace_is_allowed(sandbox_ws: Workspace, tmp_path: Path):
    src = tmp_path / "readable.txt"
    src.write_text("secret-42", encoding="utf-8")
    r = sandbox_ws.run(f'type "{src}"', timeout=30)
    assert r.exit_code == 0, r.output
    assert "secret-42" in r.output


@win_only
def test_private_temp_is_writable_and_isolated(sandbox_ws: Workspace):
    r = sandbox_ws.run('echo hi > "%TMP%\\scratch.txt" && echo %TMP%', timeout=30)
    assert r.exit_code == 0, r.output
    tmp_dir = Path(r.output.strip().splitlines()[-1])
    assert (tmp_dir / "scratch.txt").exists()
    assert tmp_dir != Path(__import__("tempfile").gettempdir())
    assert "agent-gateway-sandbox" in str(tmp_dir)


@win_only
def test_nested_subprocess_with_pipes_works(sandbox_ws: Workspace):
    code = "import subprocess; print(subprocess.run(['cmd','/c','echo nested'], capture_output=True, text=True).stdout)"
    r = sandbox_ws.run(f'"{sys.executable}" -c "{code}"', timeout=60)
    assert r.exit_code == 0, r.output
    assert "nested" in r.output


@win_only
def test_powershell_runs(sandbox_ws: Workspace):
    r = sandbox_ws.run('powershell -NoProfile -Command "Write-Output ps-ok"', timeout=60)
    assert r.exit_code == 0, r.output
    assert "ps-ok" in r.output


@win_only
def test_timeout_and_kill(sandbox_ws: Workspace):
    r = sandbox_ws.run(f'"{sys.executable}" -c "import time; time.sleep(30)"', timeout=2)
    assert r.timed_out
    proc = sandbox_ws.spawn(f'"{sys.executable}" -c "import time; time.sleep(30)"')
    proc.kill()
    assert proc.wait(10).exit_code != 0
