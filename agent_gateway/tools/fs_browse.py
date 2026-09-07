"""目录浏览：只列目录，供调试页面选择工作区。"""

from __future__ import annotations

import os
import string
import sys
from pathlib import Path
from typing import Any


class DirectoryNotFound(LookupError):
    pass


class DirectoryExists(ValueError):
    pass


def roots() -> list[str]:
    if sys.platform == "win32":
        return [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
    return ["/"]


def _resolve_dir(path: str | None) -> Path:
    target = Path(path).expanduser() if path else Path.home()
    try:
        target = target.resolve(strict=True)
    except (OSError, RuntimeError):
        raise DirectoryNotFound(str(target)) from None
    if not target.is_dir():
        raise DirectoryNotFound(str(target))
    return target


def list_dirs(path: str | None) -> dict[str, Any]:
    target = _resolve_dir(path)
    entries = []
    try:
        children = sorted(target.iterdir(), key=lambda p: p.name.lower())
    except PermissionError:
        children = []
    for child in children:
        if child.name.startswith("."):
            continue
        try:
            if child.is_dir():
                entries.append({"name": child.name, "path": str(child)})
        except OSError:
            continue
    parent = target.parent
    return {
        "path": str(target),
        "parent": None if parent == target else str(parent),
        "roots": roots(),
        "entries": entries,
    }


def make_dir(parent: str, name: str) -> str:
    base = _resolve_dir(parent)
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise ValueError("invalid directory name")
    target = base / name
    if target.exists():
        raise DirectoryExists(str(target))
    target.mkdir()
    return str(target)
