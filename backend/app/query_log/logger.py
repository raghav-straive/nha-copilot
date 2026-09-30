"""Query logging (§5).

Stored in a local SQLite file because the BigQuery service account is read-only.
Every turn (success, rejection, or error) is logged; failed queries are the
primary input for improving GOVERNANCE.md.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from app.config import BACKEND_DIR

DB_PATH = BACKEND_DIR / "query_log.sqlite"
MAX_FETCH = 2000
_lock = Lock()


def _connect():
    # `with sqlite3.connect(...)` commits or rolls back but does NOT close the
    # connection — that leaks a handle per logged turn. `closing(...)` supplies
    # the close; entering `conn` as well keeps the transaction behaviour.
    return closing(sqlite3.connect(DB_PATH, timeout=10))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS query_log (
    query_id           TEXT PRIMARY KEY,
    session_id         TEXT,
    user_id            TEXT,
    user_role          TEXT,
    timestamp          TEXT,
    original_question  TEXT,
    resolved_geography TEXT,
    resolved_period    TEXT,
    generated_sql      TEXT,
    execution_status   TEXT,
    error_message      TEXT,
    row_count          INTEGER,
    response_shown     TEXT
);
"""


def init_db() -> None:
    with _lock, _connect() as conn, conn:
        conn.execute(_SCHEMA)


def log_query(
    *,
    session_id: str,
    user_id: str,
    user_role: str,
    original_question: str,
    resolved_geography: Any,
    resolved_period: Any,
    generated_sql: str | None,
    execution_status: str,
    error_message: str | None,
    row_count: int | None,
    response_shown: str | None,
) -> str:
    query_id = str(uuid.uuid4())
    row = (
        query_id,
        session_id,
        user_id,
        user_role,
        datetime.now(timezone.utc).isoformat(),
        original_question,
        json.dumps(resolved_geography, default=str),
        json.dumps(resolved_period, default=str),
        generated_sql,
        execution_status,
        error_message,
        row_count,
        response_shown,
    )
    with _lock, _connect() as conn, conn:
        conn.execute(
            "INSERT INTO query_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", row
        )
    return query_id


def fetch_logs(limit: int = 200) -> list[dict]:
    # Clamp: an unbounded limit turns this into a full-table scan.
    try:
        limit = max(1, min(int(limit), MAX_FETCH))
    except (TypeError, ValueError):
        limit = 200
    with _lock, _connect() as conn, conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM query_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in cur.fetchall()]
