"""工具调用前的权限检查；两个引擎共用。"""

from __future__ import annotations

import json
from typing import Any

from ..config import Settings
from ..engines.base import InteractionPort

DENIED_MESSAGE = "Permission denied by user."
EXEMPT_TOOLS = {"ask_user"}


class PermissionGuard:
    def __init__(self, session_id: str, interaction: InteractionPort, settings: Settings) -> None:
        self.session_id = session_id
        self.interaction = interaction
        self.mode = settings.permission_mode
        self.ask_tools = settings.gateway_file.permission_ask_tools
        self._always: set[str] = set()

    def needs_ask(self, tool_name: str) -> bool:
        if self.mode != "ask" or tool_name in self._always or tool_name in EXEMPT_TOOLS:
            return False
        return not self.ask_tools or tool_name in self.ask_tools

    async def allow(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        if not self.needs_ask(tool_name):
            return True
        reply = await self.interaction.ask_permission(
            self.session_id,
            f"tool.{tool_name}",
            [json.dumps(arguments, ensure_ascii=False, default=str)],
        )
        if reply == "always":
            self._always.add(tool_name)
        return reply != "reject"
