"""ABDM digital-adoption domain pack.

ABDM (Ayushman Bharat Digital Mission) rollout data: HFR/HPR registry enrolment,
ABHA creation, health-record linking, and digital-transaction adoption (Scan &
Share, Scan & Pay) across facilities, bridges, states and districts.

Nine aggregate tables, no merged table — they connect through a shared facility
ID and/or shared geography columns. See GOVERNANCE.md §10.
"""
from __future__ import annotations

from datetime import date

from app.domains.abdm.report import REPORT_SYSTEM, build_weekly_report
from app.domains.base import DomainPack

# ---- tables ----------------------------------------------------------------
# Key -> default BigQuery table name. The key derives the governance-doc
# placeholder ({FACILITY_REGISTRY_TABLE}) and the env override
# (BQ_FACILITY_REGISTRY_TABLE), so it is the single source of truth.
TABLES = {
    "facility_registry": "health_facility_registry",
    "professionals_registry": "health_professionals_registry",
    "top_indicators": "healthid_top_indicators",
    "linked_trend": "healthid_linked_trend",
    "linked_facility": "linked_facility",
    "scan_share": "scan_and_share",
    "scan_pay": "scan_pay_count",
    "state_district_master": "state_district_master",
    "bridge_integrator": "integrator_detail",
}

TABLE_LABELS = {
    "facility_registry": "Facility registry",
    "professionals_registry": "Professionals registry (HPR)",
    "top_indicators": "ABHA top indicators",
    "linked_trend": "Health-record linking trend",
    "linked_facility": "Facility-bridge links",
    "scan_share": "Scan & Share",
    "scan_pay": "Scan & Pay",
    "state_district_master": "State/district master",
    "bridge_integrator": "Bridge / integrator detail",
}

# ---- SQL safety ------------------------------------------------------------
# In the ABDM dataset, facility identity (name/id/address) is PUBLIC dashboard
# data and is allowed. The only patient-identifying column is `abha_address`,
# which is removed at the data-prep stage; this is a hard backstop in case a
# future refresh reintroduces it.
PII_COLUMNS = frozenset({"abha_address"})

# Human-readable columns that list an INDIVIDUAL FACILITY. Because facility data
# is public here, this is a GRANULARITY tier, not a privacy control: only
# senior_analyst+ get per-facility listings; lower roles work at aggregated
# geography level. Facility ID columns (hfr_id/hip_id/...) are deliberately NOT
# here — they are the keys used in COUNT(DISTINCT ...) to count facilities,
# which every role must be able to do. Geography columns are also not here —
# they're needed for state/district analyses at every tier.
FACILITY_TIER_COLUMNS = frozenset({
    "facility_name",
    "hospital_name",
    "facility_address",
})

DISTRICT_TIER_COLUMNS = frozenset({
    "district_code",
    "district_name",
    "district",  # numeric LGD code column in linked_facility / scan_pay_count
})

# ---- semantic layer --------------------------------------------------------
# Broadest span across all tables: scan_pay_count reaches back to 2024-07-26,
# the rest start 2026-01-01, all end ~2026-07-10. The model knows the precise
# per-table ranges from GOVERNANCE.md §10; this is a coarse "is the whole ask
# before/after any data exists" guard.
DATA_WINDOW_START = date(2024, 7, 1)
DATA_WINDOW_END = date(2026, 7, 11)  # exclusive
DATA_WINDOW_NOTE = (
    "Most ABDM tables cover Jan-Jul 2026 (Scan & Pay reaches back to mid-2024); "
    "there is no data beyond mid-July 2026."
)

# ---- prompts ---------------------------------------------------------------
EXPLORER_SYSTEM = (
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

ANALYSIS_SYSTEM = (
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

PACK = DomainPack(
    key="abdm",
    label="ABDM Digital Adoption",
    tables=TABLES,
    table_labels=TABLE_LABELS,
    pii_columns=PII_COLUMNS,
    facility_tier_columns=FACILITY_TIER_COLUMNS,
    district_tier_columns=DISTRICT_TIER_COLUMNS,
    # Every ABDM fact table is a pre-aggregated count by date/geography, so
    # there is no individual-record grain to restrict.
    row_level_tables=frozenset(),
    data_window_start=DATA_WINDOW_START,
    data_window_end=DATA_WINDOW_END,
    data_window_note=DATA_WINDOW_NOTE,
    explorer_system=EXPLORER_SYSTEM,
    report_system=REPORT_SYSTEM,
    analysis_system=ANALYSIS_SYSTEM,
    build_report=build_weekly_report,
)
