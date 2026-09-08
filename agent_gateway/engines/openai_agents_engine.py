"""openai-agents 适配器。"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack
from typing import Any

from agents import (
    Agent,
    ModelSettings,
    OpenAIChatCompletionsModel,
    RunContextWrapper,
    Runner,
    Tool,
    function_tool,
    set_tracing_disabled,
)
from agents.memory import SessionABC
from agents.tool import FunctionTool
from agents.tool_context import ToolContext
from openai import AsyncOpenAI

from ..config import Settings
from ..tools.ask_user import make_ask_user
from ..tools.http import make_async_client
from ..tools.local import LocalTools
from ..tools.mcp_config import parse_mcp_servers, to_openai_agents_server
from ..tools.permissions import DENIED_MESSAGE, PermissionGuard
from ..tools.skills import Skill, discover_skills, skills_prompt
from .base import (
    EngineEvent,
    EngineInfo,
    HistoryMessage,
    InteractionPort,
    ModelRef,
    NullInteraction,
    SessionContext,
    StepFinish,
    TextDelta,
    ToolCallEnd,
    ToolCallStart,
)

log = logging.getLogger(__name__)


class MemorySession(SessionABC):
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._items: list[Any] = []

    async def get_items(self, limit: int | None = None) -> list[Any]:
        return list(self._items[-limit:] if limit else self._items)

    async def add_items(self, items: list[Any]) -> None:
        self._items.extend(items)

    async def pop_item(self) -> Any | None:
        return self._items.pop() if self._items else None

    async def clear_session(self) -> None:
        self._items.clear()


class GuardedAgent(Agent):
    """在工具枚举时包一层权限检查。"""

    guard: PermissionGuard | None = None

    async def get_all_tools(self, run_context: RunContextWrapper[Any]) -> list[Tool]:
        tools = await super().get_all_tools(run_context)
        if self.guard is None:
            return tools
        return [self._wrap(t) if isinstance(t, FunctionTool) else t for t in tools]

    def _wrap(self, tool: FunctionTool) -> FunctionTool:
        guard = self.guard
        original = tool.on_invoke_tool
        name = tool.name

        async def guarded(ctx: ToolContext[Any], input_json: str) -> Any:
            try:
                args = json.loads(input_json) if input_json else {}
            except json.JSONDecodeError:
                args = {"raw": input_json}
            if not await guard.allow(name, args if isinstance(args, dict) else {"raw": args}):
                return DENIED_MESSAGE
            return await original(ctx, input_json)

        wrapped = copy.copy(tool)
        wrapped.on_invoke_tool = guarded
        return wrapped


class OpenAIAgentsEngine:
    name = "openai-agents"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._stack = AsyncExitStack()
        self._servers: list[Any] = []
        self._skills: list[Skill] = []
        self._client: AsyncOpenAI | None = None
        self._mcp_tool_names: list[str] = []
        self._sessions: dict[str, MemorySession] = {}

    # ---- 生命周期 ----

    async def start(self) -> EngineInfo:
        set_tracing_disabled(True)
        cfg = self.settings.gateway_file
        self._client = AsyncOpenAI(
            base_url=self.settings.model_base_url,
            api_key=self.settings.model_api_key,
            http_client=make_async_client(),
        )
        tool_names: list[str] = []
        for spec in parse_mcp_servers(cfg.mcp_servers):
            server = to_openai_agents_server(spec)
            await self._stack.enter_async_context(server)
            names = [t.name for t in await server.list_tools(None, None)]
            log.info("mcp server %s: %d tools", spec.name, len(names))
            tool_names.extend(names)
            self._servers.append(server)
        self._skills = discover_skills(cfg.skill_dirs)
        self._mcp_tool_names = tool_names
        return EngineInfo(
            name=self.name,
            model=self.settings.model_name or "(from request)",
            tool_names=tool_names,
            skill_names=[s.name for s in self._skills],
        )

    async def stop(self) -> None:
        await self._stack.aclose()

    async def open_session(
        self, session: SessionContext, history: Sequence[HistoryMessage] = ()
    ) -> None:
        memory = MemorySession(session.id)
        await memory.add_items([{"role": h.role, "content": h.content} for h in history])
        self._sessions[session.id] = memory

    async def close_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    # ---- 执行 ----

    def _build_agent(
        self, session: SessionContext, model_name: str, interaction: InteractionPort
    ) -> GuardedAgent:
        tools = self._session_tools(session, interaction)
        instructions = (
            self.settings.system_prompt(session.directory) + "\n" + skills_prompt(self._skills)
        )
        agent = GuardedAgent(
            name="assistant",
            instructions=instructions,
            tools=tools,
            mcp_servers=self._servers,
            model=OpenAIChatCompletionsModel(model=model_name, openai_client=self._client),
            model_settings=ModelSettings(
                parallel_tool_calls=False, extra_body=self.settings.model_extra_body or None
            ),
        )
        agent.guard = PermissionGuard(session.id, interaction, self.settings)
        return agent

    def _session_tools(self, session: SessionContext, interaction: InteractionPort) -> list[Any]:
        local = LocalTools(session.directory, shell_sandbox=self.settings.shell_sandbox)
        tools: list[Any] = [
            function_tool(local.read_file),
            function_tool(local.write_file),
            function_tool(local.list_directory),
            function_tool(local.run_command),
        ]
        if self.settings.ask_user:
            tools.append(
                function_tool(make_ask_user(session.id, interaction), name_override="ask_user")
            )
        return tools

    def tool_names_for(self, session: SessionContext) -> list[str]:
        names = [t.name for t in self._session_tools(session, NullInteraction())]
        return names + [n for n in self._mcp_tool_names]

    async def run(
        self,
        session: SessionContext,
        prompt: str,
        model: ModelRef,
        interaction: InteractionPort,
    ) -> AsyncIterator[EngineEvent]:
        model_name = self.settings.model_name or model.model_id
        if not model_name:
            raise ValueError("model is required: set MODEL_NAME or pass model.modelID")
        memory = self._sessions.setdefault(session.id, MemorySession(session.id))
        agent = self._build_agent(session, model_name, interaction)
        result = Runner.run_streamed(
            agent, prompt, session=memory, max_turns=self.settings.max_steps
        )
        mapper = _EventMapper()
        try:
            async for event in result.stream_events():
                for out in mapper.map(event):
                    yield out
        except asyncio.CancelledError:
            result.cancel()
            raise
        for out in mapper.flush():
            yield out


class _EventMapper:
    """stream_events() → EngineEvent。"""

    def __init__(self) -> None:
        self.names: dict[str, str] = {}
        self.finished = False

    def map(self, ev: Any) -> list[EngineEvent]:
        if ev.type == "raw_response_event":
            data = ev.data
            kind = getattr(data, "type", "")
            if kind == "response.output_text.delta" and data.delta:
                return [TextDelta(text=data.delta)]
            if kind == "response.completed":
                return self._response_completed(data.response)
            return []
        if ev.type == "run_item_stream_event" and ev.name == "tool_output":
            raw = ev.item.raw_item
            call_id = raw.get("call_id") if isinstance(raw, dict) else getattr(raw, "call_id", "")
            output = ev.item.output
            if not isinstance(output, str):
                output = json.dumps(output, ensure_ascii=False, default=str)
            return [
                ToolCallEnd(
                    call_id=call_id or "", name=self.names.pop(call_id, "tool"), output=output
                )
            ]
        return []

    def _response_completed(self, response: Any) -> list[EngineEvent]:
        events: list[EngineEvent] = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", "") != "function_call":
                continue
            try:
                args = json.loads(item.arguments) if item.arguments else {}
            except json.JSONDecodeError:
                args = {"raw": item.arguments}
            self.names[item.call_id] = item.name
            events.append(ToolCallStart(call_id=item.call_id, name=item.name, arguments=args))
        finish = "tool-calls" if events else "stop"
        self.finished = finish == "stop"
        events.append(StepFinish(finish=finish))
        return events

    def flush(self) -> list[EngineEvent]:
        return [] if self.finished else [StepFinish(finish="stop")]


__all__ = ["OpenAIAgentsEngine"]
