from datetime import date

from app.semantic.geography import get_geography
from app.semantic.time_resolver import get_time_resolver


def test_aurangabad_is_ambiguous():
    r = get_geography().resolve("Aurangabad")
    assert r.status == "ambiguous"
    assert "which did you mean" in (r.message or "").lower()


def test_state_resolves_to_lgd_code():
    r = get_geography().resolve("Bihar")
    assert r.status == "resolved"
    assert r.resolved is not None
    assert r.resolved.state_code == 10  # Bihar LGD code


def test_alias_orissa_to_odisha():
    r = get_geography().resolve("Orissa")
    assert r.status == "resolved"
    assert "odisha" in r.resolved.name.lower()


def test_detect_finds_state_in_sentence():
    hits = get_geography().detect("How many facilities are registered in Gujarat?")
    names = {m.name for r in hits for m in r.matches}
    assert any("gujarat" in n.lower() for n in names)


def test_quarter_range_and_window_flag():
    tr = get_time_resolver().resolve("Q2 2023-24", today=date(2026, 7, 6))
    assert tr.start == date(2023, 7, 1)
    assert tr.end == date(2023, 10, 1)
    assert tr.outside_data_window  # 2023 is before the ABDM data window


def test_2026_year_in_window():
    tr = get_time_resolver().resolve("in 2026", today=date(2026, 7, 6))
    assert tr.start == date(2026, 1, 1)
    assert not tr.outside_data_window


def test_last_month_resolves():
    tr = get_time_resolver().resolve("last month", today=date(2026, 6, 15))
    assert tr.status == "resolved"
    assert tr.start == date(2026, 5, 1)
    assert tr.end == date(2026, 6, 1)


# ---- a bare "q" must not swallow the year ----
# The quarter guard used to test `"q" not in text`, which matched the letter
# inside ordinary words and silently dropped the year from the resolved context.


import pytest  # noqa: E402


@pytest.mark.parametrize(
    "question",
    [
        "How many unique facilities were registered in 2026?",
        "Show me the frequency of ABHA creation in 2026",
        "Run a query for facilities in 2026",
        "What quantity of records were linked in 2026?",
        "How many equal splits in 2026?",
        "Which districts had the highest frequency in 2026?",
    ],
)
def test_year_survives_an_incidental_letter_q(question):
    tr = get_time_resolver().resolve(question, today=date(2026, 7, 6))
    assert tr.status == "resolved", f"year was dropped from: {question}"
    assert tr.start == date(2026, 1, 1)
    assert tr.end == date(2027, 1, 1)


def test_a_real_quarter_reference_still_wins_over_the_bare_year():
    # "Q2 2025-26" must stay a quarter, not become the whole of 2025.
    tr = get_time_resolver().resolve("Q2 2025-26", today=date(2026, 7, 6))
    assert tr.start == date(2025, 7, 1)
    assert tr.end == date(2025, 10, 1)


def test_the_word_quarter_suppresses_a_bare_year():
    tr = get_time_resolver().resolve("facilities by quarter in 2026", today=date(2026, 7, 6))
    assert tr.status == "none", "an unqualified quarter question should not resolve to a year"


# ---- explicit ISO dates ----
# The financial-year pattern used to match inside an ISO date, so "2026-03-15"
# resolved to FY2026-27 — a range that does not even contain that date.


def test_single_iso_date_resolves_to_that_day():
    tr = get_time_resolver().resolve("ABHA created on 2026-03-15", today=date(2026, 7, 6))
    assert tr.start == date(2026, 3, 15)
    assert tr.end == date(2026, 3, 16), "end is exclusive, so one day later"


def test_iso_date_range_is_inclusive_of_the_end_the_user_named():
    tr = get_time_resolver().resolve(
        "facilities registered between 2026-04-01 and 2026-06-30", today=date(2026, 7, 6)
    )
    assert tr.start == date(2026, 4, 1)
    assert tr.end == date(2026, 7, 1), "30 June inclusive -> 1 July exclusive"


@pytest.mark.parametrize("joiner", ["to", "and", "till", "until", "through", "-"])
def test_iso_range_joiners(joiner):
    tr = get_time_resolver().resolve(
        f"records linked 2026-02-01 {joiner} 2026-02-28", today=date(2026, 7, 6)
    )
    assert tr.start == date(2026, 2, 1)
    assert tr.end == date(2026, 3, 1)


def test_fy_shorthand_still_works():
    tr = get_time_resolver().resolve("show data for 2025-26", today=date(2026, 7, 6))
    assert tr.start == date(2025, 4, 1)
    assert tr.end == date(2026, 4, 1)


def test_impossible_iso_date_does_not_crash():
    # Falls through rather than raising; better no period than a wrong one.
    tr = get_time_resolver().resolve("data for 2026-13-45", today=date(2026, 7, 6))
    assert tr.status in {"none", "resolved"}


def test_reversed_iso_range_is_ignored():
    tr = get_time_resolver().resolve(
        "between 2026-06-30 and 2026-04-01", today=date(2026, 7, 6)
    )
    # Not treated as a range; must not produce start > end.
    if tr.status == "resolved":
        assert tr.start < tr.end
