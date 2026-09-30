"""Explorer: proactively surface interesting trends/patterns.

A (stronger) model proposes diverse, non-obvious analytical questions; each is run
through the normal NL-to-SQL pipeline to produce a chart + insight card. Results
are cached per role so the tab loads instantly after the first generation.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import logging
from threading import Lock

from app.nl_to_sql.client import get_explorer_llm
from app.nl_to_sql.pipeline import run_turn
from app.query_log.logger import log_query

logger = logging.getLogger(__name__)

_CACHE: dict[str, dict] = {}
_TTL_SECONDS = 6 * 3600
_LOCK = Lock()

_PROPOSE_SYSTEM = (
    "You are a data-exploration assistant for India's ABDM (Ayushman Bharat "
    "Digital Mission) adoption analytics. Propose diverse, insightful, NON-OBVIOUS "
    "analytical questions an ABDM/NHA official would find worth investigating — "
    "each answerable by a single aggregate query over the ABDM data. Cover "
    "different angles across the set: ABHA (health ID) creation, facility "
    "registration (by ownership Government/Private and facility type), health-"
    "professional registration (doctor/nurse/pharmacist), health-record linking "
    "volumes and document types, Scan & Share and Scan & Pay transaction volumes "
    "and payment amounts/status, bridge/software-vendor adoption, and geography "
    "(states/districts). Prefer questions that reveal concentration, imbalance, "
    "leaders/laggards, or gaps. Avoid anything needing data outside these tables "
    "(no budgets). Keep each question concrete and self-contained. Return JSON: "
    '{"insights":[{"title":"short catchy title","question":"natural-language '
    'question to ask the analytics tool","why":"one line on why it matters"}]}'
)


def _jsonable(obj):
    if isinstance(obj, list):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    return obj


def _propose(n: int) -> list[dict]:
    llm = get_explorer_llm()
    try:
        out = llm.generate_json(_PROPOSE_SYSTEM, f"Propose {n} questions.")
    except Exception:  # noqa: BLE001
        logger.warning("Explorer proposal failed", exc_info=True)
        return []
    items = out.get("insights") or out.get("questions") or []
    result = []
    for it in items:
        if isinstance(it, dict) and str(it.get("question") or "").strip():
            result.append(
                {
                    "title": str(it.get("title") or "").strip() or "Insight",
                    "question": str(it["question"]).strip(),
                    "why": str(it.get("why") or "").strip(),
                }
            )
    return result


def _cached(role: str) -> dict | None:
    c = _CACHE.get(role)
    if not c:
        return None
    age = (
        _dt.datetime.now(_dt.timezone.utc)
        - _dt.datetime.fromisoformat(c["generated_at"])
    ).total_seconds()
    return c if age < _TTL_SECONDS else None


def generate_insights(role: str, want: int = 6, force: bool = False) -> dict:
    if not force:
        hit = _cached(role)
        if hit:
            return hit
    # Serialise builds. A cold Explorer tab costs ~10 LLM calls plus a BigQuery
    # job per card; without this, every concurrent opener pays the full price.
    with _LOCK:
        if not force:
            hit = _cached(role)
            if hit:
                return hit
        return _build(role, want)


def _build(role: str, want: int) -> dict:
    now = _dt.datetime.now(_dt.timezone.utc)
    proposals = _propose(want + 4)
    cards: list[dict] = []
    for pr in proposals:
        if len(cards) >= want:
            break
        try:
            res = run_turn(pr["question"], role=role)
        except Exception:  # noqa: BLE001
            logger.warning("Explorer run failed for %r", pr["question"], exc_info=True)
            continue

        # Explorer SQL is the most adventurous the system generates, so it is
        # exactly what the audit trail and GOVERNANCE.md refinement need. Logged
        # BEFORE the success filter below — failed queries are the informative ones.
        try:
            log_query(
                session_id=f"explorer:{role}",
                user_id="explorer",
                user_role=role,
                original_question=pr["question"],
                resolved_geography=res.resolved.get("geography"),
                resolved_period=res.resolved.get("period"),
                generated_sql=res.sql,
                execution_status=res.execution_status,
                error_message=res.error_message,
                row_count=len(res.rows) if res.action == "answer" else None,
                response_shown=res.answer or res.message,
            )
        except Exception:  # noqa: BLE001 - logging must never break generation
            logger.warning("Explorer query log failed", exc_info=True)

        if res.action != "answer" or not res.rows:
            continue
        analysis = res.analysis or {}
        cards.append(
            {
                "title": pr["title"],
                "question": pr["question"],
                "why": pr["why"],
                "summary": analysis.get("summary") or res.answer or "",
                "insights": analysis.get("insights") or [],
                "chart": res.chart,
                "columns": res.columns,
                "rows": _jsonable(res.rows),
                "sql": res.sql,
            }
        )

    payload = {"generated_at": now.isoformat(), "insights": cards}
    if cards:
        _CACHE[role] = payload
    return payload
