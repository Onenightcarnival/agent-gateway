"""内存会话仓库。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .models import Session


class SessionNotFound(LookupError):
    pass


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self, directory: str, title: str | None = None) -> Session:
        path = Path(directory).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        session = Session(
            title=title or f"Session {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            directory=str(path),
        )
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session:
        try:
            return self._sessions[session_id]
        except KeyError:
            raise SessionNotFound(session_id) from None

    def delete(self, session_id: str) -> Session:
        session = self.get(session_id)
        del self._sessions[session_id]
        return session

    def statuses(self) -> dict[str, dict[str, str]]:
        return {sid: {"type": s.status} for sid, s in self._sessions.items()}

    def __len__(self) -> int:
        return len(self._sessions)
