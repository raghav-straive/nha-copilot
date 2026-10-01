"""Natural-language time reference resolution.

Converts phrases like "last quarter", "Q2 2025-26", "last month" into explicit
[start, end) date ranges. Also knows the active domain's overall data window and
flags requests that fall entirely outside it.

The parsing itself (financial years, quarters, ISO dates, relative phrases) is
scheme-agnostic; only the data window is domain-specific and comes from the pack.

Dates are resolved relative to a supplied `today` (defaults to the real current
date) so the module is deterministic in tests.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from app.domains import get_domain


def _data_window() -> tuple[date, date, str]:
    """The active domain's coarse data window: (start, end-exclusive, note).

    The model knows precise per-table ranges from the governance doc; this is
    only a "is the whole ask before/after any data exists" guard.
    """
    pack = get_domain()
    return pack.data_window_start, pack.data_window_end, pack.data_window_note


@dataclass
class TimeResolution:
    status: str  # "resolved" | "none"
    start: date | None = None
    end: date | None = None  # exclusive
    label: str | None = None
    outside_data_window: bool = False
    note: str | None = None


# An explicit ISO date. Matched (and consumed) before the financial-year
# patterns, because "2026-03-15" otherwise looks like "FY2026-..." — see
# _ISO_RANGE_RE below and the FY guard in resolve().
_ISO = r"(20\d{2})-(\d{2})-(\d{2})"
_ISO_RANGE_RE = re.compile(
    _ISO + r"\s*(?:to|and|till|until|through|upto|up to|–|—|-|\.\.)\s*" + _ISO
)
_ISO_ONE_RE = re.compile(_ISO)

# A real quarter reference: the word itself, or q1..q4. NOT a bare "q", which
# appears in ordinary words (unique, query, frequency, quantity, equal).
_QUARTER_HINT_RE = re.compile(r"\bquarter\b|\bq\s*[1-4]\b")


def _fy_range(fy_start_year: int) -> tuple[date, date]:
    """Indian financial year: 1 Apr Y -> 1 Apr Y+1 (exclusive)."""
    return date(fy_start_year, 4, 1), date(fy_start_year + 1, 4, 1)


def _as_date(y: str, m: str, d: str) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None  # e.g. 2025-13-45


def _quarter_range(fy_start_year: int, q: int) -> tuple[date, date]:
    # Q1 Apr-Jun, Q2 Jul-Sep, Q3 Oct-Dec, Q4 Jan-Mar
    starts = {
        1: date(fy_start_year, 4, 1),
        2: date(fy_start_year, 7, 1),
        3: date(fy_start_year, 10, 1),
        4: date(fy_start_year + 1, 1, 1),
    }
    ends = {
        1: date(fy_start_year, 7, 1),
        2: date(fy_start_year, 10, 1),
        3: date(fy_start_year + 1, 1, 1),
        4: date(fy_start_year + 1, 4, 1),
    }
    return starts[q], ends[q]


class TimeResolver:
    def resolve(self, text: str, today: date | None = None) -> TimeResolution:
        today = today or date.today()
        t = text.lower()

        # ---- explicit ISO dates first ----
        # These must be checked before the financial-year patterns: the FY regex
        # would otherwise read "2026-03-15" as FY2026-27 (Apr 2026 - Apr 2027),
        # a range that does not even contain the date the user asked about.

        # A date range: "between 2025-04-01 and 2025-06-30".
        m = _ISO_RANGE_RE.search(t)
        if m:
            start = _as_date(m.group(1), m.group(2), m.group(3))
            last = _as_date(m.group(4), m.group(5), m.group(6))
            if start and last and last >= start:
                # The user means an inclusive range; our `end` is exclusive.
                return self._finalize(
                    start,
                    last + timedelta(days=1),
                    f"{start.isoformat()} to {last.isoformat()}",
                )

        # A single date: "ABHA created on 2026-03-15" -> just that day.
        m = _ISO_ONE_RE.search(t)
        if m:
            day = _as_date(m.group(1), m.group(2), m.group(3))
            if day:
                return self._finalize(day, day + timedelta(days=1), day.isoformat())

        # Q2 2023-24 / Q2 FY2023-24 / quarter 2 2023-24
        m = re.search(r"q(?:uarter)?\s*([1-4]).*?(20\d{2})\s*[-/]\s*(\d{2,4})", t)
        if m:
            q = int(m.group(1))
            fy_start = int(m.group(2))
            start, end = _quarter_range(fy_start, q)
            return self._finalize(start, end, f"Q{q} {fy_start}-{str(fy_start + 1)[-2:]}")

        # FY2023-24 / 2023-24 / financial year 2023-24.
        # The trailing guard rejects a third date component, so a stray ISO date
        # that slipped past the checks above is not read as a financial year.
        m = re.search(r"(?:fy\s*)?(20\d{2})\s*[-/]\s*(\d{2,4})(?!\s*[-/]?\s*\d)", t)
        if m:
            fy_start = int(m.group(1))
            start, end = _fy_range(fy_start)
            return self._finalize(start, end, f"FY{fy_start}-{str(fy_start + 1)[-2:]}")

        # Single calendar year, e.g. "in 2025".
        # Skipped when the question really is about a quarter, so "Q2 2025" is
        # not flattened into the whole of 2025. The check looks for the word
        # "quarter" or q1..q4 — NOT a bare "q", which previously matched the
        # letter inside ordinary words (unique, query, frequency, quantity) and
        # silently dropped the year from the resolved context.
        m = re.search(r"\b(20\d{2})\b", t)
        if m and not _QUARTER_HINT_RE.search(t):
            y = int(m.group(1))
            return self._finalize(date(y, 1, 1), date(y + 1, 1, 1), str(y))

        if "last year" in t or "previous year" in t:
            start, end = _fy_range(today.year - 1 if today.month >= 4 else today.year - 2)
            return self._finalize(start, end, "last financial year")

        if "this year" in t or "current year" in t:
            start, end = _fy_range(today.year if today.month >= 4 else today.year - 1)
            return self._finalize(start, end, "this financial year")

        if "last quarter" in t or "previous quarter" in t:
            fy_start = today.year if today.month >= 4 else today.year - 1
            cur_q = ((today.month - 4) % 12) // 3 + 1
            q = cur_q - 1 if cur_q > 1 else 4
            if cur_q == 1:
                fy_start -= 1
            start, end = _quarter_range(fy_start, q)
            return self._finalize(start, end, f"last quarter (Q{q})")

        if "last month" in t or "previous month" in t:
            y, mo = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
            end_y, end_mo = (y, mo + 1) if mo < 12 else (y + 1, 1)
            return self._finalize(date(y, mo, 1), date(end_y, end_mo, 1), "last month")

        return TimeResolution(status="none")

    def _finalize(self, start: date, end: date, label: str) -> TimeResolution:
        # Overlap check against the active domain's data window.
        win_start, win_end, win_note = _data_window()
        outside = end <= win_start or start >= win_end
        note = None
        if outside:
            note = (
                f"The requested period ({label}) is outside the available data "
                f"window. {win_note}"
            )
        return TimeResolution(
            status="resolved",
            start=start,
            end=end,
            label=label,
            outside_data_window=outside,
            note=note,
        )


_resolver: TimeResolver | None = None


def get_time_resolver() -> TimeResolver:
    global _resolver
    if _resolver is None:
        _resolver = TimeResolver()
    return _resolver
