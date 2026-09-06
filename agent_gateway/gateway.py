"""运行时装配：设置、引擎、会话仓库、事件总线、交互队列、进行中的轮次。"""

from __future__ import annotations

import asyncio
import logging

from .config import Settings
from .core.events import EventBus
from .core.interaction import InteractionHub
from .core.models import Session
from .core.store import SessionStore
from .core.turn import TurnOutcome, TurnRunner
from .engines.base import AgentEngine, EngineInfo, ModelRef, SessionContext

log = logging.getLogger(__name__)


class Gateway:
    def __init__(self, settings: Settings, engine: AgentEngine) -> None:
        self.settings = settings
        self.engine = engine
        self.store = SessionStore()
        self.bus = EventBus()
        self.hub = InteractionHub(self.bus, settings)
        self.engine_info: EngineInfo | None = None
        self._turns: dict[str, tuple[TurnRunner, asyncio.Task[TurnOutcome]]] = {}

    async def startup(self) -> None:
        self.engine_info = await self.engine.start()
        log.info(
            "engine=%s model=%s tools=%d skills=%d",
            self.engine_info.name,
            self.engine_info.model,
            len(self.engine_info.tool_names),
            len(self.engine_info.skill_names),
        )

    async def shutdown(self) -> None:
        for session_id in list(self._turns):
            await self.abort_turn(session_id)
        await self.engine.stop()

    async def create_session(self, directory: str, title: str | None) -> dict:
        session = self.store.create(directory, title)
        await self.engine.open_session(
            SessionContext(id=session.id, directory=session.directory, title=session.title)
        )
        return session.summary()

    async def delete_session(self, session_id: str) -> None:
        await self.abort_turn(session_id)
        self.hub.drop_session(session_id)
        await self.engine.close_session(session_id)
        self.store.delete(session_id)

    async def run_turn(self, session: Session, prompt: str, model: ModelRef) -> TurnOutcome:
        runner = TurnRunner(
            session=session,
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
