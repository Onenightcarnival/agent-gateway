import sys
from pathlib import Path

import pytest

from agent_gateway.tools.workspace import Workspace, WorkspaceViolation


def test_contains_and_resolve(tmp_path: Path):
    ws = Workspace(str(tmp_path / "ws"))
    assert ws.root == (tmp_path / "ws").resolve()
    assert ws.contains("a.txt")
    assert ws.contains("sub/deep/b.txt")
    assert ws.contains(str(ws.root / "c.txt"))
    assert not ws.contains("../escape.txt")
    assert not ws.contains(str(tmp_path / "outside.txt"))
    assert not ws.contains("/etc/hosts")


def test_check_write_raises_outside(tmp_path: Path):
    ws = Workspace(str(tmp_path / "ws"))
    assert ws.check_write("ok.txt") == ws.root / "ok.txt"
    with pytest.raises(WorkspaceViolation, match="outside workspace"):
        ws.check_write("../no.txt")
    with pytest.raises(WorkspaceViolation):
        ws.check_write(str(tmp_path / "no.txt"))


def test_sandbox_command_by_platform(tmp_path: Path):
    import os
    import tempfile

    ws = Workspace(str(tmp_path))
    wrapped, enforced = ws.sandbox_command("echo hi")
    if sys.platform == "darwin":
        assert enforced is True
        assert wrapped.startswith("sandbox-exec -p ")
        assert str(ws.root) in wrapped
        assert os.path.realpath(tempfile.gettempdir()) in wrapped
        assert '/var/folders"' not in wrapped
        assert "echo hi" in wrapped
    else:
        assert enforced is False
        assert wrapped == "echo hi"
