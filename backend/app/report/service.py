"""Weekly report dispatcher.

The aggregates are scheme-specific and live in the active domain pack
(app/domains/<domain>/report.py); the shared machinery — concurrency, decimal
cleanup, period predicates, week-over-week deltas — is in report/common.py.

This module only routes, so adding a domain means adding its report module and
nothing here.
"""
from __future__ import annotations

from datetime import date

from app.domains import get_domain


class ReportUnavailable(RuntimeError):
    """The active domain does not publish a weekly report."""


def build_weekly_report(start: date, end: date, llm=None) -> dict:
    pack = get_domain()
    if pack.build_report is None:
        raise ReportUnavailable(
            f"The {pack.label} domain does not define a weekly report."
        )
    return pack.build_report(start, end, llm=llm)
