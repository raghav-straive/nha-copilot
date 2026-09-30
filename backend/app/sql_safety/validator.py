"""SQL validation and safety layer (§4.4).

Sits between the LLM output and BigQuery. Independent of the system prompt: even
if the LLM ignores its instructions, this layer rejects anything that is not a
single read-only SELECT, and rejects any query that projects a PII column.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

# PII columns that must never be returned to a user. In the ABDM dataset,
# facility identity (name/id/address) is PUBLIC dashboard data and is allowed;
# the only patient-identifying column is `abha_address`, which is removed at the
# data-prep stage. This is a hard backstop in case a future refresh reintroduces
# it (or any obviously patient-identifying column). Matched case-insensitively.
PII_COLUMNS = {
    "abha_address",
}

# Statement types that are categorically rejected even if somehow parsed.
_FORBIDDEN_EXPR = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Merge,
    exp.Command,  # TRUNCATE, GRANT, COPY, etc. parse as Command
)


# Ceiling on rows returned to the caller. The BigQuery job's
# maximum_bytes_billed caps how much the query *scans*, not how much it
# *returns* — so a broad question ("list every facility") could otherwise stream
# an unbounded result set into the browser and the analysis pass.
MAX_ROWS = 5000


@dataclass
class ValidationResult:
    ok: bool
    reason: str | None = None
    pii_hit: list[str] = field(default_factory=list)


def validate_sql(sql: str) -> ValidationResult:
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        return ValidationResult(ok=False, reason="Empty query.")

    # Parse (BigQuery dialect). Reject multi-statement input.
    try:
        statements = sqlglot.parse(sql, read="bigquery")
    except Exception as exc:  # noqa: BLE001
        return ValidationResult(ok=False, reason=f"Could not parse SQL: {exc}")

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return ValidationResult(
            ok=False, reason="Only a single SELECT statement is permitted."
        )

    stmt = statements[0]

    # Root must be a SELECT (or a WITH ... SELECT / set operation over SELECTs).
    if isinstance(stmt, _FORBIDDEN_EXPR):
        return ValidationResult(
            ok=False, reason=f"Non-SELECT statement rejected: {type(stmt).__name__}."
        )
    if not _is_read_only_select(stmt):
        return ValidationResult(
            ok=False, reason="Only read-only SELECT queries are permitted."
        )

    # A star projection defeats BOTH the PII scan below and the RBAC tier check
    # in rbac_filter.py: each works by enumerating the exp.Column nodes a query
    # references, and `SELECT *` references none — so both find nothing to block
    # and a viewer could receive facility-level (or reintroduced PII) columns.
    # Requiring explicit columns closes that gap and keeps scan cost predictable.
    if _has_star_projection(stmt):
        return ValidationResult(
            ok=False,
            reason="SELECT * is not permitted; list the required columns explicitly.",
        )

    # Defence in depth: reject if any write keyword appears anywhere in the tree.
    for node in stmt.walk():
        if isinstance(node, _FORBIDDEN_EXPR):
            return ValidationResult(
                ok=False,
                reason=f"Embedded non-SELECT operation rejected: {type(node).__name__}.",
            )

    # PII column scan across all referenced column names.
    hits = sorted(
        {
            col.name.lower()
            for col in stmt.find_all(exp.Column)
            if col.name and col.name.lower() in PII_COLUMNS
        }
    )
    # Also catch aliased projections like `SELECT abha_address AS a`.
    for alias in stmt.find_all(exp.Alias):
        inner = alias.this
        if isinstance(inner, exp.Column) and inner.name.lower() in PII_COLUMNS:
            hits.append(inner.name.lower())

    hits = sorted(set(hits))
    if hits:
        return ValidationResult(
            ok=False,
            reason=f"Query references PII column(s): {', '.join(hits)}.",
            pii_hit=hits,
        )

    return ValidationResult(ok=True)


def _is_read_only_select(stmt: exp.Expression) -> bool:
    if isinstance(stmt, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        return True
    # WITH ... SELECT
    if isinstance(stmt, exp.With):
        return _is_read_only_select(stmt.this)
    # Subquery wrapper
    if isinstance(stmt, exp.Subquery):
        return _is_read_only_select(stmt.this)
    return False


def _output_selects(stmt: exp.Expression):
    """Yield the SELECT nodes whose projections actually reach the caller.

    A star deeper in the tree (inside a subquery feeding an explicit outer
    projection) is harmless: the outer projection still names its columns, so
    the PII and RBAC scans see them. Only the output level needs checking.
    """
    if isinstance(stmt, (exp.With, exp.Subquery)):
        yield from _output_selects(stmt.this)
    elif isinstance(stmt, (exp.Union, exp.Intersect, exp.Except)):
        yield from _output_selects(stmt.this)
        yield from _output_selects(stmt.expression)
    elif isinstance(stmt, exp.Select):
        yield stmt


def _has_star_projection(stmt: exp.Expression) -> bool:
    """True if the query's output projection is `*` or `alias.*`.

    This inspects Select.expressions (the projection list) ONLY. It must never
    use find_all(exp.Star): `COUNT(*)` also contains an exp.Star node, so a
    tree-wide search would reject the most common aggregate in this dataset
    (and four queries in report/service.py).
    """
    for select in _output_selects(stmt):
        for proj in select.expressions:
            if isinstance(proj, exp.Star):
                return True
            # `t.*` parses as a Column whose `this` is a Star.
            if isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star):
                return True
    return False


def enforce_row_limit(sql: str, max_rows: int = MAX_ROWS) -> str:
    """Append a LIMIT when the query has none, or a looser one than max_rows.

    Returns the SQL unchanged if it already limits to <= max_rows, or if
    anything about the rewrite is uncertain — a missing cap is better than a
    corrupted query.
    """
    try:
        stmt = sqlglot.parse_one(sql, read="bigquery")
        existing = stmt.args.get("limit")
        if existing is not None and int(existing.expression.name) <= max_rows:
            return sql
        return stmt.limit(max_rows).sql(dialect="bigquery")
    except Exception:  # noqa: BLE001 - never break a valid query just to cap it
        return sql
