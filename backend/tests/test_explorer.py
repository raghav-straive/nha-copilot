"""Explorer: caching, concurrency, audit logging, and failure handling.

Everything is faked — no LLM calls, no BigQuery, no real query log.
"""
import datetime as dt

import pytest

from app.explorer import service as es
from app.nl_to_sql.pipeline import TurnResult


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Fresh in-memory and on-disk caches, and a captured query log."""
    es._CACHE.clear()
    monkeypatch.setattr(es, "_disk_cache_path", lambda role: tmp_path / f"e_{role}.json")
    logged: list[dict] = []
    monkeypatch.setattr(es, "log_query", lambda **kw: logged.append(kw))
    return logged


def _answer(q, rows=None):
    return TurnResult(
        action="answer",
        answer=f"answer to {q}",
        sql=f"SELECT 1 -- {q}",
        columns=["state", "n"],
        rows=rows if rows is not None else [{"state": "Bihar", "n": 5}],
        execution_status="success",
        analysis={"summary": "summary", "insights": ["one", "two"]},
    )


def _proposals(n):
    return [
        {"title": f"T{i}", "question": f"question {i}", "why": f"why {i}"}
        for i in range(n)
    ]


def test_builds_the_requested_number_of_cards(monkeypatch):
    monkeypatch.setattr(es, "_propose", lambda n: _proposals(n))
    monkeypatch.setattr(es, "run_turn", lambda q, role: _answer(q))

    out = es.generate_insights("analyst", want=3)
    assert len(out["insights"]) == 3
    assert out["insights"][0]["title"] == "T0"
    assert out["insights"][0]["summary"] == "summary"


def test_cards_carry_the_sql_and_chart_fields_the_ui_needs(monkeypatch):
    monkeypatch.setattr(es, "_propose", lambda n: _proposals(n))
    monkeypatch.setattr(es, "run_turn", lambda q, role: _answer(q))

    card = es.generate_insights("analyst", want=1)["insights"][0]
    for key in ("title", "question", "why", "summary", "insights", "chart",
                "columns", "rows", "sql"):
        assert key in card


def test_questions_that_return_no_rows_are_skipped(monkeypatch):
    monkeypatch.setattr(es, "_propose", lambda n: _proposals(n))

    def sometimes(q, role):
        # Every other question comes back empty.
        n = int(q.split()[-1])
        return _answer(q) if n % 2 == 0 else _answer(q, rows=[])

    monkeypatch.setattr(es, "run_turn", sometimes)
    out = es.generate_insights("analyst", want=3)
    assert all(c["rows"] for c in out["insights"]), "empty results must not become cards"


def test_a_raising_question_does_not_break_the_build(monkeypatch):
    monkeypatch.setattr(es, "_propose", lambda n: _proposals(n))

    def flaky(q, role):
        if q.endswith("1"):
            raise RuntimeError("pipeline blew up")
        return _answer(q)

    monkeypatch.setattr(es, "run_turn", flaky)
    out = es.generate_insights("analyst", want=3)
    assert len(out["insights"]) == 3, "should top up from the spare proposals"


def test_every_question_is_logged_including_failures(monkeypatch, isolate):
    logged = isolate
    monkeypatch.setattr(es, "_propose", lambda n: _proposals(n))

    def mixed(q, role):
        if q.endswith("0"):
            return TurnResult(action="error", message="nope",
                              execution_status="error", error_message="bad column")
        return _answer(q)

    monkeypatch.setattr(es, "run_turn", mixed)
    es.generate_insights("analyst", want=2)

    statuses = {e["execution_status"] for e in logged}
    assert "error" in statuses, "failed Explorer queries are the informative ones"
    assert all(e["source"] == "explorer" for e in logged)
    assert all(e["user_role"] == "analyst" for e in logged)


def test_a_broken_logger_does_not_break_the_build(monkeypatch):
    monkeypatch.setattr(es, "_propose", lambda n: _proposals(n))
    monkeypatch.setattr(es, "run_turn", lambda q, role: _answer(q))

    def explode(**kw):
        raise RuntimeError("log db locked")

    monkeypatch.setattr(es, "log_query", explode)
    out = es.generate_insights("analyst", want=2)
    assert len(out["insights"]) == 2


def test_second_call_is_served_from_cache(monkeypatch):
    monkeypatch.setattr(es, "_propose", lambda n: _proposals(n))
    calls = {"n": 0}

    def counting(q, role):
        calls["n"] += 1
        return _answer(q)

    monkeypatch.setattr(es, "run_turn", counting)
    es.generate_insights("analyst", want=2)
    first = calls["n"]
    es.generate_insights("analyst", want=2)
    assert calls["n"] == first, "a cached tab must not re-run the pipeline"


def test_force_bypasses_the_cache(monkeypatch):
    monkeypatch.setattr(es, "_propose", lambda n: _proposals(n))
    calls = {"n": 0}
    monkeypatch.setattr(es, "run_turn",
                        lambda q, role: (calls.__setitem__("n", calls["n"] + 1), _answer(q))[1])
    es.generate_insights("analyst", want=2)
    first = calls["n"]
    es.generate_insights("analyst", want=2, force=True)
    assert calls["n"] > first


def test_roles_are_cached_separately(monkeypatch):
    monkeypatch.setattr(es, "_propose", lambda n: _proposals(n))
    seen_roles: list[str] = []
    monkeypatch.setattr(es, "run_turn",
                        lambda q, role: (seen_roles.append(role), _answer(q))[1])
    es.generate_insights("viewer", want=1)
    es.generate_insights("admin", want=1)
    # A viewer's cards must never be served to an admin or vice versa.
    assert set(seen_roles) == {"viewer", "admin"}


def test_a_fresh_process_reuses_the_on_disk_cache(monkeypatch):
    """Stands in for a second uvicorn worker: it must not regenerate."""
    monkeypatch.setattr(es, "_propose", lambda n: _proposals(n))
    calls = {"n": 0}
    monkeypatch.setattr(es, "run_turn",
                        lambda q, role: (calls.__setitem__("n", calls["n"] + 1), _answer(q))[1])

    es.generate_insights("analyst", want=2)
    built = calls["n"]
    assert built > 0

    es._CACHE.clear()  # the other worker has an empty in-memory cache
    out = es.generate_insights("analyst", want=2)
    assert calls["n"] == built, "the second worker must reuse the shared disk cache"
    assert len(out["insights"]) == 2


def test_a_stale_disk_cache_is_ignored(monkeypatch):
    monkeypatch.setattr(es, "_propose", lambda n: _proposals(n))
    monkeypatch.setattr(es, "run_turn", lambda q, role: _answer(q))
    es.generate_insights("analyst", want=1)

    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=es._TTL_SECONDS + 60)
    stale = es._load_disk_cache("analyst")
    stale["generated_at"] = old.isoformat()
    es._save_disk_cache("analyst", stale)
    es._CACHE.clear()

    assert es._cached("analyst") is None, "past its TTL, the cache must be ignored"


def test_a_corrupt_disk_cache_is_ignored(monkeypatch, tmp_path):
    es._disk_cache_path("analyst").parent.mkdir(parents=True, exist_ok=True)
    es._disk_cache_path("analyst").write_text("{not json", encoding="utf-8")
    assert es._load_disk_cache("analyst") is None
    assert es._cached("analyst") is None


def test_no_proposals_yields_an_empty_payload(monkeypatch):
    monkeypatch.setattr(es, "_propose", lambda n: [])
    monkeypatch.setattr(es, "run_turn", lambda q, role: _answer(q))
    out = es.generate_insights("analyst", want=3)
    assert out["insights"] == []
    assert "generated_at" in out
