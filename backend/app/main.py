"""FastAPI application entry point.

Wires routers, CORS, rate limiting, and startup loading of the semantic layer
and governance prompt.
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.auth.router import router as auth_router
from app.chat.router import router as chat_router
from app.config import get_settings
from app.query_log.logger import init_db
from app.query_log.router import router as query_log_router
from app.rate_limit import limiter
from app.explorer.router import router as explorer_router
from app.pdfchat.router import router as pdfchat_router
from app.report.router import router as report_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="NHA Analytics Co-Pilot", version="0.3.0")

settings = get_settings()

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(query_log_router)
app.include_router(report_router)
app.include_router(explorer_router)
app.include_router(pdfchat_router)

# What startup actually managed to load, so /health can report readiness
# instead of asserting "ok" while every query fails on expired credentials.
_READY: dict[str, bool] = {"geography": False, "schema": False}

_DEFAULT_JWT_SECRET = "change-me-to-a-long-random-string"


def _assert_secure_config(cfg) -> None:
    """Refuse to start with prototype credentials.

    users.py builds (and bcrypt-hashes) the seed accounts at import time, and
    the only previous signal was a logger.warning nobody reads in production. A
    deployment that forgets APP_USERS and JWT_SECRET otherwise serves
    admin/admin123 signed with a key published in this repository.
    """
    problems = []
    if cfg.jwt_secret == _DEFAULT_JWT_SECRET:
        problems.append("JWT_SECRET is still the built-in default")
    elif len(cfg.jwt_secret) < 32:
        problems.append("JWT_SECRET is shorter than 32 characters")
    if not cfg.app_users.strip():
        problems.append(
            "APP_USERS is unset — the dev seed accounts (admin/admin123) would be active"
        )
    if not problems:
        return
    if os.getenv("ALLOW_INSECURE_DEV") == "1":
        for p in problems:
            logger.warning("INSECURE DEV MODE: %s", p)
        return
    raise RuntimeError(
        "Refusing to start with insecure configuration:\n  - "
        + "\n  - ".join(problems)
        + "\n\nSet these in the environment (see deploy/env.example), or export "
        "ALLOW_INSECURE_DEV=1 for local development."
    )


@app.on_event("startup")
def _startup() -> None:
    _assert_secure_config(settings)
    init_db()
    # Sessions are shared across workers via SQLite; drop stale ones at boot.
    try:
        from app.chat.session import get_session_store
        from app.chat.session import init_db as init_sessions

        init_sessions()
        get_session_store().purge_expired()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Session store init failed: %s", exc)
    # Warm the semantic layer so the first query isn't slow / doesn't fail late.
    try:
        from app.semantic.geography import get_geography

        get_geography().load()
        _READY["geography"] = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Geography preload skipped: %s", exc)
    # Fetch the live BigQuery schema so the LLM gets authoritative column types.
    try:
        from app.db.schema import load_schemas

        loaded = load_schemas()
        _READY["schema"] = bool(loaded)
        logger.info("Loaded live schema for: %s", list(loaded.keys()))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Schema preload skipped: %s", exc)
    logger.info("NHA Co-pilot backend ready.")


@app.get("/health")
def health():
    """Liveness plus a cheap readiness signal, so a monitor can't report green
    while every query fails. Deliberately free of credentials and side effects —
    no BigQuery job per health poll."""
    key_present = bool(settings.openai_api_key)
    ready = all(_READY.values()) and key_present
    return {
        "status": "ok" if ready else "degraded",
        **_READY,
        "openai_key_present": key_present,
    }
