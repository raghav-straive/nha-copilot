"""FastAPI application entry point.

Wires routers, CORS, rate limiting, and startup loading of the semantic layer
and governance prompt.
"""
from __future__ import annotations

import logging

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


@app.on_event("startup")
def _startup() -> None:
    init_db()
    # Warm the semantic layer so the first query isn't slow / doesn't fail late.
    try:
        from app.semantic.geography import get_geography

        get_geography().load()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Geography preload skipped: %s", exc)
    # Fetch the live BigQuery schema so the LLM gets authoritative column types.
    try:
        from app.db.schema import load_schemas

        loaded = load_schemas()
        logger.info("Loaded live schema for: %s", list(loaded.keys()))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Schema preload skipped: %s", exc)
    logger.info("NHA Co-pilot backend ready.")


@app.get("/health")
def health():
    return {"status": "ok"}
