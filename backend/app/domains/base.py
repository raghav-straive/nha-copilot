"""The DomainPack contract.

A pack holds everything that is specific to ONE scheme (ABDM digital adoption,
PM-JAY claims) so the platform around it — auth, SQL safety mechanism, charts,
exports, RAG — stays scheme-agnostic. See docs/pmjay-migration.md.

This module deliberately imports nothing from app.config: packs are plain data,
and the settings lookup lives in app.domains.__init__ so there is no import
cycle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Protocol


class ReportBuilder(Protocol):
    def __call__(self, start: date, end: date, llm=None) -> dict: ...


@dataclass(frozen=True)
class DomainPack:
    """One scheme's data model, rules, prompts and UI copy."""

    # --- identity ---
    key: str  # "abdm" | "pmjay" — must match the directory name
    label: str  # human-readable, shown in the UI

    # --- tables ---
    # logical key -> default BigQuery table name. The logical key drives
    # EVERYTHING else: the governance-doc placeholder is derived from it as
    # {KEY_UPPER_TABLE}, and the env override as BQ_{KEY}_TABLE. Keep keys
    # snake_case and stable; renaming one is a breaking change.
    tables: dict[str, str]
    # logical key -> "### heading" used in the live-schema prompt block.
    table_labels: dict[str, str]

    # --- SQL safety ---
    # Columns that must NEVER reach a user, whatever their role. This is a hard
    # backstop enforced by validator.validate_sql, which runs before and
    # independently of RBAC — so admin does not bypass it either.
    pii_columns: frozenset[str]
    # RBAC granularity tiers (see rbac_filter). facility_* is restricted below
    # senior_analyst; district_* below analyst. These are about GRANULARITY, not
    # privacy — anything genuinely sensitive belongs in pii_columns instead.
    facility_tier_columns: frozenset[str]
    district_tier_columns: frozenset[str]
    # Tables whose rows are individual records (a claim, a patient episode).
    # A non-aggregated SELECT against one of these is row-level access and is
    # restricted below senior_analyst. Empty for aggregate-only domains.
    row_level_tables: frozenset[str] = frozenset()

    # --- semantic layer ---
    # Coarse "is the whole ask outside any data we hold" guard. The model knows
    # precise per-table ranges from the governance doc; this is the backstop.
    data_window_start: date = date(2000, 1, 1)
    data_window_end: date = date(2100, 1, 1)  # exclusive
    data_window_note: str = ""

    # --- prompts ---
    governance_file: str = "GOVERNANCE.md"  # resolved inside the pack directory
    explorer_system: str = ""
    report_system: str = ""

    # --- weekly report ---
    # None means this domain has no weekly report; the endpoint 404s.
    build_report: Callable | None = None

    # --- frontend ---
    # Copy and coded-value maps served to the SPA by GET /meta, so one frontend
    # build serves both deployments. Shape is validated in tests.
    ui: dict = field(default_factory=dict)

    # ----- derived -----
    def placeholder(self, key: str) -> str:
        """The governance-doc token for a table key.

        Derived, not hand-maintained: a table used to need registering in three
        places that could silently drift (config.table_map,
        prompt_builder._PLACEHOLDERS, schema._TABLE_LABELS). Verified against
        all 9 ABDM and all 3 PM-JAY placeholders.
        """
        return "{" + key.upper() + "_TABLE}"

    @property
    def table_keys(self) -> list[str]:
        return list(self.tables)

    def label_for(self, key: str) -> str:
        return self.table_labels.get(key, key)
