"""Weekly report endpoint."""
from datetime import date, datetime, timedelta, timezone
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.jwt import CurrentUser, get_current_user
from app.nl_to_sql.client import get_llm_client
from app.rate_limit import limiter
from app.report.service import build_weekly_report

router = APIRouter(prefix="/report", tags=["report"])

# Each build costs ~20 BigQuery jobs plus an LLM summary, and a past week's
# numbers don't change — so cache by range. Bounded so it can't grow unchecked.
_CACHE: dict[tuple[str, str], tuple[datetime, dict]] = {}
_CACHE_TTL = timedelta(hours=6)
_CACHE_MAX = 32
_LOCK = Lock()


def _fresh(key: tuple[str, str]) -> dict | None:
    hit = _CACHE.get(key)
    if hit and datetime.now(timezone.utc) - hit[0] < _CACHE_TTL:
        return hit[1]
    return None


@router.get("/weekly")
@limiter.limit("6/minute")
def weekly(
    request: Request,  # required by slowapi's decorator — it raises without it
    start: str,
    end: str,
    _: CurrentUser = Depends(get_current_user),
):
    """Weekly report for [start, end) (ISO dates; end exclusive)."""
    try:
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
    except ValueError:
        raise HTTPException(status_code=400, detail="Dates must be YYYY-MM-DD")
    if e <= s or (e - s).days > 31:
        raise HTTPException(status_code=400, detail="Invalid date range")

    key = (s.isoformat(), e.isoformat())
    cached = _fresh(key)
    if cached:
        return cached

    with _LOCK:
        # Another request may have built it while we waited on the lock.
        cached = _fresh(key)
        if cached:
            return cached
        report = build_weekly_report(s, e, get_llm_client())
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(min(_CACHE, key=lambda k: _CACHE[k][0]), None)
        _CACHE[key] = (datetime.now(timezone.utc), report)
        return report
