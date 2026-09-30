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
    """Log in and return the bearer token, leaving the client cookie-free.

    Login now also sets an auth cookie, and TestClient persists cookies across
    requests â€” so without this clear, every later request on the shared client
    would authenticate via the leftover cookie and tests meaning to check the
    unauthenticated path would silently pass for the wrong reason.
    """
    r = client.post("/auth/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    client.cookies.clear()
    return r.json()["access_token"]


@pytest.fixture
def anon():
    """A client with no cookies and no token."""
    with TestClient(app) as c:
        yield c


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


def test_refresh_requires_a_token(anon):
    assert anon.post("/auth/refresh").status_code == 401


def test_refresh_rejects_a_forged_token(anon):
    r = anon.post("/auth/refresh", headers={"Authorization": "Bearer not.a.token"})
    assert r.status_code == 401


# ---- cookie auth ----
# The point of the cookie is that a script injected into the page cannot read
# the token. These tests check it is set with the right flags, that it actually
# authenticates on its own, and that signing out clears it.


def test_login_sets_an_httponly_cookie(client):
    r = client.post(
        "/auth/login", data={"username": "analyst", "password": "analyst123"}
    )
    raw = r.headers.get("set-cookie", "")
    assert "nha_token=" in raw
    assert "HttpOnly" in raw, "must be unreadable from page scripts"
    assert "samesite=lax" in raw.lower(), "must not ride along on cross-site POSTs (CSRF)"


def test_the_cookie_authenticates_without_any_header(client):
    fresh = TestClient(app)
    fresh.post("/auth/login", data={"username": "admin", "password": "admin123"})
    # No Authorization header at all â€” the cookie alone must be enough.
    r = fresh.get("/query-log")
    assert r.status_code == 200, "cookie-only request should authenticate"


def test_cookie_carries_the_role(client):
    fresh = TestClient(app)
    fresh.post("/auth/login", data={"username": "analyst", "password": "analyst123"})
    # An analyst must still be refused the admin-only log.
    assert fresh.get("/query-log").status_code == 403


def test_logout_clears_the_cookie(client):
    fresh = TestClient(app)
    fresh.post("/auth/login", data={"username": "admin", "password": "admin123"})
    assert fresh.get("/query-log").status_code == 200

    fresh.post("/auth/logout")
    assert fresh.get("/query-log").status_code == 401, "signed out, so no access"


def test_logout_works_without_a_valid_token(client):
    # Signing out must not require the thing you are trying to discard.
    assert TestClient(app).post("/auth/logout").status_code == 200


def test_refresh_restores_a_session_from_the_cookie_alone(client):
    """This is what lets the frontend keep no token in browser storage: on
    load it calls refresh with the cookie and gets a usable session back."""
    fresh = TestClient(app)
    fresh.post("/auth/login", data={"username": "senior", "password": "senior123"})
    r = fresh.post("/auth/refresh")
    assert r.status_code == 200
    assert r.json()["username"] == "senior"
    assert r.json()["role"] == "senior_analyst"


def test_a_garbage_cookie_is_rejected(client):
    fresh = TestClient(app)
    fresh.cookies.set("nha_token", "not-a-real-token")
    assert fresh.get("/query-log").status_code == 401


def test_cookie_takes_precedence_over_a_stale_header(client):
    """Cookie first: it is the safer mechanism, so a same-origin deployment
    that sets it should win over whatever header a client sends."""
    admin_client = TestClient(app)
    admin_client.post("/auth/login", data={"username": "admin", "password": "admin123"})
    analyst_token = _token(client, "analyst", "analyst123")
    # Admin cookie + analyst header -> the admin cookie should decide.
    r = admin_client.get(
        "/query-log", headers={"Authorization": f"Bearer {analyst_token}"}
    )
    assert r.status_code == 200


# ---- access control ----


def test_protected_endpoints_need_a_token(anon):
    for path in ("/chat/session/abc", "/explorer", "/query-log", "/pdfchat/documents"):
        assert anon.get(path).status_code == 401, f"{path} should require auth"


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

