"""一轮 prompt 的执行：消费 EngineEvent，维护消息与状态，推送 SSE。"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Literal

from ..config import Settings
from ..engines.base import (
    AgentEngine,
    InteractionPort,
    ModelRef,
    SessionContext,
    StepFinish,
    TextDelta,
    ToolCallEnd,
    ToolCallStart,
)
from .events import EventBus
from .models import (
    AssistantMessage,
    Session,
    StepFinishPart,
    TextPart,
    ToolCall,
    ToolMessage,
    ToolPart,
    ToolState,
    UserMessage,
    dump,
)
from .store import SessionStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnOutcome:
    kind: Literal["ok", "error", "aborted"]
    message: str = ""


class TurnRunner:
    def __init__(
        self,
        *,
        session: Session,
        store: SessionStore,
        engine: AgentEngine,
        bus: EventBus,
        interaction: InteractionPort,
        settings: Settings,
        prompt: str,
        model: ModelRef,
    ) -> None:
        self.session = session
        self.store = store
        self.engine = engine
        self.bus = bus
        self.interaction = interaction
        self.settings = settings
        self.prompt = prompt
        self.model = model
        self._consume_task: asyncio.Task[None] | None = None
        self._abort_reason: str | None = None
        self._current: AssistantMessage | None = None
        self._tool_parts: dict[str, tuple[AssistantMessage, ToolPart]] = {}

    # ---- 生命周期 ----

    async def run(self) -> TurnOutcome:
        self.session.status = "busy"
        self.store.save_session(self.session)
        self.session.messages.append(UserMessage(content=self.prompt))
        self._persist_last()
        self._publish_status()
        started = time.monotonic()
        log.info("turn start session=%s prompt=%r", self.session.id, self.prompt[:80])
        outcome = TurnOutcome("ok")
        try:
            self._consume_task = asyncio.create_task(self._consume())
            async with asyncio.timeout(self.settings.turn_timeout):
                await self._consume_task
        except TimeoutError:
            outcome = TurnOutcome("aborted", "timeout")
        except asyncio.CancelledError:
            outcome = TurnOutcome("aborted", self._abort_reason or "abort")
        except Exception as exc:  # noqa: BLE001 - 引擎异常统一收尾
            log.exception("engine failure in session %s", self.session.id)
            outcome = TurnOutcome("error", str(exc) or exc.__class__.__name__)
        self._finalize(outcome)
        log.info(
            "turn end session=%s outcome=%s %s elapsed=%.1fs messages=%d",
            self.session.id,
            outcome.kind,
            outcome.message,
            time.monotonic() - started,
            len(self.session.messages),
        )
        return outcome

    def abort(self, reason: str = "abort") -> None:
        if self._consume_task is not None and not self._consume_task.done():
            self._abort_reason = reason
            self._consume_task.cancel()

    # ---- 事件消费 ----

    async def _consume(self) -> None:
        context = SessionContext(
            id=self.session.id, directory=self.session.directory, title=self.session.title
        )
        stream = self.engine.run(context, self.prompt, self.model, self.interaction)
        try:
            async for event in stream:
                match event:
                    case TextDelta(text):
                        self._on_text(text)
                    case ToolCallStart(call_id, name, arguments):
                        self._on_tool_start(call_id, name, arguments)
                    case ToolCallEnd(call_id, name, output, is_error):
                        self._on_tool_end(call_id, name, output, is_error)
                    case StepFinish(finish):
                        self._on_step_finish(finish)
        finally:
            await stream.aclose()

    def _assistant(self) -> AssistantMessage:
        if self._current is None:
            self._current = AssistantMessage()
            self.session.messages.append(self._current)
            self._persist_last()
        return self._current

    def _on_text(self, text: str) -> None:
        msg = self._assistant()
        if msg.parts and isinstance(msg.parts[-1], TextPart):
            part = msg.parts[-1]
        else:
            part = TextPart()
            msg.parts.append(part)
        part.content += text
        msg.refresh_content()
        self._publish_part(msg, part)

    def _on_tool_start(self, call_id: str, name: str, arguments: dict) -> None:
        msg = self._assistant()
        msg.tool_calls.append(ToolCall(id=call_id, name=name, arguments=arguments))
        part = ToolPart(
            tool=name,
            call_id=call_id,
            arguments=arguments,
            state=ToolState(status="running", title=f"正在执行 {name}"),
        )
        msg.parts.append(part)
        self._tool_parts[call_id] = (msg, part)
        self._publish_part(msg, part)

    def _on_tool_end(self, call_id: str, name: str, output: str, is_error: bool) -> None:
        entry = self._tool_parts.pop(call_id, None)
        if entry is None:
            self._on_tool_start(call_id, name, {})
            entry = self._tool_parts.pop(call_id)
        msg, part = entry
        part.state = ToolState(
            status="error" if is_error else "completed",
            title=f"{name} 失败" if is_error else f"{name} 完成",
            output=output,
        )
        self._publish_part(msg, part)
        self._persist(msg)
        self.session.messages.append(
            ToolMessage(tool_call_id=call_id, tool_name=name, content=output)
        )
        self._persist_last()

    def _on_step_finish(self, finish: str) -> None:
        msg = self._assistant()
        msg.info.finish = finish  # type: ignore[assignment]
        part = StepFinishPart()
        msg.parts.append(part)
        self._publish_part(msg, part)
        self._persist(msg)
        self._current = None

    # ---- 收尾 ----

    def _finalize(self, outcome: TurnOutcome) -> None:
        for _, (msg, part) in list(self._tool_parts.items()):
            part.state = ToolState(status="error", title=f"{part.tool} 已中止")
            self._publish_part(msg, part)
        self._tool_parts.clear()

        last = self.session.messages[-1] if self.session.messages else None
        needs_final = not (isinstance(last, AssistantMessage) and last.is_final)
        if needs_final or outcome.kind != "ok":
            msg = self._current if self._current is not None else None
            if msg is None and isinstance(last, AssistantMessage) and not last.is_final:
                msg = last
            if msg is None:
                msg = AssistantMessage()
                self.session.messages.append(msg)
            if outcome.kind == "error":
                msg.info.error = outcome.message
                self._append_text(msg, f"\n[执行出错] {outcome.message}")
            elif outcome.kind == "aborted":
                msg.info.aborted = True
                if outcome.message != "abort":
                    msg.info.aborted_reason = outcome.message
            if not msg.is_final:
                msg.info.finish = "stop"
                part = StepFinishPart()
                msg.parts.append(part)
                self._publish_part(msg, part)
            self._persist(msg)
        self._current = None

        self.session.status = "idle"
        self.store.save_session(self.session)
        if outcome.kind == "error":
            self.bus.publish(
                "session.error",
                {"sessionID": self.session.id, "error": {"message": outcome.message, "data": {}}},
            )
        self._publish_status()
        self.bus.publish("session.idle", {"sessionID": self.session.id})

    def _append_text(self, msg: AssistantMessage, text: str) -> None:
        part = TextPart(content=text)
        msg.parts.append(part)
        msg.refresh_content()
        self._publish_part(msg, part)

    # ---- 持久化 ----

    def _persist_last(self) -> None:
        self.store.save_message(self.session, len(self.session.messages) - 1)

    def _persist(self, msg: AssistantMessage) -> None:
        for index, m in enumerate(self.session.messages):
            if m is msg:
                self.store.save_message(self.session, index)
                return

    # ---- 推送 ----

    def _publish_status(self) -> None:
        self.bus.publish(
            "session.status",
            {"sessionID": self.session.id, "status": {"type": self.session.status}},
        )

    def _publish_part(self, msg: AssistantMessage, part) -> None:
        self.bus.publish(
            "message.part.updated",
            {"sessionID": self.session.id, "messageID": msg.id, "part": dump(part)},
        )
