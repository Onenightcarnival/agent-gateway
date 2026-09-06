"""运行时装配：设置、引擎、会话仓库、事件总线、交互队列、进行中的轮次。"""

from __future__ import annotations

import asyncio
import logging

from .config import Settings
from .core.events import EventBus
from .core.interaction import InteractionHub
from .core.models import AssistantMessage, Session, UserMessage
from .core.store import SessionStore
from .core.turn import TurnOutcome, TurnRunner
from .engines.base import AgentEngine, EngineInfo, HistoryMessage, ModelRef, SessionContext

log = logging.getLogger(__name__)


class Gateway:
    def __init__(self, settings: Settings, engine: AgentEngine) -> None:
        self.settings = settings
        self.engine = engine
        self.store = SessionStore(settings.db_path)
        self.bus = EventBus()
        self.hub = InteractionHub(self.bus, settings)
        self.engine_info: EngineInfo | None = None
        self.engine_error: str | None = None
        self.shutting_down = False
        self._locks: dict[str, asyncio.Lock] = {}
        self._turns: dict[str, tuple[TurnRunner, asyncio.Task[TurnOutcome]]] = {}

    @property
    def unavailable_reason(self) -> str | None:
        if self.shutting_down:
            return "gateway is shutting down"
        if self.engine_error is not None:
            return f"engine failed to start: {self.engine_error}"
        return None

    async def startup(self) -> None:
        try:
            self.engine_info = await self.engine.start()
            for session in self.store.all():
                await self.engine.open_session(_context(session), _history(session))
        except Exception as exc:  # noqa: BLE001 - 引擎不可用时网关以 503 应答
            self.engine_error = str(exc) or exc.__class__.__name__
            log.exception("engine %s failed to start", self.settings.engine)
            return
        log.info(
            "engine=%s model=%s tools=%d skills=%d",
            self.engine_info.name,
            self.engine_info.model,
            len(self.engine_info.tool_names),
            len(self.engine_info.skill_names),
        )

    async def shutdown(self) -> None:
        self.shutting_down = True
        for session_id in list(self._turns):
            await self.abort_turn(session_id)
        if self.engine_error is None:
            await self.engine.stop()
        self.store.close()

    async def create_session(self, directory: str, title: str | None) -> dict:
        session = self.store.create(directory, title)
        await self.engine.open_session(_context(session))
        return session.summary()

    async def delete_session(self, session_id: str) -> None:
        await self.abort_turn(session_id)
        self.hub.drop_session(session_id)
        self._locks.pop(session_id, None)
        await self.engine.close_session(session_id)
        self.store.delete(session_id)

    async def run_turn(self, session: Session, prompt: str, model: ModelRef) -> TurnOutcome:
        lock = self._locks.setdefault(session.id, asyncio.Lock())
        async with lock:
            return await self._run_turn(session, prompt, model)

    async def _run_turn(self, session: Session, prompt: str, model: ModelRef) -> TurnOutcome:
        runner = TurnRunner(
            session=session,
            store=self.store,
            engine=self.engine,
            bus=self.bus,
            interaction=self.hub,
            settings=self.settings,
            prompt=prompt,
            model=model,
        )
        task = asyncio.create_task(runner.run(), name=f"turn:{session.id}")
        self._turns[session.id] = (runner, task)
        task.add_done_callback(lambda _: self._turns.pop(session.id, None))
        return await asyncio.shield(task)

    async def abort_turn(self, session_id: str) -> None:
        entry = self._turns.get(session_id)
        if entry is None:
            return
        runner, task = entry
        runner.abort()
        await asyncio.shield(task)


def _context(session: Session) -> SessionContext:
    return SessionContext(id=session.id, directory=session.directory, title=session.title)


def _history(session: Session) -> list[HistoryMessage]:
    out: list[HistoryMessage] = []
    for m in session.messages:
        if isinstance(m, UserMessage):
            out.append(HistoryMessage(role="user", content=m.content))
        elif isinstance(m, AssistantMessage) and m.content.strip():
            out.append(HistoryMessage(role="assistant", content=m.content))
    return out
