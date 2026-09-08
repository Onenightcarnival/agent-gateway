import sys
import uuid
from pathlib import Path

import pytest

from agent_gateway.tools.windows_sandbox import capability_sid_string

win32 = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_capability_sid_is_persisted_and_well_formed(tmp_path: Path):
    f = tmp_path / "cap_sid"
    sid = capability_sid_string(f)
    parts = sid.split("-")
    assert parts[:4] == ["S", "1", "5", "21"]
    assert len(parts) == 8
    assert all(0 <= int(x) < 2**32 for x in parts[4:])
    assert capability_sid_string(f) == sid
    assert f.read_text(encoding="utf-8").strip() == sid


@win32
def test_sandbox_confines_writes(tmp_path: Path):
    from agent_gateway.tools.shell import make_shell_runner
    from agent_gateway.tools.workspace import Workspace

    ws = tmp_path / "ws"
    ws.mkdir()
    runner = make_shell_runner(Workspace(str(ws)), enabled=True)
    assert runner.sandboxed is True

    inside = runner.run("echo hi > inside.txt", timeout_seconds=30)
    assert inside.exit_code == 0, inside.output
    assert (ws / "inside.txt").read_text().strip() == "hi"

    temp = runner.run("echo hi > %TEMP%\\agent-gateway-sandbox-probe.txt", timeout_seconds=30)
    assert temp.exit_code == 0, temp.output

    outside = Path.home() / f".agent-gateway-sandbox-probe-{uuid.uuid4().hex}.txt"
    try:
        denied = runner.run(f'echo hi > "{outside}"', timeout_seconds=30)
        assert denied.exit_code != 0
        assert not outside.exists()
    finally:
        outside.unlink(missing_ok=True)


@win32
def test_sandbox_captures_output_and_exit_code(tmp_path: Path):
    from agent_gateway.tools.shell import make_shell_runner
    from agent_gateway.tools.workspace import Workspace

    runner = make_shell_runner(Workspace(str(tmp_path)), enabled=True)
    result = runner.run(
        f"\"{sys.executable}\" -c \"import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)\"",
        timeout_seconds=30,
    )
    assert result.exit_code == 7
    assert "out" in result.output and "err" in result.output


@win32
def test_sandbox_timeout_kills_job(tmp_path: Path):
    from agent_gateway.tools.shell import make_shell_runner
    from agent_gateway.tools.workspace import Workspace

    runner = make_shell_runner(Workspace(str(tmp_path)), enabled=True)
    result = runner.run(f'"{sys.executable}" -c "import time; time.sleep(30)"', timeout_seconds=2)
    assert result.timed_out is True
