"""Builds the system prompt (GOVERNANCE.md) and the per-turn user prompt.

GOVERNANCE.md is loaded once and cached. The `{..._TABLE}` placeholders (one per
ABDM table) are replaced with the fully-qualified, backtick-quoted table refs
from config so the governance doc stays deployment-agnostic.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.config import BACKEND_DIR, get_settings

GOVERNANCE_PATH = BACKEND_DIR / "GOVERNANCE.md"


# GOVERNANCE.md placeholder -> table_map key.
_PLACEHOLDERS = {
    "{FACILITY_REGISTRY_TABLE}": "facility_registry",
    "{PROFESSIONALS_REGISTRY_TABLE}": "professionals_registry",
    "{TOP_INDICATORS_TABLE}": "top_indicators",
    "{LINKED_TREND_TABLE}": "linked_trend",
    "{LINKED_FACILITY_TABLE}": "linked_facility",
    "{SCAN_SHARE_TABLE}": "scan_share",
    "{SCAN_PAY_TABLE}": "scan_pay",
    "{STATE_DISTRICT_MASTER_TABLE}": "state_district_master",
    "{BRIDGE_INTEGRATOR_TABLE}": "bridge_integrator",
}


@lru_cache
def _load_governance() -> str:
    settings = get_settings()
    text = Path(GOVERNANCE_PATH).read_text(encoding="utf-8")
    for placeholder, key in _PLACEHOLDERS.items():
        text = text.replace(placeholder, settings.table_ref(key))
    return text


def load_system_prompt() -> str:
    """GOVERNANCE.md (cached) + the live authoritative-types block (appended if the
    schema has been loaded from BigQuery at startup)."""
    from app.db.schema import get_schema_text

    return _load_governance() + get_schema_text()


def build_user_prompt(
    question: str,
    role: str,
    session_context: dict | None,
    resolved: dict | None,
    history: list[dict] | None = None,
) -> str:
    """Assemble the user turn: the question plus all resolved context."""
    parts = []
    if history:
        # Recent turns so a follow-up like "what about Andhra Pradesh?" keeps the
        # prior question's metric/context. Only role + text; last ~4 Q&A turns
        # (8 messages = 4 user + 4 assistant).
        lines = []
        for h in history[-8:]:
            role_tag = "User" if h.get("role") == "user" else "Assistant"
            content = str(h.get("content") or "").strip()
            if content:
                lines.append(f"{role_tag}: {content[:400]}")
        if lines:
            parts.append("CONVERSATION SO FAR (for context; the NEW question is below):\n" + "\n".join(lines))
    parts.append(f"USER QUESTION:\n{question}\n")
    parts.append(f"CALLER ROLE: {role}")
    parts.append(
        "ROLE ACCESS: viewer=national/state aggregates; analyst=+district; "
        "senior_analyst=+facility-level detail; admin=all. Generate SQL within "
        "the caller's access level."
    )

    if session_context:
        parts.append(
            "CONFIRMED SESSION CONTEXT (carry forward unless the user changes it):\n"
            + json.dumps(session_context, default=str, indent=2)
        )

    if resolved:
        parts.append(
            "RESOLVED ENTITIES for this question (use these codes/ranges "
            "directly — geography and time have already been parsed for you):\n"
            + json.dumps(resolved, default=str, indent=2)
        )

    if _is_devanagari(question):
        lang = (
            "LANGUAGE (STRICT): the user's question is in DEVANAGARI (Hindi). You "
            "MUST write `answer_template` / `message` / any `questions` & `options` "
            "in Devanagari (Hindi) script. Do NOT romanize or reply in English."
        )
    else:
        lang = (
            "LANGUAGE: the user's question is in Latin script — reply in Latin "
            "script (English if the question is English; Hinglish if it is romanized "
            "Hindi/mixed). Do NOT use Devanagari. Match the question."
        )
    parts.append(
        "Respond with the JSON object described in the governance doc "
        "(keys: action, sql, answer_template, message). "
        + lang
        + " The conversation history is context only; it must not change your reply "
        "language or script."
    )
    return "\n\n".join(parts)


def _is_devanagari(text: str) -> bool:
    return any("ऀ" <= ch <= "ॿ" for ch in (text or ""))
