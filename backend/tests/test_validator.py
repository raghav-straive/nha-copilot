from app.sql_safety.validator import MAX_ROWS, enforce_row_limit, validate_sql

FAC = "`p.d.health_facility_registry`"


def test_accepts_plain_select():
    assert validate_sql(f"SELECT COUNT(DISTINCT hfr_id) AS n FROM {FAC}").ok


def test_accepts_with_cte():
    sql = (
        f"WITH x AS (SELECT hfr_id, facility_ownership FROM {FAC}) "
        "SELECT facility_ownership, COUNT(DISTINCT hfr_id) AS n FROM x GROUP BY facility_ownership"
    )
    assert validate_sql(sql).ok


def test_rejects_delete():
    assert not validate_sql(f"DELETE FROM {FAC}").ok


def test_rejects_drop():
    assert not validate_sql(f"DROP TABLE {FAC}").ok


def test_rejects_update():
    assert not validate_sql(f"UPDATE {FAC} SET registered_count = 0").ok


def test_rejects_multi_statement():
    assert not validate_sql("SELECT 1; DROP TABLE t").ok


def test_rejects_abha_address_pii():
    r = validate_sql("SELECT abha_address FROM `p.d.scan_pay_count`")
    assert not r.ok
    assert "abha_address" in r.pii_hit


def test_rejects_abha_address_alias():
    r = validate_sql("SELECT abha_address AS a FROM `p.d.scan_pay_count`")
    assert not r.ok
    assert "abha_address" in r.pii_hit


def test_allows_facility_identity():
    # Facility name/id/address are PUBLIC in the ABDM dataset — must be allowed.
    assert validate_sql(
        f"SELECT facility_name, hfr_id, facility_address FROM {FAC} LIMIT 10"
    ).ok


def test_allows_payment_amount_aggregate():
    assert validate_sql(
        "SELECT payment_status, SUM(payment_amount) AS amt "
        "FROM `p.d.scan_pay_count` GROUP BY payment_status"
    ).ok


# ---- star projections ----
# A star names no columns, so the PII scan here and the tier check in
# rbac_filter both had nothing to inspect and let it through.


def test_rejects_star_projection():
    assert not validate_sql(f"SELECT * FROM {FAC} LIMIT 50").ok


def test_rejects_qualified_star_projection():
    assert not validate_sql(f"SELECT t.* FROM {FAC} t LIMIT 50").ok


def test_rejects_star_mixed_with_columns():
    assert not validate_sql(
        f"SELECT a.*, b.state_name FROM {FAC} a "
        "JOIN `p.d.state_district_master` b USING (state_code)"
    ).ok


def test_rejects_star_in_union_branch():
    assert not validate_sql(f"SELECT * FROM {FAC} UNION ALL SELECT * FROM {FAC}").ok


def test_rejects_star_through_cte():
    assert not validate_sql(
        f"WITH x AS (SELECT hfr_id FROM {FAC}) SELECT * FROM x"
    ).ok


def test_still_accepts_count_star():
    # Regression guard: COUNT(*) contains an exp.Star node, so a tree-wide star
    # search would reject it — along with four queries in report/service.py.
    assert validate_sql(f"SELECT COUNT(*) AS n FROM {FAC}").ok


def test_still_accepts_count_star_grouped():
    assert validate_sql(
        f"SELECT facility_ownership, COUNT(*) AS n FROM {FAC} GROUP BY facility_ownership"
    ).ok


def test_allows_star_inside_subquery_with_explicit_outer():
    # Harmless: the outer projection names its columns, so the PII and RBAC
    # scans still see them.
    assert validate_sql(f"SELECT hfr_id FROM (SELECT * FROM {FAC}) LIMIT 10").ok


# ---- row cap ----


def test_row_limit_added_when_absent():
    out = enforce_row_limit(f"SELECT hfr_id FROM {FAC}")
    assert f"LIMIT {MAX_ROWS}" in out


def test_row_limit_respects_tighter_existing_limit():
    sql = f"SELECT hfr_id FROM {FAC} LIMIT 10"
    assert enforce_row_limit(sql) == sql


def test_row_limit_tightens_looser_existing_limit():
    out = enforce_row_limit(f"SELECT hfr_id FROM {FAC} LIMIT 999999")
    assert f"LIMIT {MAX_ROWS}" in out


def test_row_limit_handles_cte_and_union():
    for sql in (
        f"WITH x AS (SELECT hfr_id FROM {FAC}) SELECT hfr_id FROM x",
        "SELECT a FROM `p.d.f` UNION ALL SELECT a FROM `p.d.g`",
    ):
        assert f"LIMIT {MAX_ROWS}" in enforce_row_limit(sql)


def test_row_limit_leaves_unparseable_sql_alone():
    # Better to skip the cap than to corrupt a query.
    assert enforce_row_limit("this is not sql") == "this is not sql"
