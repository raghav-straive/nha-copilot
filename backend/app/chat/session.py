"""Session store backed by SQLite so sessions survive across uvicorn workers.

This was previously a process-local dict, which breaks silently as soon as the
service runs with more than one worker (the deployed unit file uses --workers 2):
consecutive turns land on different processes and the conversation history
disappears, so a follow-up like "what about Andhra Pradesh?" loses its context
roughly half the time. SQLite keeps one shared copy on disk, needs no extra
infrastructure, and the query log already establishes the pattern here.

Sessions are also owner-checked on resume — see get_or_create.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

from app.config import BACKEND_DIR

DB_PATH = BACKEND_DIR / "sessions.sqlite"
SESSION_TTL_DAYS = 7
# build_user_prompt only reads the last 8 messages, so there is no reason to
# carry (or re-serialise) an unbounded history.
MAX_HISTORY = 40

_lock = Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id        TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    role              TEXT NOT NULL,
    confirmed_context TEXT NOT NULL DEFAULT '{}',
    history           TEXT NOT NULL DEFAULT '[]',
    updated_at        TEXT NOT NULL
);
"""


@dataclass
class Session:
    session_id: str
    user_id: str
    role: str
    confirmed_context: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)


def _connect():
    # `closing(...)` supplies the close; the caller also enters `conn` itself so
    # the transaction is committed. `with sqlite3.connect(...)` alone commits but
    # never closes.
    return closing(sqlite3.connect(DB_PATH, timeout=10))


def init_db() -> None:
    with _lock, _connect() as conn, conn:
        conn.execute(_SCHEMA)
        # WAL lets readers proceed during a write — needed once more than one
        # worker shares the file.
        conn.execute("PRAGMA journal_mode=WAL")


class SessionStore:
    def get_or_create(self, session_id: str | None, user_id: str, role: str) -> Session:
        if session_id:
            existing = self._read(session_id)
            if existing is not None:
                if existing.user_id == user_id:
                    return existing
                # Belongs to someone else. Never hand one user another's
                # history, and never reuse the id (that would overwrite the
                # owner's session) — issue a fresh one instead.
                session_id = None
        session = Session(
            session_id=session_id or str(uuid.uuid4()), user_id=user_id, role=role
        )
        self.save(session)
        return session

    def get(self, session_id: str) -> Session | None:
        return self._read(session_id)

    def save(self, session: Session) -> None:
        """Persist a session. Callers must call this after mutating one — unlike
        the old in-memory store, editing the object is not enough."""
        session.history = session.history[-MAX_HISTORY:]
        row = (
            session.session_id,
            session.user_id,
            session.role,
            json.dumps(session.confirmed_context, default=str),
            json.dumps(session.history, default=str),
            datetime.now(timezone.utc).isoformat(),
        )
        with _lock, _connect() as conn, conn:
            conn.execute(
                "INSERT INTO sessions VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "role=excluded.role, confirmed_context=excluded.confirmed_context, "
                "history=excluded.history, updated_at=excluded.updated_at",
                row,
            )

    def update_context(self, session: Session, resolved: dict) -> None:
        """Persist confirmed geography/period from a successful turn.

        Mutates `session` only; the caller persists via save().
        """
        geos = resolved.get("geography") or []
        if geos:
            g = geos[0]
            session.confirmed_context["state_lgd_code"] = g.get("state_code")
            session.confirmed_context["state_name"] = g.get("state_name") or g.get("name")
        period = resolved.get("period")
        if period:
            session.confirmed_context["period_start"] = period.get("start")
            session.confirmed_context["period_end"] = period.get("end")
            session.confirmed_context["period_label"] = period.get("label")

    def purge_expired(self) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=SESSION_TTL_DAYS)
        ).isoformat()
        with _lock, _connect() as conn, conn:
            return conn.execute(
                "DELETE FROM sessions WHERE updated_at < ?", (cutoff,)
            ).rowcount

    def _read(self, session_id: str) -> Session | None:
        with _lock, _connect() as conn, conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        return Session(
            session_id=row["session_id"],
            user_id=row["user_id"],
            role=row["role"],
            confirmed_context=json.loads(row["confirmed_context"]),
            history=json.loads(row["history"]),
        )


_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store
