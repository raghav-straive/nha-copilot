"""Role-based access control for generated SQL (§4.5).

The architecture calls for restricting *granularity* rather than hard-blocking.
Because the SQL is free-form against BigQuery (no row-security policies), we
enforce RBAC pragmatically by inspecting the columns a query references:

  viewer          national + state aggregates only
  analyst         + district-level
  senior_analyst  + facility-level detail
  admin           everything, plus query/error logs

If a query references columns above the caller's tier, the query is not executed;
the caller receives a scoped message explaining the access limit (the LLM can
then re-scope on the next turn). Row-level access (no aggregation on a
sensitive table) is likewise restricted below senior_analyst.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from app.domains import get_domain

logger = logging.getLogger(__name__)

ROLE_LEVELS = {"viewer": 0, "analyst": 1, "senior_analyst": 2, "admin": 3}


@dataclass
class RbacResult:
    allowed: bool
    reason: str | None = None
    # columns that triggered the block, for logging/UX
    blocked_columns: list[str] = field(default_factory=list)


def check_rbac(sql: str, role: str) -> RbacResult:
    level = ROLE_LEVELS.get(role, 0)
    if level >= ROLE_LEVELS["admin"]:
        return RbacResult(allowed=True)

    try:
        stmt = sqlglot.parse_one(sql, read="bigquery")
    except Exception:  # noqa: BLE001
        # Fail CLOSED. This is a security control, and it must not depend on the
        # validator having run: that parses with sqlglot.parse() while this uses
        # parse_one(), so the two can legitimately disagree. An unparseable
        # query must never execute on a non-admin's behalf.
        logger.warning("RBAC could not parse SQL; denying")
        return RbacResult(
            allowed=False,
            reason=(
                "I couldn't verify that query against your access level. "
                "Please rephrase."
            ),
        )

    pack = get_domain()
    referenced = {c.name.lower() for c in stmt.find_all(exp.Column) if c.name}

    facility_hits = sorted(referenced & pack.facility_tier_columns)
    district_hits = sorted(referenced & pack.district_tier_columns)

    # Row-level access: an un-aggregated SELECT against a table whose rows are
    # individual records (a claim, a patient episode). This is a tier the
    # column-name checks cannot catch — the query may name only innocuous
    # columns and still return one row per person. Aggregate-only domains
    # declare no such tables, so this is a no-op for them.
    if level < ROLE_LEVELS["senior_analyst"] and _is_row_level(stmt, pack):
        return RbacResult(
            allowed=False,
            reason=(
                "That query would return individual records. Your role has "
                "access to aggregated figures — try asking for counts or totals "
                "grouped by state, district, or time period."
            ),
        )

    # senior_analyst (2) may use facility-level columns; analyst/viewer may not.
    if facility_hits and level < ROLE_LEVELS["senior_analyst"]:
        return RbacResult(
            allowed=False,
            reason=(
                "Your role has access to aggregated data down to district level, "
                "but not individual-facility detail. Try asking for state- or "
                "district-level figures instead."
            ),
            blocked_columns=facility_hits,
        )

    # analyst (1) may use district columns; viewer may not.
    if district_hits and level < ROLE_LEVELS["analyst"]:
        return RbacResult(
            allowed=False,
            reason=(
                "Your role has access to national and state-level aggregates only. "
                "Try asking for figures at the state or national level."
            ),
            blocked_columns=district_hits,
        )

    return RbacResult(allowed=True)


def _is_row_level(stmt: exp.Expression, pack) -> bool:
    """True if the query returns individual rows from a record-grain table.

    "Aggregated" means the output projection contains an aggregate call, or the
    query groups / distinct-ifies. A bare `SELECT col, col FROM claims LIMIT 50`
    is row-level access however harmless its column list looks.
    """
    if not pack.row_level_tables:
        return False

    from app.domains import table_name

    sensitive = {table_name(k).lower() for k in pack.row_level_tables}
    touched = {
        t.name.lower() for t in stmt.find_all(exp.Table) if t.name
    }
    if not (touched & sensitive):
        return False

    # Any aggregation anywhere makes this a summary, not a record listing.
    if any(isinstance(n, exp.AggFunc) for n in stmt.walk()):
        return False
    for node in stmt.walk():
        if isinstance(node, (exp.Group, exp.Distinct)):
            return False
    return True
