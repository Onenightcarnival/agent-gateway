"""会话仓库：内存工作集 + SQLite 写穿。"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from pydantic import TypeAdapter

from .models import Message, Session

_MESSAGE = TypeAdapter(Message)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    directory TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_session_seq ON messages(session_id, seq);
"""


class SessionNotFound(LookupError):
    pass


class SessionStore:
    def __init__(self, db_path: Path | None) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        target = ":memory:" if db_path is None else str(Path(db_path).expanduser().resolve())
        if db_path is not None:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(target, check_same_thread=False)
        self._db.execute("PRAGMA foreign_keys = ON")
        if db_path is not None:
            self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(SCHEMA)
        self._load()

    # ---- 装载 ----

    def _load(self) -> None:
        rows = self._db.execute(
            "SELECT id, title, directory, created_at FROM sessions ORDER BY created_at"
        ).fetchall()
        for sid, title, directory, created_at in rows:
            bodies = self._db.execute(
                "SELECT body FROM messages WHERE session_id = ? ORDER BY seq", (sid,)
            ).fetchall()
            self._sessions[sid] = Session(
                id=sid,
                title=title,
                directory=directory,
                created_at=created_at,
                status="idle",
                messages=[_MESSAGE.validate_json(b) for (b,) in bodies],
            )

    # ---- 会话 ----

    def create(self, directory: str, title: str | None = None) -> Session:
        path = Path(directory).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        session = Session(
            title=title or f"Session {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            directory=str(path),
        )
        self._sessions[session.id] = session
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO sessions (id, title, directory, created_at, status) "
                "VALUES (?,?,?,?,?)",
                (session.id, session.title, session.directory, session.created_at, session.status),
            )
        return session

    def get(self, session_id: str) -> Session:
        try:
            return self._sessions[session_id]
        except KeyError:
            raise SessionNotFound(session_id) from None

    def all(self) -> list[Session]:
        return list(self._sessions.values())

    def delete(self, session_id: str) -> Session:
        session = self.get(session_id)
        del self._sessions[session_id]
        with self._lock, self._db:
            self._db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return session

    def statuses(self) -> dict[str, dict[str, str]]:
        return {sid: {"type": s.status} for sid, s in self._sessions.items()}

    def __len__(self) -> int:
        return len(self._sessions)

    # ---- 写穿 ----

    def save_session(self, session: Session) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE sessions SET title = ?, status = ? WHERE id = ?",
                (session.title, session.status, session.id),
            )

    def save_message(self, session: Session, index: int) -> None:
        message = session.messages[index]
        body = json.dumps(message.model_dump(exclude_none=True), ensure_ascii=False)
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO messages (id, session_id, seq, role, body) VALUES (?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET seq = excluded.seq, body = excluded.body",
                (message.id, session.id, index, message.role, body),
            )

    def count_messages(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    def close(self) -> None:
        self._db.close()
