"""引擎契约：网关与任意 Agent 引擎之间的唯一接口。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

FinishReason = Literal["stop", "tool-calls"]
PermissionReply = Literal["once", "always", "reject"]


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCallStart:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolCallEnd:
    call_id: str
    name: str
    output: str
    is_error: bool = False


@dataclass(frozen=True)
class StepFinish:
    finish: FinishReason


EngineEvent = TextDelta | ToolCallStart | ToolCallEnd | StepFinish


@dataclass(frozen=True)
class SessionContext:
    id: str
    directory: str
    title: str


@dataclass(frozen=True)
class HistoryMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class ModelRef:
    provider_id: str | None = None
    model_id: str | None = None


@dataclass
class EngineInfo:
    name: str
    model: str
    tool_names: list[str] = field(default_factory=list)
    skill_names: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class QuestionOption:
    label: str
    description: str = ""


@dataclass(frozen=True)
class Question:
    question: str
    options: list[QuestionOption]


class InteractionPort(Protocol):
    async def ask_question(self, session_id: str, questions: list[Question]) -> list[list[str]]: ...

    async def ask_permission(
        self, session_id: str, permission: str, patterns: list[str]
    ) -> PermissionReply: ...


class NullInteraction:
    """不询问：反问取默认项，权限一律放行。"""

    async def ask_question(self, session_id: str, questions: list[Question]) -> list[list[str]]:
        return [[q.options[0].label] if q.options else [] for q in questions]

    async def ask_permission(
        self, session_id: str, permission: str, patterns: list[str]
    ) -> PermissionReply:
        return "always"


class AgentEngine(Protocol):
    name: str

    async def start(self) -> EngineInfo: ...

    async def stop(self) -> None: ...

    async def open_session(
        self, session: SessionContext, history: Sequence[HistoryMessage] = ()
    ) -> None: ...

    async def close_session(self, session_id: str) -> None: ...

    def run(
        self,
        session: SessionContext,
        prompt: str,
        model: ModelRef,
        interaction: InteractionPort,
    ) -> AsyncIterator[EngineEvent]: ...
