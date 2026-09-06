"""deepagents 适配器。"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from ..config import Settings
from ..tools.ask_user import make_ask_user_langchain_tool
from ..tools.http import make_async_client, make_sync_client
from ..tools.mcp_config import parse_mcp_servers, to_langchain_connection
from ..tools.permissions import DENIED_MESSAGE, PermissionGuard
from ..tools.skills import Skill, discover_skills
from .base import (
    EngineEvent,
    EngineInfo,
    HistoryMessage,
    InteractionPort,
    ModelRef,
    SessionContext,
    StepFinish,
    TextDelta,
    ToolCallEnd,
    ToolCallStart,
)

log = logging.getLogger(__name__)


class PermissionMiddleware(AgentMiddleware):
    """被拒绝的调用不会经过工具节点，其结果由 `denied` 直接送入事件流。"""

    def __init__(self, guard: PermissionGuard) -> None:
        super().__init__()
        self.guard = guard
        self.denied: list[ToolCallEnd] = []

    async def awrap_tool_call(self, request: ToolCallRequest, handler):
        call = request.tool_call
        if not await self.guard.allow(call["name"], dict(call.get("args") or {})):
            self.denied.append(
                ToolCallEnd(
                    call_id=call["id"], name=call["name"], output=DENIED_MESSAGE, is_error=True
                )
            )
            return ToolMessage(
                content=DENIED_MESSAGE, tool_call_id=call["id"], name=call["name"], status="error"
            )
        return await handler(request)


@dataclass
class _SessionState:
    context: SessionContext
    checkpointer: InMemorySaver = field(default_factory=InMemorySaver)
    history: list[HistoryMessage] = field(default_factory=list)
    agents: dict[str, tuple[Any, PermissionMiddleware]] = field(default_factory=dict)


def _chunk_text(chunk: Any) -> str:
    text = getattr(chunk, "text", "")
    if isinstance(text, str):
        return str(text)
    return text() if callable(text) else ""


def _tool_output_text(output: Any) -> str:
    """ToolMessage / Command(update={"messages": [...]}) / 任意值 → 文本。"""
    update = getattr(output, "update", None)
    if isinstance(update, dict) and update.get("messages"):
        output = update["messages"][-1]
    if isinstance(output, ToolMessage):
        output = output.content
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return _content_text(output)
    try:
        return json.dumps(output, ensure_ascii=False, default=str)
    except TypeError:
        return str(output)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)


class DeepAgentsEngine:
    name = "deepagents"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._stack = AsyncExitStack()
        self._mcp_tools: list[BaseTool] = []
        self._skills: list[Skill] = []
        self._sessions: dict[str, _SessionState] = {}

    # ---- 生命周期 ----

    async def start(self) -> EngineInfo:
        cfg = self.settings.gateway_file
        specs = parse_mcp_servers(cfg.mcp_servers)
        if specs:
            client = MultiServerMCPClient({s.name: to_langchain_connection(s) for s in specs})
            for spec in specs:
                session = await self._stack.enter_async_context(client.session(spec.name))
                tools = await load_mcp_tools(session)
                log.info("mcp server %s: %d tools", spec.name, len(tools))
                self._mcp_tools.extend(tools)
        self._skills = discover_skills(cfg.skill_dirs)
        return EngineInfo(
            name=self.name,
            model=self.settings.model_name or "(from request)",
            tool_names=[t.name for t in self._mcp_tools],
            skill_names=[s.name for s in self._skills],
        )

    async def stop(self) -> None:
        await self._stack.aclose()

    async def open_session(
        self, session: SessionContext, history: Sequence[HistoryMessage] = ()
    ) -> None:
        self._sessions[session.id] = _SessionState(context=session, history=list(history))

    async def close_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    # ---- 执行 ----

    def _build_agent(self, state: _SessionState, model_name: str, interaction: InteractionPort):
        directory = state.context.directory
        model = ChatOpenAI(
            model=model_name,
            base_url=self.settings.model_base_url,
            api_key=self.settings.model_api_key,
            streaming=True,
            http_client=make_sync_client(),
            http_async_client=make_async_client(),
        )
        backend = LocalShellBackend(root_dir=directory, virtual_mode=False, inherit_env=True)
        tools: list[Any] = [
            *self._mcp_tools,
            make_ask_user_langchain_tool(state.context.id, interaction),
        ]
        permission = PermissionMiddleware(
            PermissionGuard(state.context.id, interaction, self.settings)
        )
        skill_dirs = [str(d) for d in self.settings.gateway_file.skill_dirs if d.is_dir()]
        agent = create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=self.settings.system_prompt(directory),
            backend=backend,
            skills=skill_dirs or None,
            middleware=[permission],
            checkpointer=state.checkpointer,
        )
        if state.history:
            agent.update_state(
                {"configurable": {"thread_id": state.context.id}},
                {
                    "messages": [
                        HumanMessage(content=h.content)
                        if h.role == "user"
                        else AIMessage(content=h.content)
                        for h in state.history
                    ]
                },
            )
            state.history = []
        return agent, permission

    def _agent_for(self, state: _SessionState, model_name: str, interaction: InteractionPort):
        if model_name not in state.agents:
            state.agents[model_name] = self._build_agent(state, model_name, interaction)
        return state.agents[model_name]

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
        state = self._sessions.get(session.id) or _SessionState(context=session)
        self._sessions[session.id] = state
        agent, permission = self._agent_for(state, model_name, interaction)
        permission.denied.clear()
        config = {
            "configurable": {"thread_id": session.id},
            "recursion_limit": self.settings.max_steps * 2 + 2,
        }
        mapper = _EventMapper()
        stream = agent.astream_events(
            {"messages": [HumanMessage(content=prompt)]}, config=config, version="v2"
        )
        try:
            async for event in stream:
                for out in mapper.map(event):
                    yield out
                while permission.denied:
                    denied = permission.denied.pop(0)
                    mapper.pending.pop(denied.call_id, None)
                    yield denied
        finally:
            await stream.aclose()
        for out in mapper.flush():
            yield out


class _EventMapper:
    """astream_events(v2) → EngineEvent。只取主 agent 层级的模型与工具事件。"""

    def __init__(self) -> None:
        self.model_depth: int | None = None
        self.tool_depth: int | None = None
        self.pending: dict[str, str] = {}
        self.streamed = ""
        self.finished = False

    def map(self, ev: dict[str, Any]) -> list[EngineEvent]:
        kind = ev["event"]
        depth = len(ev.get("parent_ids") or [])
        if kind == "on_chat_model_start":
            if self.model_depth is None:
                self.model_depth = depth
            if depth == self.model_depth:
                self.streamed = ""
            return []
        if kind == "on_chat_model_stream" and depth == self.model_depth:
            text = _chunk_text(ev["data"]["chunk"])
            if text:
                self.streamed += text
                return [TextDelta(text=text)]
            return []
        if kind == "on_chat_model_end" and depth == self.model_depth:
            return self._model_end(ev["data"]["output"])
        if kind == "on_tool_start":
            if self.tool_depth is None:
                self.tool_depth = depth
            return []
        if kind == "on_tool_end" and depth == self.tool_depth:
            return [self._tool_end(ev["name"], ev["data"].get("output"))]
        if kind == "on_tool_error" and depth == self.tool_depth:
            call_id = self._pop_call_id(ev["name"], None)
            return [
                ToolCallEnd(
                    call_id=call_id,
                    name=ev["name"],
                    output=str(ev["data"].get("error")),
                    is_error=True,
                )
            ]
        return []

    def _model_end(self, message: Any) -> list[EngineEvent]:
        events: list[EngineEvent] = []
        final_text = _content_text(getattr(message, "content", ""))
        if not self.streamed and final_text:
            events.append(TextDelta(text=final_text))
        tool_calls = list(getattr(message, "tool_calls", []) or [])
        for tc in tool_calls:
            call_id = tc.get("id") or f"call_{len(self.pending)}"
            self.pending[call_id] = tc["name"]
            events.append(
                ToolCallStart(
                    call_id=call_id, name=tc["name"], arguments=dict(tc.get("args") or {})
                )
            )
        finish = "tool-calls" if tool_calls else "stop"
        self.finished = finish == "stop"
        events.append(StepFinish(finish=finish))
        return events

    def _tool_end(self, name: str, output: Any) -> ToolCallEnd:
        call_id = self._pop_call_id(name, getattr(output, "tool_call_id", None))
        is_error = getattr(output, "status", None) == "error"
        return ToolCallEnd(
            call_id=call_id, name=name, output=_tool_output_text(output), is_error=is_error
        )

    def _pop_call_id(self, name: str, call_id: str | None) -> str:
        if call_id and call_id in self.pending:
            self.pending.pop(call_id)
            return call_id
        for cid, n in self.pending.items():
            if n == name:
                self.pending.pop(cid)
                return cid
        return call_id or f"call_{name}"

    def flush(self) -> list[EngineEvent]:
        return [] if self.finished else [StepFinish(finish="stop")]


__all__ = ["DeepAgentsEngine"]
