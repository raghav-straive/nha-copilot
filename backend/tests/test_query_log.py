"""Query log: the `source` column, its migration, and the fetch clamp."""
import sqlite3

import pytest

from app.query_log import logger as ql


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(ql, "DB_PATH", tmp_path / "query_log.sqlite")
    ql.init_db()
    return ql.DB_PATH


def _log(**over):
    kw = dict(
        session_id="s1", user_id="alice", user_role="analyst",
        original_question="how many facilities?", resolved_geography=None,
        resolved_period=None, generated_sql="SELECT 1", execution_status="success",
        error_message=None, row_count=1, response_shown="42",
    )
    kw.update(over)
    return ql.log_query(**kw)


def test_logs_a_turn_and_reads_it_back(db):
    _log()
    logs = ql.fetch_logs()
    assert len(logs) == 1
    assert logs[0]["user_id"] == "alice"
    assert logs[0]["generated_sql"] == "SELECT 1"


def test_source_defaults_to_chat(db):
    _log()
    assert ql.fetch_logs()[0]["source"] == "chat"


def test_source_is_recorded(db):
    _log(source="explorer")
    _log(source="pdfchat")
    assert {r["source"] for r in ql.fetch_logs()} == {"explorer", "pdfchat"}


def test_unknown_source_falls_back_to_chat(db):
    # Never store an arbitrary string in a column used for filtering.
    _log(source="../../etc/passwd")
    assert ql.fetch_logs()[0]["source"] == "chat"


def test_filtering_by_source(db):
    _log(source="chat")
    _log(source="explorer")
    _log(source="explorer")
    assert len(ql.fetch_logs(source="explorer")) == 2
    assert len(ql.fetch_logs(source="chat")) == 1
    assert len(ql.fetch_logs()) == 3, "no filter returns everything"


def test_unknown_filter_value_is_ignored_rather_than_returning_nothing(db):
    _log()
    assert len(ql.fetch_logs(source="nonsense")) == 1


def test_fetch_limit_is_clamped(db):
    for _ in range(5):
        _log()
    assert len(ql.fetch_logs(limit=2)) == 2
    # An absurd limit must not become a full-table scan.
    assert len(ql.fetch_logs(limit=10_000_000)) == 5
    assert len(ql.fetch_logs(limit=0)) == 1, "clamped up to at least 1"
    assert len(ql.fetch_logs(limit="bogus")) == 5, "garbage falls back to the default"


def test_failures_are_logged_too(db):
    _log(execution_status="error", error_message="bad column", row_count=None)
    row = ql.fetch_logs()[0]
    assert row["execution_status"] == "error"
    assert row["error_message"] == "bad column"


def test_migration_adds_source_to_a_pre_existing_database(tmp_path, monkeypatch):
    """A database created before `source` existed must gain the column rather
    than breaking on the next write."""
    path = tmp_path / "old.sqlite"
    old_schema = """
    CREATE TABLE query_log (
        query_id TEXT PRIMARY KEY, session_id TEXT, user_id TEXT, user_role TEXT,
        timestamp TEXT, original_question TEXT, resolved_geography TEXT,
        resolved_period TEXT, generated_sql TEXT, execution_status TEXT,
        error_message TEXT, row_count INTEGER, response_shown TEXT
    );
    """
    with sqlite3.connect(path) as conn:
        conn.execute(old_schema)
        conn.execute(
            "INSERT INTO query_log VALUES "
            "('old-1','s','u','analyst','2025-01-01','q',NULL,NULL,'SELECT 1',"
            "'success',NULL,1,'shown')"
        )
    monkeypatch.setattr(ql, "DB_PATH", path)

    ql.init_db()  # should migrate, not raise
    _log(source="explorer")

    logs = ql.fetch_logs()
    assert len(logs) == 2
    by_id = {r["query_id"]: r for r in logs}
    assert by_id["old-1"]["source"] == "chat", "existing rows default to chat"
    assert any(r["source"] == "explorer" for r in logs)


def test_init_db_is_idempotent(db):
    _log()
    ql.init_db()
    ql.init_db()
    assert len(ql.fetch_logs()) == 1
