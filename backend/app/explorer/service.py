"""Explorer: proactively surface interesting trends/patterns.

A (stronger) model proposes diverse, non-obvious analytical questions; each is run
through the normal NL-to-SQL pipeline to produce a chart + insight card. Results
are cached per role so the tab loads instantly after the first generation.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from app.config import get_settings
from app.nl_to_sql.client import get_explorer_llm
from app.nl_to_sql.pipeline import run_turn
from app.query_log.logger import log_query

logger = logging.getLogger(__name__)

_CACHE: dict[str, dict] = {}
_TTL_SECONDS = 6 * 3600
_LOCK = Lock()
# Cards are independent pipeline turns; this bounds how many run at once.
_MAX_WORKERS = 6


def _disk_cache_path(role: str):
    # Persisted so a second uvicorn worker (or a restart) reuses the cards
    # instead of regenerating them. Unlike the PDF index, Explorer had no disk
    # cache at all, so every worker genuinely paid the full AI cost.
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in role) or "role"
    return get_settings().pdf_index_path / f"explorer_{safe}.json"


def _load_disk_cache(role: str) -> dict | None:
    p = _disk_cache_path(role)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - corrupt cache -> regenerate
        return None


def _save_disk_cache(role: str, payload: dict) -> None:
    p = _disk_cache_path(role)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
        tmp.replace(p)  # atomic
    except Exception:  # noqa: BLE001
        logger.warning("Could not write Explorer cache", exc_info=True)

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


def _fresh(payload: dict | None) -> dict | None:
    if not payload or not payload.get("generated_at"):
        return None
    try:
        age = (
            _dt.datetime.now(_dt.timezone.utc)
            - _dt.datetime.fromisoformat(payload["generated_at"])
        ).total_seconds()
    except (TypeError, ValueError):
        return None
    return payload if age < _TTL_SECONDS else None


def _cached(role: str) -> dict | None:
    """In-memory first, then the shared on-disk copy (which another worker or a
    previous run may have written)."""
    hit = _fresh(_CACHE.get(role))
    if hit:
        return hit
    hit = _fresh(_load_disk_cache(role))
    if hit:
        _CACHE[role] = hit  # promote so the next hit skips the file read
        return hit
    return None


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


def _run_one(pr: dict, role: str) -> dict | None:
    """Run one proposed question through the pipeline and shape it into a card.

    Returns None when the question didn't produce usable data. Always logs,
    including on failure — Explorer SQL is the most adventurous the system
    generates, so it is exactly what the audit trail and GOVERNANCE.md
    refinement need, and the failures are the informative ones.
    """
    try:
        res = run_turn(pr["question"], role=role)
    except Exception:  # noqa: BLE001
        logger.warning("Explorer run failed for %r", pr["question"], exc_info=True)
        return None

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
            source="explorer",
        )
    except Exception:  # noqa: BLE001 - logging must never break generation
        logger.warning("Explorer query log failed", exc_info=True)

    if res.action != "answer" or not res.rows:
        return None
    analysis = res.analysis or {}
    return {
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


def _build(role: str, want: int) -> dict:
    now = _dt.datetime.now(_dt.timezone.utc)
    proposals = _propose(want + 4)
    cards: list[dict] = []

    # Each card is an independent pipeline turn (2 LLM calls + a BigQuery job),
    # and they were run one after another. Running a batch concurrently cuts a
    # cold Explorer load to roughly the slowest single card.
    #
    # Deliberately batched rather than firing all `want + 4` at once: the
    # sequential version stopped as soon as it had `want` cards, so running
    # everything would add up to 4 turns of cost on every build. We run `want`,
    # then top up from the spares only if some failed.
    pending = list(proposals)
    while pending and len(cards) < want:
        batch, pending = pending[: want - len(cards)], pending[want - len(cards):]
        with ThreadPoolExecutor(max_workers=min(len(batch), _MAX_WORKERS)) as pool:
            for card in pool.map(lambda p: _run_one(p, role), batch):
                if card is not None:
                    cards.append(card)

    payload = {"generated_at": now.isoformat(), "insights": cards[:want]}
    if cards:
        _CACHE[role] = payload
        _save_disk_cache(role, payload)
    return payload


