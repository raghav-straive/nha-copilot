"""The per-turn NL-to-SQL orchestration pipeline (§4.3).

1. Geography / period resolution (semantic layer)
2. Deterministic short-circuits (ambiguous district) -> clarify
3. Context injection + LLM SQL generation
4. SQL safety validation (SELECT-only, PII)
5. RBAC granularity check
6. Read-only execution against BigQuery (one corrective retry on error)
7. Response formatting + analysis
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

from app.db.bigquery_client import get_bigquery_client
from app.nl_to_sql.client import get_llm_client
from app.nl_to_sql.prompt_builder import build_user_prompt, load_system_prompt
from app.semantic.geography import get_geography
from app.semantic.time_resolver import get_time_resolver
from app.sql_safety.rbac_filter import check_rbac
from app.sql_safety.validator import MAX_ROWS, enforce_row_limit, validate_sql

logger = logging.getLogger(__name__)

# The data is Indian, so the clock should be too. On a UTC-hosted server,
# "how many ABHAs were created today?" asked at 03:00 IST would otherwise
# resolve to yesterday in India — a silently wrong answer.
IST = ZoneInfo("Asia/Kolkata")

# ---- Repeated-question cache ----------------------------------------------
# Officials re-ask the same things and the Explorer regenerates the same popular
# questions; each repeat otherwise costs two LLM calls plus a BigQuery job.
#
# Two rules make this safe, and both matter:
#  1. The role is part of the key. RBAC changes what a query may return, so an
#     admin's cached answer must never be served to a viewer.
#  2. The RESOLVED PERIOD is part of the key. "Today" resolves to a concrete
#     date range, so the key changes by itself when the date rolls over — a
#     stale number is worse than a slow one.
# Only successful data answers are cached; clarifications and errors are not.
_TURN_CACHE: "OrderedDict[tuple, tuple[datetime, TurnResult]]" = OrderedDict()
_TURN_TTL = timedelta(minutes=30)
_TURN_CACHE_MAX = 256
_TURN_LOCK = Lock()


def _cache_key(question: str, role: str, resolved: dict) -> tuple:
    period = resolved.get("period") or {}
    geo = tuple(
        sorted(
            (g.get("level"), g.get("lgd_code"))
            for g in (resolved.get("geography") or [])
        )
    )
    return (
        " ".join((question or "").lower().split()),
        role,
        period.get("start"),
        period.get("end"),
        geo,
    )


def _cache_get(key: tuple) -> TurnResult | None:
    with _TURN_LOCK:
        hit = _TURN_CACHE.get(key)
        if not hit:
            return None
        stamped, result = hit
        if datetime.now(timezone.utc) - stamped > _TURN_TTL:
            _TURN_CACHE.pop(key, None)
            return None
        _TURN_CACHE.move_to_end(key)  # LRU
        return result


def _cache_put(key: tuple, result: TurnResult) -> None:
    with _TURN_LOCK:
        _TURN_CACHE[key] = (datetime.now(timezone.utc), result)
        _TURN_CACHE.move_to_end(key)
        while len(_TURN_CACHE) > _TURN_CACHE_MAX:
            _TURN_CACHE.popitem(last=False)


def clear_turn_cache() -> None:
    """Drop every cached answer. For tests, and for use after a data refresh."""
    with _TURN_LOCK:
        _TURN_CACHE.clear()

FAILURE_MESSAGE = (
    "I was unable to answer that question. This has been logged for review. "
    "You could try rephrasing, or ask for a broader aggregate which I can "
    "answer reliably."
)


@dataclass
class TurnResult:
    action: str  # "answer" | "clarify" | "out_of_scope" | "chat" | "error"
    answer: str | None = None
    message: str | None = None
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    context_chips: dict[str, Any] = field(default_factory=dict)
    chart: dict[str, Any] | None = None
    options: list[str] = field(default_factory=list)  # quick-reply chips for clarify
    questions: list[dict[str, Any]] = field(default_factory=list)  # multi-question clarify form
    analysis: dict[str, Any] | None = None  # {summary, insights[], trends[]}
    resolved: dict[str, Any] = field(default_factory=dict)
    # internal, for logging
    execution_status: str = "n/a"
    error_message: str | None = None


def resolve_entities(question: str, today: date | None = None) -> dict[str, Any]:
    geo = get_geography()
    time_r = get_time_resolver()

    geo_hits = geo.detect(question)
    time_res = time_r.resolve(question, today=today)

    resolved: dict[str, Any] = {
        "geography": [
            {
                "level": m.level,
                "lgd_code": m.lgd_code,
                "name": m.name,
                "state_code": m.state_code,
                "state_name": m.state_name,
                "name_in_data": m.name_in_data,
                "status": r.status,
                "clarify": r.message,
            }
            for r in geo_hits
            for m in (r.matches or [None])
            if m is not None
        ],
        "ambiguous_geography": [
            r.message for r in geo_hits if r.status == "ambiguous"
        ],
        "period": None,
    }
    if time_res.status == "resolved":
        resolved["period"] = {
            "start": time_res.start.isoformat(),
            "end": time_res.end.isoformat(),
            "label": time_res.label,
            "outside_data_window": time_res.outside_data_window,
            "note": time_res.note,
        }
    return resolved


def run_turn(
    question: str,
    role: str,
    session_context: dict | None = None,
    today: date | None = None,
    history: list[dict] | None = None,
    use_cache: bool = True,
) -> TurnResult:
    resolved = resolve_entities(question, today=today or datetime.now(IST).date())

    # A repeat of an identical question, at the same role and the same resolved
    # period, returns the previous answer. Skipped when the turn depends on
    # conversation history, since the same words can mean different things after
    # a different preceding turn ("what about Bihar?").
    cache_key = None
    if use_cache and not history:
        cache_key = _cache_key(question, role, resolved)
        hit = _cache_get(cache_key)
        if hit is not None:
            logger.info("Turn cache hit")
            return hit

    # ---- Step 2: deterministic short-circuits (semantic layer decisions) ----
    if resolved["ambiguous_geography"]:
        # Offer the alternatives as quick-reply options.
        alt = sorted(
            {
                f"{g['name']}, {g['state_name']}"
                for g in resolved["geography"]
                if g.get("name") and g.get("state_name")
            }
        )
        return TurnResult(
            action="clarify",
            message=resolved["ambiguous_geography"][0],
            options=alt,
            resolved=resolved,
        )

    # ---- Step 3: LLM SQL generation ----
    system_prompt = load_system_prompt()
    user_prompt = build_user_prompt(question, role, session_context, resolved, history)
    llm = get_llm_client()
    try:
        gen = llm.generate_json(system_prompt, user_prompt)
    except Exception as exc:  # noqa: BLE001
        logger.exception("LLM generation failed")
        return TurnResult(
            action="error", message=FAILURE_MESSAGE, error_message=str(exc),
            resolved=resolved,
        )

    action = gen.get("action", "sql")
    chips = _build_chips(resolved, session_context)

    if action in ("clarify", "chat"):
        raw_opts = gen.get("options") or []
        opts = [str(o).strip() for o in raw_opts if str(o).strip()][:5]
        questions = []
        for q in gen.get("questions") or []:
            if isinstance(q, dict) and str(q.get("question") or "").strip():
                q_opts = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()][:6]
                questions.append({"question": str(q["question"]).strip(), "options": q_opts})
            elif isinstance(q, str) and q.strip():
                # chat examples may come as bare strings
                questions.append({"question": q.strip(), "options": []})
        # chat examples may also arrive under an "examples" key
        for ex in gen.get("examples") or []:
            if isinstance(ex, str) and ex.strip():
                questions.append({"question": ex.strip(), "options": []})
        return TurnResult(action=action, message=gen.get("message"),
                          options=opts, questions=questions,
                          resolved=resolved, context_chips=chips)
    if action == "out_of_scope":
        return TurnResult(action="out_of_scope", message=gen.get("message"),
                          resolved=resolved, context_chips=chips)

    sql = (gen.get("sql") or "").strip()
    answer_template = gen.get("answer_template") or ""
    if not sql:
        return TurnResult(action="out_of_scope",
                          message=gen.get("message") or "I couldn't form a query for that.",
                          resolved=resolved, context_chips=chips)

    # For non-English (e.g. Hindi) questions the model sometimes writes a
    # Devanagari column alias into the SQL, which BigQuery rejects with an
    # "Illegal input character" error. Catch it up front and regenerate ASCII SQL
    # (the answer text stays in the user's language; only the SQL must be ASCII).
    if not sql.isascii():
        logger.warning("Non-ASCII SQL generated; regenerating ASCII-only SQL")
        ascii_sql = _regen_ascii_sql(llm, system_prompt, user_prompt, sql)
        if ascii_sql:
            sql = ascii_sql

    # ---- Step 4: SQL safety ----
    # `result` is set early only when a corrective retry already executed.
    result = None
    v = validate_sql(sql)
    if not v.ok:
        logger.warning("SQL rejected by validator: %s", v.reason)
        # Give the model one chance to fix a mechanical rejection (most often a
        # `SELECT *` projection) exactly as we do for an execution error.
        # _retry_sql re-validates, re-checks RBAC, and executes.
        fixed = _retry_sql(llm, system_prompt, user_prompt, sql, v.reason, role)
        if fixed is None:
            return TurnResult(
                action="error", message=FAILURE_MESSAGE, sql=sql,
                execution_status="rejected", error_message=v.reason,
                resolved=resolved, context_chips=chips,
            )
        sql, result = fixed

    # ---- Step 5: RBAC ----
    if result is None:
        rbac = check_rbac(sql, role)
        if not rbac.allowed:
            return TurnResult(
                action="out_of_scope", message=rbac.reason, sql=None,
                execution_status="rbac_blocked",
                error_message=f"blocked columns: {rbac.blocked_columns}",
                resolved=resolved, context_chips=chips,
            )

    # ---- Step 6: execution (with one corrective retry on error) ----
    if result is None:
        sql = enforce_row_limit(sql)
        result = get_bigquery_client().run_select(sql)
    if not result.ok:
        logger.warning("Execution error, attempting one corrective retry: %s", result.error)
        fixed = _retry_sql(llm, system_prompt, user_prompt, sql, result.error, role)
        if fixed is not None:
            sql, result = fixed
    if not result.ok:
        logger.warning("Execution error (after retry): %s", result.error)
        return TurnResult(
            action="error", message=FAILURE_MESSAGE, sql=sql,
            execution_status="error", error_message=result.error,
            resolved=resolved, context_chips=chips,
        )

    # ---- Step 7: formatting + analysis ----
    chart = _sanitize_chart(gen.get("chart"), result.columns, result.rows)
    # Second pass: structured analysis of the ACTUAL results (skip trivial
    # single-value answers, where the template already says it).
    analysis = None
    if len(result.rows) >= 2:
        analysis = analyze_results(question, result.columns, result.rows, llm)
    answer = (
        analysis["summary"]
        if analysis and analysis.get("summary")
        else _format_answer(answer_template, result.columns, result.rows)
    )
    # The row cap is a safety limit, not a filter the user asked for — say so,
    # otherwise a truncated aggregate reads as a complete one.
    if len(result.rows) >= MAX_ROWS:
        answer += (
            f"\n\n(Showing the first {MAX_ROWS:,} rows — narrow the question "
            "for a complete answer.)"
        )
    turn = TurnResult(
        action="answer",
        answer=answer,
        sql=sql,
        columns=result.columns,
        rows=result.rows,
        chart=chart,
        analysis=analysis,
        execution_status="success",
        resolved=resolved,
        context_chips=chips,
    )
    # Only successful data answers are cached — never a clarification, an
    # out-of-scope reply, or an error.
    if cache_key is not None:
        _cache_put(cache_key, turn)
    return turn


def _retry_sql(llm, system_prompt, user_prompt, bad_sql, error, role):
    """One corrective retry: feed the failed SQL + BigQuery error back and ask for
    a fix. Returns (sql, QueryResult) if a corrected query runs, else None."""
    fix_prompt = (
        user_prompt
        + "\n\nYOUR PREVIOUS SQL FAILED. Fix it and return the SAME JSON (action=sql).\n"
        + f"Failed SQL:\n{bad_sql}\n\nBigQuery error:\n{error}\n\n"
        + "Check exact column names and WHICH TABLE they belong to (see the table "
        + "sections and the AUTHORITATIVE COLUMN TYPES block). Common issues: a date "
        + "column named differently across tables (created_date vs date_created vs "
        + "verified_date); DATETIME columns needing DATE(col); joining hosp_id/"
        + "service_id without stripping the `_N` suffix (see §10); or non-ASCII "
        + "characters in the SQL (Hindi/Devanagari in an alias — all SQL identifiers "
        + "and aliases MUST be plain ASCII English). Do not reference a column that "
        + "isn't in the table you selected."
    )
    try:
        gen = llm.generate_json(system_prompt, fix_prompt)
    except Exception:  # noqa: BLE001
        return None
    sql2 = (gen.get("sql") or "").strip()
    if not sql2:
        return None
    if not validate_sql(sql2).ok or not check_rbac(sql2, role).allowed:
        return None
    sql2 = enforce_row_limit(sql2)
    result2 = get_bigquery_client().run_select(sql2)
    return (sql2, result2)


def _regen_ascii_sql(llm, system_prompt, user_prompt, bad_sql):
    """Regenerate SQL as pure ASCII when the model emitted non-ASCII identifiers
    (e.g. a Devanagari alias for a Hindi question). Returns the new SQL string, or
    None. The answer_template can stay in the user's language — only SQL must be
    ASCII. Validation/RBAC/execution happen in the normal flow afterwards."""
    prompt = (
        user_prompt
        + "\n\nYOUR PREVIOUS SQL CONTAINED NON-ASCII CHARACTERS (e.g. a Hindi/"
        "Devanagari column alias), which BigQuery rejects. Regenerate the SAME "
        "query with EVERY SQL keyword, table name, column name and alias in plain "
        "ASCII English (e.g. `AS total_transactions`, never a Hindi alias). Keep "
        "answer_template in the user's language. Return the same JSON (action=sql)."
        f"\nPrevious SQL:\n{bad_sql}"
    )
    try:
        gen = llm.generate_json(system_prompt, prompt)
    except Exception:  # noqa: BLE001
        return None
    s = (gen.get("sql") or "").strip()
    return s if (s and s.isascii()) else None


def _build_chips(resolved: dict, session_context: dict | None) -> dict:
    chips: dict[str, Any] = {}
    geos = resolved.get("geography") or []
    if geos:
        chips["geography"] = geos[0]["name"]
    elif session_context and session_context.get("state_name"):
        chips["geography"] = session_context["state_name"]
    period = resolved.get("period")
    if period:
        chips["period"] = period["label"]
    elif session_context and session_context.get("period_label"):
        chips["period"] = session_context["period_label"]
    return chips


_ANALYSIS_SYSTEM = (
    "You are a senior data analyst for India's ABDM (Ayushman Bharat Digital "
    "Mission) — health facility/professional registries, ABHA creation, "
    "health-record linking, and Scan & Share / Scan & Pay adoption. You are "
    "given the ACTUAL results of a database query. Analyse the "
    "numbers and return a JSON object with keys: "
    '{"summary": string, "insights": string[], "trends": string[]}. '
    "Rules: base EVERY statement strictly on the data provided — cite specific "
    "categories and figures (largest/smallest, totals, shares/percentages, notable "
    "gaps or concentration). Give 2–4 `insights`, each one concise sentence. Put "
    "items in `trends` ONLY if there is a time or naturally ordered dimension "
    "(otherwise return an empty list). Never invent data not present. LANGUAGE — "
    "MIRROR THE SCRIPT of the user's question: Devanagari characters in the "
    "question → write in Hindi Devanagari (do NOT romanize); Latin English → "
    "English; Latin Hindi/mixed (Hinglish) → Hinglish in Latin. Devanagari in → "
    "Devanagari out; Latin in → Latin out."
)


def analyze_results(
    question: str, columns: list[str], rows: list[dict], llm
) -> dict[str, Any] | None:
    """Second pass: turn the fetched rows into a structured summary + insights.

    Sends up to 50 rows (results are usually small aggregates) back to the LLM.
    Results never contain PII (the safety layer forbids PII columns), so this is
    safe to send. Returns None on any failure so the turn still succeeds."""
    import json

    sample = rows[:50]
    dev = any("ऀ" <= ch <= "ॿ" for ch in (question or ""))
    lang_note = (
        "Write summary/insights/trends in DEVANAGARI (Hindi) script — the question "
        "is in Devanagari; do NOT romanize."
        if dev
        else "Write in Latin script matching the question (English or Hinglish); no Devanagari."
    )
    user = (
        f"User question: {question}\n"
        f"{lang_note}\n"
        f"Columns: {', '.join(columns)}\n"
        f"Row count: {len(rows)}\n"
        f"Data (JSON, up to 50 rows):\n{json.dumps(sample, default=str)}"
    )
    try:
        out = llm.generate_json(_ANALYSIS_SYSTEM, user)
    except Exception:  # noqa: BLE001
        logger.warning("Analysis pass failed", exc_info=True)
        return None
    summary = str(out.get("summary") or "").strip()
    insights = [str(x).strip() for x in (out.get("insights") or []) if str(x).strip()][:5]
    trends = [str(x).strip() for x in (out.get("trends") or []) if str(x).strip()][:4]
    if not summary and not insights:
        return None
    return {"summary": summary, "insights": insights, "trends": trends}


def _sanitize_chart(
    chart: Any, columns: list[str], rows: list[dict]
) -> dict[str, Any] | None:
    """Validate the LLM's chart suggestion against the real result set.

    Returns a clean spec {type, x, series[], title, drilldown?} or None if the
    result isn't chartable (scalar, <2 rows, unknown columns, no numeric series).
    """
    if not isinstance(chart, dict) or len(rows) < 2:
        return None
    ctype = str(chart.get("type", "")).lower()
    if ctype not in ("bar", "line", "area", "pie"):
        return None
    colset = set(columns)
    x = chart.get("x")
    if x not in colset:
        return None
    series = [s for s in (chart.get("series") or []) if s in colset and s != x]
    if not series:
        # fall back to any numeric column that isn't x
        series = [
            c
            for c in columns
            if c != x and any(isinstance(r.get(c), (int, float)) for r in rows)
        ]
    if not series:
        return None
    spec = {
        "type": ctype,
        "x": x,
        "series": series[:6],  # soft cap per the categorical series ladder
        "title": str(chart.get("title") or "").strip(),
    }
    drill = chart.get("drilldown")
    if isinstance(drill, str) and drill.strip().lower() not in ("", "none", "null", "na", "n/a"):
        spec["drilldown"] = drill.strip()
    return spec


def _format_answer(template: str, columns: list[str], rows: list[dict]) -> str:
    """Produce a concise plain-language answer.

    For a single scalar result we substitute the value inline; otherwise we
    summarise row/column counts and let the table carry the detail.
    """
    # Keep the answer in the LLM's language (English/Hindi/Hinglish); the table or
    # chart carries the row/column detail, so we don't append English summaries.
    template = (template or "").strip()
    if not rows:
        return template or "No matching records were found."
    if len(rows) == 1 and len(columns) == 1:
        val = rows[0][columns[0]]
        base = template or f"{columns[0]}: {val}"
        return base if str(val) in base else f"{base} ({columns[0]} = {val})"
    return template or f"{len(rows)} rows returned."
