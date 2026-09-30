"""Session store: ownership on resume, and persistence across instances.

The store is SQLite-backed so sessions survive across uvicorn workers; these
tests point it at a temp file rather than the real backend/sessions.sqlite.
"""
import importlib

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    from app.chat import session as session_mod

    monkeypatch.setattr(session_mod, "DB_PATH", tmp_path / "sessions.sqlite")
    session_mod.init_db()
    return session_mod.SessionStore()


def test_owner_can_resume_their_session(store):
    a = store.get_or_create(None, "alice", "admin")
    a.history.append({"role": "user", "content": "alice's question"})
    store.save(a)

    again = store.get_or_create(a.session_id, "alice", "admin")
    assert again.session_id == a.session_id
    assert again.history == [{"role": "user", "content": "alice's question"}]


def test_other_user_cannot_resume_someone_elses_session(store):
    a = store.get_or_create(None, "alice", "admin")
    a.history.append({"role": "user", "content": "alice's private question"})
    store.save(a)

    b = store.get_or_create(a.session_id, "bob", "viewer")
    assert b.session_id != a.session_id, "bob must not be given alice's session id"
    assert b.user_id == "bob"
    assert b.history == [], "bob must not see alice's history"


def test_resuming_someone_elses_id_does_not_overwrite_it(store):
    a = store.get_or_create(None, "alice", "admin")
    a.history.append({"role": "user", "content": "keep me"})
    store.save(a)

    store.get_or_create(a.session_id, "bob", "viewer")

    still = store.get(a.session_id)
    assert still is not None
    assert still.user_id == "alice"
    assert still.history == [{"role": "user", "content": "keep me"}]


def test_session_survives_a_new_store_instance(store, tmp_path, monkeypatch):
    # Stands in for "a second uvicorn worker handles the next turn".
    from app.chat import session as session_mod

    a = store.get_or_create(None, "alice", "analyst")
    a.history.append({"role": "user", "content": "first turn"})
    store.save(a)

    other_worker = session_mod.SessionStore()
    resumed = other_worker.get_or_create(a.session_id, "alice", "analyst")
    assert resumed.history == [{"role": "user", "content": "first turn"}]


def test_history_is_capped(store):
    from app.chat.session import MAX_HISTORY

    s = store.get_or_create(None, "alice", "admin")
    s.history.extend({"role": "user", "content": str(i)} for i in range(MAX_HISTORY + 20))
    store.save(s)

    assert len(store.get(s.session_id).history) == MAX_HISTORY
    # The most recent turns are the ones kept.
    assert store.get(s.session_id).history[-1]["content"] == str(MAX_HISTORY + 19)


def test_update_context_carries_geography_and_period(store):
    s = store.get_or_create(None, "alice", "admin")
    store.update_context(s, {
        "geography": [{"state_code": 10, "state_name": "Bihar", "name": "Bihar"}],
        "period": {"start": "2025-04-01", "end": "2025-04-08", "label": "last week"},
    })
    store.save(s)

    ctx = store.get(s.session_id).confirmed_context
    assert ctx["state_lgd_code"] == 10
    assert ctx["state_name"] == "Bihar"
    assert ctx["period_label"] == "last week"
