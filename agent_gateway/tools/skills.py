"""skill 目录扫描：<root>/<name>/SKILL.md，frontmatter 含 name、description。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    directory: Path

    @property
    def file(self) -> Path:
        return self.directory / "SKILL.md"


def _frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    data = yaml.safe_load(text[3:end]) or {}
    return data if isinstance(data, dict) else {}


def discover_skills(roots: list[Path]) -> list[Skill]:
    found: dict[str, Skill] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for skill_file in sorted(root.glob("*/SKILL.md")):
            meta = _frontmatter(skill_file.read_text(encoding="utf-8"))
            name = str(meta.get("name") or skill_file.parent.name)
            found[name] = Skill(
                name=name,
                description=str(meta.get("description") or ""),
                directory=skill_file.parent.resolve(),
            )
    return list(found.values())


def skills_prompt(skills: list[Skill]) -> str:
    if not skills:
        return ""
    lines = [
        "## Skills",
        "",
        "以下 skill 提供特定任务的操作规程。任务与某个 skill 的描述匹配时，"
        "先用 read_file 读取其 SKILL.md 全文，再按其中的步骤执行；"
        "skill 目录内的脚本和资源用绝对路径访问。",
        "",
    ]
    for s in skills:
        lines.append(f"- {s.name}: {s.description}\n  SKILL.md: {s.file}")
    return "\n".join(lines) + "\n"
