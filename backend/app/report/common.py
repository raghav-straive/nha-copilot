"""Scheme-agnostic helpers for building a weekly report.

The aggregates themselves are domain-specific and live in each pack's report
module; everything here — concurrency, decimal cleanup, period predicates,
week-over-week deltas — is shared so both domains get the same behaviour (and
the same concurrency fix) for free.
"""
from __future__ import annotations

import decimal
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date

logger = logging.getLogger(__name__)

# BigQuery jobs are I/O-bound (submit, then wait), so threads are the right tool
# here. Kept modest to stay well inside per-project concurrent-query limits.
MAX_WORKERS = 8


def num(x) -> float:
    if x is None:
        return 0.0
    if isinstance(x, decimal.Decimal):
        return float(x)
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def clean(rows: list[dict]) -> list[dict]:
    return [
        {k: (float(v) if isinstance(v, decimal.Decimal) else v) for k, v in r.items()}
        for r in rows
    ]


def rows(bq, sql: str) -> list[dict]:
    res = bq.run_select(sql)
    if not res.ok:
        logger.warning("Report query failed: %s", res.error)
        return []
    return clean(res.rows)


def rows_many(bq, queries: dict[str, str]) -> dict[str, list[dict]]:
    """Run independent aggregates concurrently.

    Each query still degrades to [] on failure exactly as `rows` does, so one
    bad query cannot take down the whole report. Sequentially this was ~20
    round-trips at 1-2s of job latency each, putting the report at 20-40s.
    """
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {k: pool.submit(rows, bq, sql) for k, sql in queries.items()}
        out: dict[str, list[dict]] = {}
        for k, f in futures.items():
            try:
                out[k] = f.result()
            except Exception:  # noqa: BLE001 - keep the rest of the report
                logger.warning("Report query %r raised", k, exc_info=True)
                out[k] = []
        return out


def period(col: str, start: date, end: date, datetime_col: bool = False) -> str:
    expr = f"DATE({col})" if datetime_col else col
    return f"{expr} >= DATE('{start.isoformat()}') AND {expr} < DATE('{end.isoformat()}')"


def delta(cur: float, prev: float) -> dict:
    change = cur - prev
    pct = (change / prev * 100) if prev else None
    return {
        "prev": round(prev, 2),
        "change": round(change, 2),
        "pct": round(pct, 1) if pct is not None else None,
    }


def scalar_of(res: dict[str, list[dict]], key: str) -> float:
    rs = res.get(key) or []
    return num(rs[0].get("v")) if rs else 0.0
