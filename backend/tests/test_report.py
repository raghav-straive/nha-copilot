"""Weekly report service: helpers, concurrency, and failure isolation.

Uses a fake BigQuery client so nothing touches the cloud.
"""
from datetime import date

import pytest

from app.report import service as rs


# ---- number/date helpers ----


def test_num_coerces_and_defaults():
    import decimal

    assert rs._num(None) == 0.0
    assert rs._num(decimal.Decimal("12.5")) == 12.5
    assert rs._num("7") == 7.0
    assert rs._num("not a number") == 0.0


def test_delta_computes_change_and_percent():
    d = rs._delta(150.0, 100.0)
    assert d["prev"] == 100.0
    assert d["change"] == 50.0
    assert d["pct"] == 50.0


def test_delta_handles_zero_previous_without_dividing_by_zero():
    d = rs._delta(10.0, 0.0)
    assert d["change"] == 10.0
    assert d["pct"] is None, "percent change from zero is undefined, not infinite"


def test_period_wraps_datetime_columns_but_not_date_columns():
    a, b = date(2025, 4, 1), date(2025, 4, 8)
    assert rs._period("created_date", a, b) == (
        "created_date >= DATE('2025-04-01') AND created_date < DATE('2025-04-08')"
    )
    # DATETIME columns must be wrapped for day-level comparison (GOVERNANCE.md).
    assert rs._period("date_created", a, b, True).startswith("DATE(date_created) >=")


def test_period_end_is_exclusive():
    sql = rs._period("d", date(2025, 4, 1), date(2025, 4, 8))
    assert "< DATE('2025-04-08')" in sql
    assert "<= DATE(" not in sql


# ---- concurrent fetch ----


class FakeResult:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.columns = list(rows[0]) if rows else []
        self.error = error

    @property
    def ok(self):
        return self.error is None


class FakeBQ:
    """Records every query it is asked to run; can fail selected ones."""

    def __init__(self, responses=None, fail_on=None):
        self.responses = responses or {}
        self.fail_on = fail_on or set()
        self.seen = []

    def run_select(self, sql):
        self.seen.append(sql)
        for marker in self.fail_on:
            if marker in sql:
                return FakeResult(error=f"boom: {marker}")
        for marker, rows in self.responses.items():
            if marker in sql:
                return FakeResult(rows=rows)
        return FakeResult(rows=[{"v": 0}])


def test_rows_many_runs_every_query_and_keys_results():
    bq = FakeBQ(responses={"ALPHA": [{"v": 1}], "BETA": [{"v": 2}]})
    out = rs._rows_many(bq, {"a": "SELECT ALPHA", "b": "SELECT BETA"})
    assert out["a"] == [{"v": 1}]
    assert out["b"] == [{"v": 2}]
    assert len(bq.seen) == 2


def test_rows_many_isolates_a_failing_query():
    # One bad query must not take down the whole report.
    bq = FakeBQ(responses={"GOOD": [{"v": 9}]}, fail_on={"BAD"})
    out = rs._rows_many(bq, {"good": "SELECT GOOD", "bad": "SELECT BAD"})
    assert out["good"] == [{"v": 9}]
    assert out["bad"] == [], "a failed query degrades to an empty list"


def test_rows_many_handles_many_queries():
    bq = FakeBQ()
    queries = {f"q{i}": f"SELECT {i}" for i in range(25)}
    out = rs._rows_many(bq, queries)
    assert len(out) == 25
    assert len(bq.seen) == 25


def test_rows_many_converts_decimals():
    import decimal

    bq = FakeBQ(responses={"AMT": [{"amount": decimal.Decimal("10.50")}]})
    out = rs._rows_many(bq, {"a": "SELECT AMT"})
    assert out["a"] == [{"amount": 10.5}]
    assert isinstance(out["a"][0]["amount"], float)


# ---- full report ----


@pytest.fixture
def fake_bq(monkeypatch):
    bq = FakeBQ(responses={
        "SUM(overall_count)": [{"v": 1000}],
        "SUM(record_linked_count)": [{"v": 500}],
        "COUNT(DISTINCT hfr_id)": [{"v": 42}],
        "state_name AS state": [{"state": "Bihar", "abha_created": 900}],
    })
    monkeypatch.setattr(rs, "get_bigquery_client", lambda: bq)
    return bq


def test_build_weekly_report_has_every_expected_section(fake_bq):
    report = rs.build_weekly_report(date(2025, 4, 1), date(2025, 4, 8), llm=None)
    for key in (
        "period", "kpis", "abha_by_state", "scan_share_by_state", "linked_by_state",
        "facilities_by_ownership", "facilities_by_type", "hpr_by_type",
        "scan_pay_by_status", "links_by_bridge", "bridge_by_status", "analysis",
    ):
        assert key in report, f"missing section: {key}"


def test_build_weekly_report_includes_week_over_week(fake_bq):
    report = rs.build_weekly_report(date(2025, 4, 1), date(2025, 4, 8), llm=None)
    wow = report["kpis"]["wow"]
    for metric in ("abha_created", "records_linked", "scan_share_txns", "scan_pay_txns"):
        assert metric in wow
        assert set(wow[metric]) == {"prev", "change", "pct"}


def test_build_weekly_report_queries_the_previous_period_too(fake_bq):
    rs.build_weekly_report(date(2025, 4, 8), date(2025, 4, 15), llm=None)
    joined = "\n".join(fake_bq.seen)
    # The previous window is the same length, immediately before.
    assert "2025-04-01" in joined, "should query the preceding week for WoW"
    assert "2025-04-15" in joined


def test_build_weekly_report_skips_the_llm_summary_when_there_is_no_activity(monkeypatch):
    quiet = FakeBQ()  # every scalar returns 0
    monkeypatch.setattr(rs, "get_bigquery_client", lambda: quiet)

    class ExplodingLLM:
        def generate_json(self, *a, **k):
            raise AssertionError("must not call the LLM for an empty week")

    report = rs.build_weekly_report(date(2025, 4, 1), date(2025, 4, 8), ExplodingLLM())
    assert report["analysis"] is None


def test_build_weekly_report_collapses_the_district_master_before_joining(fake_bq):
    # Joining the raw master on state_code alone fans out one row per district.
    rs.build_weekly_report(date(2025, 4, 1), date(2025, 4, 8), llm=None)
    joins = [q for q in fake_bq.seen if "state_district_master" in q]
    assert joins, "expected at least one query against the master"
    for q in joins:
        assert "SELECT DISTINCT state_code, state_name" in q
