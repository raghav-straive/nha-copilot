"""Weekly report data service (ABDM).

Runs a fixed set of read-only aggregate queries over the ABDM tables for a date
range and returns a structured report payload covering digital-adoption activity:
ABHA creation, facility & professional registration, health-record linking, and
Scan & Share / Scan & Pay transactions — with week-over-week change, geography
and ownership breakdowns, and bridge/integrator status. Deterministic SQL — the
LLM only writes the executive summary.
"""
from __future__ import annotations

import decimal
import json
import logging
from datetime import date

from app.config import get_settings
from app.db.bigquery_client import get_bigquery_client

logger = logging.getLogger(__name__)


def _num(x) -> float:
    if x is None:
        return 0.0
    if isinstance(x, decimal.Decimal):
        return float(x)
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _clean(rows: list[dict]) -> list[dict]:
    return [
        {k: (float(v) if isinstance(v, decimal.Decimal) else v) for k, v in r.items()}
        for r in rows
    ]


def _rows(bq, sql: str) -> list[dict]:
    res = bq.run_select(sql)
    if not res.ok:
        logger.warning("Report query failed: %s", res.error)
        return []
    return _clean(res.rows)


def _one(bq, sql: str) -> dict:
    r = _rows(bq, sql)
    return r[0] if r else {}


def _period(col: str, start: date, end: date, datetime_col: bool = False) -> str:
    expr = f"DATE({col})" if datetime_col else col
    return f"{expr} >= DATE('{start.isoformat()}') AND {expr} < DATE('{end.isoformat()}')"


def _delta(cur: float, prev: float) -> dict:
    change = cur - prev
    pct = (change / prev * 100) if prev else None
    return {
        "prev": round(prev, 2),
        "change": round(change, 2),
        "pct": round(pct, 1) if pct is not None else None,
    }


def build_weekly_report(start: date, end: date, llm=None) -> dict:
    s = get_settings()
    bq = get_bigquery_client()

    FAC = s.table_ref("facility_registry")
    HPR = s.table_ref("professionals_registry")
    ABHA = s.table_ref("top_indicators")
    LINK = s.table_ref("linked_trend")
    LFAC = s.table_ref("linked_facility")
    SS = s.table_ref("scan_share")
    SP = s.table_ref("scan_pay")
    BR = s.table_ref("bridge_integrator")

    prev_start = start - (end - start)

    # ---- Period volume metrics (+ previous period for WoW) ----
    def abha_created(a: date, b: date) -> float:
        # overall_count is the per-day ABHA count (today_count is ~always 0). See GOVERNANCE.md §4.
        return _num(_one(bq, f"SELECT SUM(overall_count) AS v FROM {ABHA} WHERE {_period('created_date', a, b)}").get("v"))

    def records_linked(a: date, b: date) -> float:
        return _num(_one(bq, f"SELECT SUM(record_linked_count) AS v FROM {LINK} WHERE {_period('created_date', a, b, True)}").get("v"))

    def scan_share(a: date, b: date) -> float:
        return _num(_one(bq, f"SELECT SUM(counts) AS v FROM {SS} WHERE {_period('date_created', a, b, True)}").get("v"))

    def scan_pay_txns(a: date, b: date) -> float:
        return _num(_one(bq, f"SELECT SUM(facility_count) AS v FROM {SP} WHERE {_period('date_created', a, b)}").get("v"))

    abha = abha_created(start, end)
    linked = records_linked(start, end)
    ss_txns = scan_share(start, end)
    sp_txns = scan_pay_txns(start, end)

    sp_amt = _num(_one(bq, f"SELECT SUM(payment_amount) AS v FROM {SP} WHERE {_period('date_created', start, end)}").get("v"))
    fac_verified = _num(_one(bq, f"SELECT COUNT(DISTINCT hfr_id) AS v FROM {FAC} WHERE {_period('verified_date', start, end)}").get("v"))
    hpr_verified = _num(_one(bq, f"SELECT SUM(registered_count) AS v FROM {HPR} WHERE {_period('created_date', start, end)}").get("v"))
    active_links = _num(_one(bq, f"SELECT COUNT(*) AS v FROM {LFAC} WHERE active = 't'").get("v"))
    states_covered = _num(_one(bq, f"SELECT COUNT(DISTINCT state_code) AS v FROM {ABHA} WHERE {_period('created_date', start, end)}").get("v"))

    kpis = {
        "abha_created": int(abha),
        "facilities_verified": int(fac_verified),
        "hpr_verified": int(hpr_verified),
        "records_linked": int(linked),
        "scan_share_txns": int(ss_txns),
        "scan_pay_txns": int(sp_txns),
        "scan_pay_amount": round(sp_amt, 2),
        "active_facility_links": int(active_links),
        "states_covered": int(states_covered),
        "wow": {
            "abha_created": _delta(abha, abha_created(prev_start, start)),
            "records_linked": _delta(linked, records_linked(prev_start, start)),
            "scan_share_txns": _delta(ss_txns, scan_share(prev_start, start)),
            "scan_pay_txns": _delta(sp_txns, scan_pay_txns(prev_start, start)),
        },
    }

    # ---- Geography breakdowns (resolve state_code -> name via the master) ----
    SM = f"(SELECT DISTINCT state_code, state_name FROM {s.table_ref('state_district_master')})"
    abha_by_state = _rows(bq, f"""SELECT sm.state_name AS state, SUM(a.overall_count) AS abha_created
        FROM {ABHA} a LEFT JOIN {SM} sm ON a.state_code = sm.state_code
        WHERE {_period('a.created_date', start, end)}
        GROUP BY state ORDER BY abha_created DESC LIMIT 10""")
    scan_share_by_state = _rows(bq, f"""SELECT state_name AS state, SUM(counts) AS transactions
        FROM {SS} WHERE {_period('date_created', start, end, True)}
        GROUP BY state ORDER BY transactions DESC LIMIT 10""")
    linked_by_state = _rows(bq, f"""SELECT sm.state_name AS state, SUM(l.record_linked_count) AS records_linked
        FROM {LINK} l LEFT JOIN {SM} sm ON l.state_code = sm.state_code
        WHERE {_period('l.created_date', start, end, True)}
        GROUP BY state ORDER BY records_linked DESC LIMIT 10""")

    # ---- Facility profile ----
    facilities_by_ownership = _rows(bq, f"""SELECT facility_ownership AS ownership, COUNT(DISTINCT hfr_id) AS facilities
        FROM {FAC} WHERE {_period('verified_date', start, end)}
        GROUP BY ownership ORDER BY facilities DESC""")
    facilities_by_type = _rows(bq, f"""SELECT facility_type_name AS facility_type, COUNT(DISTINCT hfr_id) AS facilities
        FROM {FAC} WHERE {_period('verified_date', start, end)} AND facility_type_name IS NOT NULL
        GROUP BY facility_type ORDER BY facilities DESC LIMIT 8""")

    # ---- Professionals by type (d/n/p) ----
    hpr_by_type = _rows(bq, f"""SELECT hpr_type, SUM(registered_count) AS professionals
        FROM {HPR} WHERE {_period('created_date', start, end)}
        GROUP BY hpr_type ORDER BY professionals DESC""")

    # ---- Scan & Pay by payment status ----
    scan_pay_by_status = _rows(bq, f"""SELECT payment_status, COUNT(*) AS records, SUM(payment_amount) AS amount
        FROM {SP} WHERE {_period('date_created', start, end)}
        GROUP BY payment_status ORDER BY records DESC""")

    # ---- Top bridges by active facility links ----
    links_by_bridge = _rows(bq, f"""SELECT bridge_name, COUNT(*) AS active_links
        FROM {LFAC} WHERE active = 't' AND bridge_name IS NOT NULL
        GROUP BY bridge_name ORDER BY active_links DESC LIMIT 10""")

    # ---- Bridge / integrator status (reference, not date-bound) ----
    bridge_by_status = _rows(bq, f"""SELECT status, COUNT(*) AS bridges
        FROM {BR} WHERE status IS NOT NULL GROUP BY status ORDER BY bridges DESC""")

    report = {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "kpis": kpis,
        "abha_by_state": abha_by_state,
        "scan_share_by_state": scan_share_by_state,
        "linked_by_state": linked_by_state,
        "facilities_by_ownership": facilities_by_ownership,
        "facilities_by_type": facilities_by_type,
        "hpr_by_type": hpr_by_type,
        "scan_pay_by_status": scan_pay_by_status,
        "links_by_bridge": links_by_bridge,
        "bridge_by_status": bridge_by_status,
        "analysis": None,
    }
    total_activity = abha + linked + ss_txns + sp_txns + fac_verified
    if llm and total_activity > 0:
        report["analysis"] = _weekly_analysis(llm, report)
    return report


