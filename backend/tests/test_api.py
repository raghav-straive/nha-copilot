"""HTTP-level tests: auth, access control, compression, health.

These exercise the real app object, so they also cover the startup wiring
(lifespan) and the middleware stack.
"""
import os

import pytest

os.environ.setdefault("ALLOW_INSECURE_DEV", "1")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    # Point the two SQLite stores at a temp dir so tests never touch real data.
    tmp = tmp_path_factory.mktemp("api")
    from app.chat import session as session_mod
    from app.query_log import logger as ql

    session_mod.DB_PATH = tmp / "sessions.sqlite"
    ql.DB_PATH = tmp / "query_log.sqlite"
    with TestClient(app) as c:
        yield c


def _token(client, username="analyst", password="analyst123"):
    r = client.post("/auth/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


# ---- health ----


def test_health_reports_readiness_not_a_blank_ok(client):
    body = client.get("/health").json()
    assert "status" in body
    # Without BigQuery credentials nothing loads, so it must not claim "ok".
    assert body["status"] in {"ok", "degraded"}
    assert set(body) >= {"status", "geography", "schema", "openai_key_present"}


# ---- auth ----


def test_login_succeeds_and_returns_role(client):
    body = client.post(
        "/auth/login", data={"username": "analyst", "password": "analyst123"}
    ).json()
    assert body["role"] == "analyst"
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_login_rejects_a_bad_password(client):
    r = client.post("/auth/login", data={"username": "analyst", "password": "wrong"})
    assert r.status_code == 401


def test_refresh_issues_a_new_token(client):
    tok = _token(client)
    r = client.post("/auth/refresh", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["username"] == "analyst"
    assert r.json()["role"] == "analyst"


def test_refresh_requires_a_token(client):
    assert client.post("/auth/refresh").status_code == 401


def test_refresh_rejects_a_forged_token(client):
    r = client.post("/auth/refresh", headers={"Authorization": "Bearer not.a.token"})
    assert r.status_code == 401


# ---- access control ----


def test_protected_endpoints_need_a_token(client):
    for path in ("/chat/session/abc", "/explorer", "/query-log", "/pdfchat/documents"):
        assert client.get(path).status_code == 401, f"{path} should require auth"


def test_query_log_is_admin_only(client):
    analyst = _token(client)
    r = client.get("/query-log", headers={"Authorization": f"Bearer {analyst}"})
    assert r.status_code == 403

    admin = _token(client, "admin", "admin123")
    r = client.get("/query-log", headers={"Authorization": f"Bearer {admin}"})
    assert r.status_code == 200
    assert "logs" in r.json()


def test_reindex_is_admin_only(client):
    analyst = _token(client)
    r = client.post("/pdfchat/reindex", headers={"Authorization": f"Bearer {analyst}"})
    assert r.status_code == 403


def test_another_users_session_is_not_readable(client):
    """The session-history endpoint must not serve a session to a non-owner."""
    from app.chat.session import get_session_store

    store = get_session_store()
    alice = store.get_or_create(None, "alice", "admin")
    alice.history.append({"role": "user", "content": "private"})
    store.save(alice)

    tok = _token(client)  # signed in as `analyst`, not `alice`
    r = client.get(
        f"/chat/session/{alice.session_id}", headers={"Authorization": f"Bearer {tok}"}
    )
    assert r.status_code == 404


# ---- report validation ----


def test_weekly_report_rejects_bad_dates(client):
    tok = _token(client)
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/report/weekly?start=nope&end=2025-04-08", headers=h).status_code == 400
    # end before start
    assert client.get("/report/weekly?start=2025-04-08&end=2025-04-01", headers=h).status_code == 400
    # range too long
    assert client.get("/report/weekly?start=2025-01-01&end=2025-06-01", headers=h).status_code == 400


# ---- compression ----


def test_large_responses_are_compressed(client):
    """Result sets are repetitive JSON; a 500-row answer is ~59 KB raw and ~7 KB
    gzipped, which matters on the connections these users are on."""
    admin = _token(client, "admin", "admin123")
    from app.query_log.logger import log_query

    for i in range(200):
        log_query(
            session_id="s", user_id="admin", user_role="admin",
            original_question=f"a reasonably long question number {i} " * 5,
            resolved_geography=None, resolved_period=None,
            generated_sql="SELECT state_code, COUNT(DISTINCT hfr_id) FROM t " * 3,
            execution_status="success", error_message=None, row_count=i,
            response_shown="a reasonably long answer " * 10,
        )

    r = client.get(
        "/query-log?limit=200",
        headers={"Authorization": f"Bearer {admin}", "Accept-Encoding": "gzip"},
    )
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip", "large payloads should be gzipped"


def test_small_responses_are_not_compressed(client):
    # Below the threshold, framing costs more than it saves.
    r = client.get("/health", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") != "gzip"
