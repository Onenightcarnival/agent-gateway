"""反问与权限请求的挂起、回复、超时默认。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..engines.base import PermissionReply, Question
from .events import EventBus
from .ids import new_id, now_iso


class RequestNotFound(LookupError):
    pass


@dataclass
class PendingQuestion:
    id: str
    session_id: str
    questions: list[Question]
    created_at: str = field(default_factory=now_iso)
    future: asyncio.Future[list[list[str]]] = field(default_factory=asyncio.Future)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sessionID": self.session_id,
            "questions": [
                {
                    "question": q.question,
                    "options": [
                        {"label": o.label, "description": o.description} for o in q.options
                    ],
                }
                for q in self.questions
            ],
            "created_at": self.created_at,
        }

    def default_answers(self) -> list[list[str]]:
        return [[q.options[0].label] if q.options else [] for q in self.questions]


@dataclass
class PendingPermission:
    id: str
    session_id: str
    permission: str
    patterns: list[str]
    created_at: str = field(default_factory=now_iso)
    future: asyncio.Future[PermissionReply] = field(default_factory=asyncio.Future)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sessionID": self.session_id,
            "permission": self.permission,
            "patterns": self.patterns,
            "created_at": self.created_at,
        }


class InteractionHub:
    def __init__(self, bus: EventBus, settings: Settings) -> None:
        self._bus = bus
        self._settings = settings
        self._questions: dict[str, PendingQuestion] = {}
        self._permissions: dict[str, PendingPermission] = {}

    # ---- 引擎侧 ----

    async def ask_question(self, session_id: str, questions: list[Question]) -> list[list[str]]:
        pending = PendingQuestion(id=new_id("req"), session_id=session_id, questions=questions)
        self._questions[pending.id] = pending
        props = pending.to_dict()
        props.pop("created_at")
        self._bus.publish("question.asked", props)
        try:
            return await asyncio.wait_for(pending.future, self._settings.question_timeout)
        except TimeoutError:
            return pending.default_answers()
        finally:
            self._questions.pop(pending.id, None)

    async def ask_permission(
        self, session_id: str, permission: str, patterns: list[str]
    ) -> PermissionReply:
        if self._settings.permission_mode == "auto":
            return "always"
        pending = PendingPermission(
            id=new_id("perm"), session_id=session_id, permission=permission, patterns=patterns
        )
        self._permissions[pending.id] = pending
        props = pending.to_dict()
        props.pop("created_at")
        self._bus.publish("permission.asked", props)
        try:
            return await asyncio.wait_for(pending.future, self._settings.question_timeout)
        except TimeoutError:
            return "once"
        finally:
            self._permissions.pop(pending.id, None)

    # ---- 客户端侧 ----

    def pending_questions(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self._questions.values()]

    def pending_permissions(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self._permissions.values()]

    def reply_question(self, request_id: str, answers: list[list[str]]) -> None:
        pending = self._questions.get(request_id)
        if pending is None:
            raise RequestNotFound(request_id)
        if not pending.future.done():
            pending.future.set_result(answers)

    def reply_permission(self, request_id: str, reply: PermissionReply) -> None:
        pending = self._permissions.get(request_id)
        if pending is None:
            raise RequestNotFound(request_id)
        if not pending.future.done():
            pending.future.set_result(reply)

    def drop_session(self, session_id: str) -> None:
        for p in [p for p in self._questions.values() if p.session_id == session_id]:
            self._questions.pop(p.id, None)
            p.future.cancel()
        for p in [p for p in self._permissions.values() if p.session_id == session_id]:
            self._permissions.pop(p.id, None)
            p.future.cancel()
