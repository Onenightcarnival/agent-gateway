"""本地工具：以会话目录为根的文件读写与命令执行。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from .workspace import Workspace, WorkspaceViolation

MAX_OUTPUT_CHARS = 100_000


class LocalTools:
    def __init__(self, root: str, *, sandbox: bool = True) -> None:
        self.workspace = Workspace(root, sandbox=sandbox)
        self.root = self.workspace.root

    def resolve(self, path: str | None) -> Path:
        return self.workspace.resolve(path)

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
        """写入文本文件（覆盖），自动创建父目录。只允许写在工作目录内。返回写入的绝对路径。"""
        try:
            target = self.workspace.check_write(path)
        except WorkspaceViolation as exc:
            return f"Error: {exc}"
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_text, content, "utf-8")
        return f"wrote {target}"

    async def list_directory(self, path: str = ".") -> str:
        """列出目录内容，目录名以 / 结尾。"""
        target = self.resolve(path)
        entries = await asyncio.to_thread(lambda: sorted(target.iterdir(), key=lambda p: p.name))
        return "\n".join(e.name + ("/" if e.is_dir() else "") for e in entries) or "(empty)"

    async def run_command(self, command: str, timeout_seconds: int = 120) -> str:
        """在会话目录下用系统 shell 执行命令，返回合并的 stdout/stderr 与退出码。

        写入只允许在工作目录内。
        """
        env = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        proc = await asyncio.to_thread(self.workspace.spawn, command, env)
        try:
            result = await asyncio.to_thread(proc.wait, timeout_seconds)
        except asyncio.CancelledError:
            proc.kill()
            raise
        if result.timed_out:
            return f"{result.output}\n[timeout after {timeout_seconds}s]"
        text = result.output
        if len(text) > MAX_OUTPUT_CHARS:
            text = text[:MAX_OUTPUT_CHARS] + "\n...[truncated]"
        return f"{text}\n[exit code {result.exit_code}]"
