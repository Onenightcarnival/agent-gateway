"""按脚本产出 EngineEvent 的测试引擎。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from agent_gateway.engines.base import (
    EngineEvent,
    EngineInfo,
    HistoryMessage,
    InteractionPort,
    ModelRef,
    Question,
    SessionContext,
    StepFinish,
    TextDelta,
)


@dataclass(frozen=True)
class Sleep:
    seconds: float


@dataclass(frozen=True)
class Raise:
    error: Exception


@dataclass(frozen=True)
class AskQuestion:
    questions: list[Question]


@dataclass(frozen=True)
class AskPermission:
    permission: str
    patterns: list[str]


ScriptItem = EngineEvent | Sleep | Raise | AskQuestion | AskPermission


@dataclass
class ScriptedEngine:
    name: str = "scripted"
    script: list[ScriptItem] = field(default_factory=list)
    prompts: list[tuple[str, str]] = field(default_factory=list)
    models: list[ModelRef] = field(default_factory=list)
    opened: list[SessionContext] = field(default_factory=list)
    histories: list[list[HistoryMessage]] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    cancelled: int = 0
    started: bool = False
    stopped: bool = False
    replies: list[Any] = field(default_factory=list)

    async def start(self) -> EngineInfo:
        self.started = True
        return EngineInfo(name=self.name, model="scripted-model", tool_names=["t1"], skill_names=[])

    async def stop(self) -> None:
        self.stopped = True

    async def open_session(
        self, session: SessionContext, history: Sequence[HistoryMessage] = ()
    ) -> None:
        self.opened.append(session)
        self.histories.append(list(history))

    async def close_session(self, session_id: str) -> None:
        self.closed.append(session_id)

    async def run(
        self,
        session: SessionContext,
        prompt: str,
        model: ModelRef,
        interaction: InteractionPort,
    ) -> AsyncIterator[EngineEvent]:
        self.prompts.append((session.id, prompt))
        self.models.append(model)
        try:
            for item in self.script:
                match item:
                    case Sleep(seconds):
                        await asyncio.sleep(seconds)
                    case Raise(error):
                        raise error
                    case AskQuestion(questions):
                        answers = await interaction.ask_question(session.id, questions)
                        self.replies.append(answers)
                        yield TextDelta(
                            text=f"answer={answers[0][0] if answers and answers[0] else ''}"
                        )
                    case AskPermission(permission, patterns):
                        reply = await interaction.ask_permission(session.id, permission, patterns)
                        self.replies.append(reply)
                        yield TextDelta(text=f"permission={reply}")
                    case _:
                        yield item
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


def plain_reply(text: str) -> list[ScriptItem]:
    return [TextDelta(text=text), StepFinish(finish="stop")]
