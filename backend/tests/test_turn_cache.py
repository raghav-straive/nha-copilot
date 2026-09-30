"""The repeated-question cache.

The two rules that keep it safe are the role in the key and the resolved period
in the key. Both get a test, because getting either wrong means serving a user
data they shouldn't see, or a number that is quietly out of date.
"""
import pytest

from app.nl_to_sql import pipeline as p


@pytest.fixture(autouse=True)
def clear():
    p.clear_turn_cache()
    yield
    p.clear_turn_cache()


def _resolved(period=None, geo=None):
    return {"geography": geo or [], "period": period, "ambiguous_geography": []}


def test_identical_question_and_role_hits():
    key = p._cache_key("how many facilities?", "analyst", _resolved())
    p._cache_put(key, p.TurnResult(action="answer", answer="42"))
    again = p._cache_key("how many facilities?", "analyst", _resolved())
    assert p._cache_get(again).answer == "42"


def test_key_ignores_case_and_extra_whitespace():
    a = p._cache_key("How  Many   Facilities?", "analyst", _resolved())
    b = p._cache_key("how many facilities?", "analyst", _resolved())
    assert a == b


def test_different_roles_do_not_share_an_answer():
    """RBAC changes what a query may return, so an admin's answer must never be
    handed to a viewer."""
    admin_key = p._cache_key("list facilities", "admin", _resolved())
    p._cache_put(admin_key, p.TurnResult(action="answer", answer="sensitive detail"))
    viewer_key = p._cache_key("list facilities", "viewer", _resolved())
    assert admin_key != viewer_key
    assert p._cache_get(viewer_key) is None


def test_different_resolved_periods_do_not_share_an_answer():
    """'Today' resolves to a concrete range, so the key changes by itself when
    the date rolls over rather than serving yesterday's number."""
    mon = p._cache_key("abha created today", "analyst",
                       _resolved({"start": "2025-04-01", "end": "2025-04-02"}))
    tue = p._cache_key("abha created today", "analyst",
                       _resolved({"start": "2025-04-02", "end": "2025-04-03"}))
    assert mon != tue
    p._cache_put(mon, p.TurnResult(action="answer", answer="monday"))
    assert p._cache_get(tue) is None


def test_different_geography_does_not_share_an_answer():
    bihar = p._cache_key("how many facilities", "analyst",
                         _resolved(geo=[{"level": "state", "lgd_code": 10}]))
    ap = p._cache_key("how many facilities", "analyst",
                      _resolved(geo=[{"level": "state", "lgd_code": 28}]))
    assert bihar != ap


def test_expired_entries_are_dropped():
    from datetime import datetime, timedelta, timezone

    key = p._cache_key("q", "analyst", _resolved())
    stale = datetime.now(timezone.utc) - p._TURN_TTL - timedelta(seconds=1)
    p._TURN_CACHE[key] = (stale, p.TurnResult(action="answer", answer="old"))
    assert p._cache_get(key) is None
    assert key not in p._TURN_CACHE, "an expired entry should be evicted on read"


def test_cache_is_bounded_and_evicts_oldest_first():
    for i in range(p._TURN_CACHE_MAX + 20):
        p._cache_put(
            p._cache_key(f"question {i}", "analyst", _resolved()),
            p.TurnResult(action="answer", answer=str(i)),
        )
    assert len(p._TURN_CACHE) <= p._TURN_CACHE_MAX
    # The earliest questions should have been pushed out.
    assert p._cache_get(p._cache_key("question 0", "analyst", _resolved())) is None
    last = p._TURN_CACHE_MAX + 19
    assert p._cache_get(p._cache_key(f"question {last}", "analyst", _resolved())) is not None


def test_reading_an_entry_keeps_it_fresh_in_the_lru():
    keys = [p._cache_key(f"q{i}", "analyst", _resolved()) for i in range(3)]
    for k in keys:
        p._cache_put(k, p.TurnResult(action="answer", answer="x"))
    p._cache_get(keys[0])                      # touch the oldest
    assert list(p._TURN_CACHE)[-1] == keys[0]  # now the most recent


def test_clear_empties_the_cache():
    p._cache_put(p._cache_key("q", "analyst", _resolved()),
                 p.TurnResult(action="answer", answer="x"))
    p.clear_turn_cache()
    assert len(p._TURN_CACHE) == 0
