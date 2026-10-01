"""ABDM weekly report aggregates.

Runs a fixed set of read-only aggregates over the ABDM tables for a date range:
ABHA creation, facility & professional registration, health-record linking, and
Scan & Share / Scan & Pay transactions — with week-over-week change, geography
and ownership breakdowns, and bridge/integrator status. Deterministic SQL — the
LLM only writes the executive summary.
"""
from __future__ import annotations

import json
import logging
from datetime import date

from app.db.bigquery_client import get_bigquery_client
from app.domains import table_ref
from app.report.common import delta, period, rows_many, scalar_of

logger = logging.getLogger(__name__)


def build_weekly_report(start: date, end: date, llm=None) -> dict:
    bq = get_bigquery_client()

    FAC = table_ref("facility_registry")
    HPR = table_ref("professionals_registry")
    ABHA = table_ref("top_indicators")
    LINK = table_ref("linked_trend")
    LFAC = table_ref("linked_facility")
    SS = table_ref("scan_share")
    SP = table_ref("scan_pay")
    BR = table_ref("bridge_integrator")
    # state_district_master is one row per district — collapse it before joining
    # on state_code alone, or a state-level aggregate fans out. See GOVERNANCE.md.
    SM = f"(SELECT DISTINCT state_code, state_name FROM {table_ref('state_district_master')})"

    prev_start = start - (end - start)

    # ---- One batch: every aggregate the report needs, all independent. ----
    queries: dict[str, str] = {
        # Period volume metrics. overall_count is the per-day ABHA count
        # (today_count is ~always 0). See GOVERNANCE.md §4.
        "abha": f"SELECT SUM(overall_count) AS v FROM {ABHA} WHERE {period('created_date', start, end)}",
        "linked": f"SELECT SUM(record_linked_count) AS v FROM {LINK} WHERE {period('created_date', start, end, True)}",
        "ss_txns": f"SELECT SUM(counts) AS v FROM {SS} WHERE {period('date_created', start, end, True)}",
        "sp_txns": f"SELECT SUM(facility_count) AS v FROM {SP} WHERE {period('date_created', start, end)}",
        # Same four over the previous period, for week-over-week.
        "abha_prev": f"SELECT SUM(overall_count) AS v FROM {ABHA} WHERE {period('created_date', prev_start, start)}",
        "linked_prev": f"SELECT SUM(record_linked_count) AS v FROM {LINK} WHERE {period('created_date', prev_start, start, True)}",
        "ss_txns_prev": f"SELECT SUM(counts) AS v FROM {SS} WHERE {period('date_created', prev_start, start, True)}",
        "sp_txns_prev": f"SELECT SUM(facility_count) AS v FROM {SP} WHERE {period('date_created', prev_start, start)}",
        # Remaining scalars.
        "sp_amt": f"SELECT SUM(payment_amount) AS v FROM {SP} WHERE {period('date_created', start, end)}",
        "fac_verified": f"SELECT COUNT(DISTINCT hfr_id) AS v FROM {FAC} WHERE {period('verified_date', start, end)}",
        "hpr_verified": f"SELECT SUM(registered_count) AS v FROM {HPR} WHERE {period('created_date', start, end)}",
        "active_links": f"SELECT COUNT(*) AS v FROM {LFAC} WHERE active = 't'",
        "states_covered": f"SELECT COUNT(DISTINCT state_code) AS v FROM {ABHA} WHERE {period('created_date', start, end)}",
        # Geography breakdowns.
        "abha_by_state": f"""SELECT sm.state_name AS state, SUM(a.overall_count) AS abha_created
            FROM {ABHA} a LEFT JOIN {SM} sm ON a.state_code = sm.state_code
            WHERE {period('a.created_date', start, end)}
            GROUP BY state ORDER BY abha_created DESC LIMIT 10""",
        "scan_share_by_state": f"""SELECT state_name AS state, SUM(counts) AS transactions
            FROM {SS} WHERE {period('date_created', start, end, True)}
            GROUP BY state ORDER BY transactions DESC LIMIT 10""",
        "linked_by_state": f"""SELECT sm.state_name AS state, SUM(l.record_linked_count) AS records_linked
            FROM {LINK} l LEFT JOIN {SM} sm ON l.state_code = sm.state_code
            WHERE {period('l.created_date', start, end, True)}
            GROUP BY state ORDER BY records_linked DESC LIMIT 10""",
        # Facility profile.
        "facilities_by_ownership": f"""SELECT facility_ownership AS ownership, COUNT(DISTINCT hfr_id) AS facilities
            FROM {FAC} WHERE {period('verified_date', start, end)}
            GROUP BY ownership ORDER BY facilities DESC""",
        "facilities_by_type": f"""SELECT facility_type_name AS facility_type, COUNT(DISTINCT hfr_id) AS facilities
            FROM {FAC} WHERE {period('verified_date', start, end)} AND facility_type_name IS NOT NULL
            GROUP BY facility_type ORDER BY facilities DESC LIMIT 8""",
        # Professionals by type (d/n/p).
        "hpr_by_type": f"""SELECT hpr_type, SUM(registered_count) AS professionals
            FROM {HPR} WHERE {period('created_date', start, end)}
            GROUP BY hpr_type ORDER BY professionals DESC""",
        # Scan & Pay by payment status.
        "scan_pay_by_status": f"""SELECT payment_status, COUNT(*) AS records, SUM(payment_amount) AS amount
            FROM {SP} WHERE {period('date_created', start, end)}
            GROUP BY payment_status ORDER BY records DESC""",
        # Top bridges by active facility links.
        "links_by_bridge": f"""SELECT bridge_name, COUNT(*) AS active_links
            FROM {LFAC} WHERE active = 't' AND bridge_name IS NOT NULL
            GROUP BY bridge_name ORDER BY active_links DESC LIMIT 10""",
        # Bridge / integrator status (reference, not date-bound).
        "bridge_by_status": f"""SELECT status, COUNT(*) AS bridges
            FROM {BR} WHERE status IS NOT NULL GROUP BY status ORDER BY bridges DESC""",
    }
    res = rows_many(bq, queries)

    def scalar(key: str) -> float:
        return scalar_of(res, key)

    abha = scalar("abha")
    linked = scalar("linked")
    ss_txns = scalar("ss_txns")
    sp_txns = scalar("sp_txns")
    fac_verified = scalar("fac_verified")

    kpis = {
        "abha_created": int(abha),
        "facilities_verified": int(fac_verified),
        "hpr_verified": int(scalar("hpr_verified")),
        "records_linked": int(linked),
        "scan_share_txns": int(ss_txns),
        "scan_pay_txns": int(sp_txns),
        "scan_pay_amount": round(scalar("sp_amt"), 2),
        "active_facility_links": int(scalar("active_links")),
        "states_covered": int(scalar("states_covered")),
        "wow": {
            "abha_created": delta(abha, scalar("abha_prev")),
            "records_linked": delta(linked, scalar("linked_prev")),
            "scan_share_txns": delta(ss_txns, scalar("ss_txns_prev")),
            "scan_pay_txns": delta(sp_txns, scalar("sp_txns_prev")),
        },
    }

    report = {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "kpis": kpis,
        "abha_by_state": res["abha_by_state"],
        "scan_share_by_state": res["scan_share_by_state"],
        "linked_by_state": res["linked_by_state"],
        "facilities_by_ownership": res["facilities_by_ownership"],
        "facilities_by_type": res["facilities_by_type"],
        "hpr_by_type": res["hpr_by_type"],
        "scan_pay_by_status": res["scan_pay_by_status"],
        "links_by_bridge": res["links_by_bridge"],
        "bridge_by_status": res["bridge_by_status"],
        "analysis": None,
    }
    total_activity = abha + linked + ss_txns + sp_txns + fac_verified
    if llm and total_activity > 0:
        report["analysis"] = _weekly_analysis(llm, report)
    return report


REPORT_SYSTEM = (
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
        out = llm.generate_json(REPORT_SYSTEM, json.dumps(payload, default=str))
    except Exception:  # noqa: BLE001
        logger.warning("Weekly analysis failed", exc_info=True)
        return None
    summary = str(out.get("summary") or "").strip()
    insights = [str(x).strip() for x in (out.get("insights") or []) if str(x).strip()][:6]
    trends = [str(x).strip() for x in (out.get("trends") or []) if str(x).strip()][:4]
    if not summary and not insights:
        return None
    return {"summary": summary, "insights": insights, "trends": trends}