_REPORT_SYSTEM = (
    "You are a senior analyst writing the executive summary of a WEEKLY report on "
    "India's ABDM (Ayushman Bharat Digital Mission) rollout, for NHA/ABDM "
    "officials. You are given the week's aggregate numbers (with week-over-week "
    "change) for ABHA (health ID) creation, facility & health-professional "
    "registration, health-record linking, and Scan & Share / Scan & Pay adoption, "
    "plus geography, facility-ownership/type, professional-type, payment-status, "
    "and bridge breakdowns. Return JSON: "
    '{"summary": string, "insights": string[], "trends": string[]}. '
    "summary = 2-3 sentences a busy official reads first. insights = 4-6 concise, "
    "number-backed bullets that FLAG what stands out (biggest movers vs last week, "
    "leading/lagging states, ownership or facility-type concentration, professional-"
    "type mix, Scan & Pay payment-success rate, bridge concentration). trends = "
    "week-over-week movements. Base everything strictly on the numbers; never "
    "invent. Write in English."
)


def _weekly_analysis(llm, report: dict) -> dict | None:
    try:
        payload = {k: report[k] for k in (
            "period", "kpis", "facilities_by_ownership", "facilities_by_type",
            "hpr_by_type", "scan_pay_by_status", "bridge_by_status",
        )}
        payload["top_states_abha"] = report["abha_by_state"][:5]
        payload["top_states_scan_share"] = report["scan_share_by_state"][:5]
        payload["top_bridges"] = report["links_by_bridge"][:5]
        out = llm.generate_json(_REPORT_SYSTEM, json.dumps(payload, default=str))
    except Exception:  # noqa: BLE001
        logger.warning("Weekly analysis failed", exc_info=True)
        return None
    summary = str(out.get("summary") or "").strip()
    insights = [str(x).strip() for x in (out.get("insights") or []) if str(x).strip()][:6]
    trends = [str(x).strip() for x in (out.get("trends") or []) if str(x).strip()][:4]
    if not summary and not insights:
        return None
    return {"summary": summary, "insights": insights, "trends": trends}
