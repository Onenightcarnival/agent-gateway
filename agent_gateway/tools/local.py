"""本地工具：以会话目录为根的文件读写与命令执行。"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

MAX_OUTPUT_CHARS = 100_000


class LocalTools:
    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def resolve(self, path: str | None) -> Path:
        p = Path(path or ".").expanduser()
        return p if p.is_absolute() else self.root / p

    async def read_file(self, path: str) -> str:
        """读取文本文件内容。path 可为相对会话目录的路径或绝对路径。"""
        target = self.resolve(path)
        text = await asyncio.to_thread(target.read_text, "utf-8", "replace")
        if len(text) > MAX_OUTPUT_CHARS:
            return (
                text[:MAX_OUTPUT_CHARS] + f"\n...[truncated {len(text) - MAX_OUTPUT_CHARS} chars]"
            )
        return text

    async def write_file(self, path: str, content: str) -> str:
        """写入文本文件（覆盖），自动创建父目录。返回写入的绝对路径。"""
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_text, content, "utf-8")
        return f"wrote {target}"

    async def list_directory(self, path: str = ".") -> str:
        """列出目录内容，目录名以 / 结尾。"""
        target = self.resolve(path)
        entries = await asyncio.to_thread(lambda: sorted(target.iterdir(), key=lambda p: p.name))
        return "\n".join(e.name + ("/" if e.is_dir() else "") for e in entries) or "(empty)"

    async def run_command(self, command: str, timeout_seconds: int = 120) -> str:
        """在会话目录下用系统 shell 执行命令，返回合并的 stdout/stderr 与退出码。"""
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(self.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        try:
            data, _ = await asyncio.wait_for(proc.communicate(), timeout_seconds)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return f"[timeout after {timeout_seconds}s]"
        except asyncio.CancelledError:
            proc.kill()
            raise
        text = data.decode("utf-8", errors="replace")
        if len(text) > MAX_OUTPUT_CHARS:
            text = text[:MAX_OUTPUT_CHARS] + "\n...[truncated]"
        return f"{text}\n[exit code {proc.returncode}]"


SHELL_NAME = "cmd.exe" if sys.platform == "win32" else "/bin/sh"
