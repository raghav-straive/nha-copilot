"""Admin-only query log endpoint (§5)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth.jwt import CurrentUser, require_admin
from app.query_log.logger import fetch_logs

router = APIRouter(tags=["admin"])


@router.get("/query-log")
def query_log(
    limit: int = 200,
    source: str | None = None,
    _: CurrentUser = Depends(require_admin),
):
    """Recent query log, newest first. `source` filters by originating feature
    (chat | explorer | pdfchat | report); omit it for everything."""
    return {"logs": fetch_logs(limit=limit, source=source)}
