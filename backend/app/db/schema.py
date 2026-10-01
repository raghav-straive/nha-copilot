"""Live schema introspection.

Fetches the ACTUAL column names + types of the active domain's tables from
BigQuery INFORMATION_SCHEMA at startup and formats them for injection into the
system prompt. This keeps the LLM's type knowledge correct regardless of how the
tables were typed on load. Ground truth beats a hand-maintained schema — and it
is why retargeting to another scheme gets correct column types for free.
"""
from __future__ import annotations

import logging

from app.config import get_settings
from app.domains import get_domain

logger = logging.getLogger(__name__)

_cache: dict[str, list[tuple[str, str]]] | None = None


def load_schemas(force: bool = False) -> dict[str, list[tuple[str, str]]]:
    """Fetch (column, type) lists for every table in the active domain. Cached.
    Safe to call anytime; returns {} if BigQuery is unreachable (e.g. offline
    unit tests).

    One query covering every table, not one query per table: this runs on the
    startup path, and nine sequential round-trips at ~1-2s of job latency each
    delayed readiness by 10-20s for data that arrives in a single scan.
    """
    global _cache
    if _cache is not None and not force:
        return _cache

    from app.db.bigquery_client import get_bigquery_client

    s = get_settings()
    bq = get_bigquery_client()
    table_map = s.table_map
    # table name -> our key. Two keys could in principle point at the same
    # table, so map to a list.
    key_by_table: dict[str, list[str]] = {}
    for key, table in table_map.items():
        key_by_table.setdefault(table, []).append(key)

    names = ", ".join(f"'{t}'" for t in sorted(key_by_table))
    sql = (
        f"SELECT table_name, column_name, data_type "
        f"FROM `{s.gcp_project}.{s.bq_dataset}`.INFORMATION_SCHEMA.COLUMNS "
        f"WHERE table_name IN ({names}) "
        f"ORDER BY table_name, ordinal_position"
    )
    res = bq.run_select(sql)
    if not res.ok:
        logger.warning("Schema fetch failed: %s", res.error)
        return _cache or {}

    out: dict[str, list[tuple[str, str]]] = {}
    for row in res.rows:
        for key in key_by_table.get(row["table_name"], ()):
            out.setdefault(key, []).append((row["column_name"], row["data_type"]))

    missing = [t for t in key_by_table if not any(k in out for k in key_by_table[t])]
    if missing:
        logger.warning("No schema rows returned for: %s", ", ".join(sorted(missing)))
    _cache = out
    return out


def get_schema_text() -> str:
    """Authoritative-types block for the system prompt. Empty string if not
    loaded (so offline tests build a prompt without hitting BigQuery)."""
    if not _cache:
        return ""
    s = get_settings()
    pack = get_domain()
    lines = [
        "",
        "---",
        "## AUTHORITATIVE COLUMN TYPES (override any type stated above)",
        "These are the real BigQuery types. Honour them exactly:",
        "- Compare STRING flags (e.g. active = 't') with quoted strings.",
        "- Filter DATE columns with DATE('YYYY-MM-DD'); wrap DATETIME columns "
        "with DATE(col) for day-level comparisons.",
        "- INT64/NUMERIC/FLOAT64 are numeric; STRING needs quotes.",
        "",
    ]
    for key in s.table_map:
        if key in _cache:
            label = pack.label_for(key)
            cols = ", ".join(f"{c} {t}" for c, t in _cache[key])
            lines.append(f"### {label} — `{s.table_ref(key)}`")
            lines.append(cols)
            lines.append("")
    return "\n".join(lines)
