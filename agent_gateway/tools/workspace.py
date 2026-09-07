"""workspace-write：写入限定在会话目录内，读取不限。"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

MAC_WRITABLE_EXTRA = ("/private/tmp", "/tmp", "/dev")


class WorkspaceViolation(PermissionError):
    pass


class Workspace:
    def __init__(self, root: str) -> None:
        self.root = Path(root).expanduser().resolve()

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
    def shell_enforced(self) -> bool:
        return sys.platform == "darwin" and shutil.which("sandbox-exec") is not None

    def sandbox_command(self, command: str) -> tuple[str, bool]:
        if not self.shell_enforced:
            return command, False
        tmp = os.path.realpath(tempfile.gettempdir())
        allowed = [str(self.root), tmp, *MAC_WRITABLE_EXTRA]
        subpaths = " ".join(f'(subpath "{p}")' for p in allowed)
        profile = f"(version 1)(allow default)(deny file-write*)(allow file-write* {subpaths})"
        return f"sandbox-exec -p {shlex.quote(profile)} /bin/sh -c {shlex.quote(command)}", True


def _normalize(p: Path) -> str:
    """解析 `..` 与符号链接；目标不存在时按最近的存在祖先解析。"""
    try:
        return str(p.resolve())
    except OSError:
        return str(p.absolute())
