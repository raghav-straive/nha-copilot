from datetime import date

import pytest

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


# ---- post-2011 splits: the named district must win ----
# A split leaves parent and child in the SAME state, so an across-states-only
# ambiguity check never fired and whichever entry was indexed first won. Asking
# about Udaipur silently answered about Salumbar.


@pytest.mark.parametrize(
    "asked,expected_code,expected_name",
    [
        ("Udaipur", 117, "Udaipur"),          # not Salumbar
        ("Barmer", 90, "Barmer"),             # not Balotra
        ("Nagaur", 110, "Nagaur"),            # not Didwana-Kuchaman
        ("Sultanpur", 185, "Sultanpur"),      # not Amethi
        ("Sangrur", 43, "Sangrur"),           # not Malerkotla
        ("Ferozepur", 31, "Ferozepur"),       # not Fazilka
    ],
)
def test_split_parent_resolves_to_the_district_named(asked, expected_code, expected_name):
    r = get_geography().resolve(asked)
    assert r.status == "resolved", f"{asked} should resolve, not {r.status}"
    assert r.resolved is not None
    assert r.resolved.lgd_code == expected_code
    assert r.resolved.name == expected_name


def test_cross_state_ambiguity_is_still_reported():
    # Hamirpur exists in both Himachal Pradesh and Uttar Pradesh: genuinely
    # undecidable, so it must ask rather than pick one.
    r = get_geography().resolve("Hamirpur")
    assert r.status == "ambiguous"
    states = {m.state_name for m in r.matches}
    assert len(states) > 1


def test_renamed_district_still_resolves_via_its_old_name():
    # An alias that is the only candidate should resolve to the current district.
    r = get_geography().resolve("Barabanki")
    assert r.status == "resolved"
    assert r.resolved is not None


def test_detect_reports_the_district_that_was_named_not_an_alias_sibling():
    # detect() used to re-resolve by entries[0]'s canonical name, so text saying
    # "Ferozepur" could come back as Fazilka.
    hits = get_geography().detect("how many facilities in Ferozepur")
    names = {m.name for r in hits for m in r.matches if r.status == "resolved"}
    assert "Ferozepur" in names
    assert "Fazilka" not in names


def test_district_index_has_no_empty_key():
    # A workbook cell holding only punctuation normalised to "" and collected
    # two dozen unrelated districts under a single empty key.
    assert "" not in get_geography()._district_by_name


def test_detect_ignores_ordinary_questions():
    g = get_geography()
    for q in (
        "How many facilities are registered by ownership type?",
        "What is the payment success rate?",
        "Give me a breakup of professionals by type",
    ):
        assert g.detect(q) == [], f"false positive on: {q}"


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
