from app.sql_safety.rbac_filter import check_rbac

FAC = "`p.d.health_facility_registry`"


def _sql(cols, group=None):
    g = f" GROUP BY {group}" if group else ""
    return f"SELECT {cols} FROM {FAC}{g}"


def test_viewer_blocked_on_district():
    r = check_rbac(_sql("district_name, COUNT(*) c", "district_name"), "viewer")
    assert not r.allowed


def test_viewer_allowed_state_level():
    r = check_rbac(_sql("state_code, COUNT(*) c", "state_code"), "viewer")
    assert r.allowed


def test_analyst_allowed_district():
    r = check_rbac(_sql("district_code, COUNT(*) c", "district_code"), "analyst")
    assert r.allowed


def test_analyst_blocked_on_facility_listing():
    r = check_rbac(_sql("facility_name, COUNT(*) c", "facility_name"), "analyst")
    assert not r.allowed


def test_analyst_allowed_facility_count_by_id():
    # COUNT(DISTINCT hfr_id) is the standard facility count — allowed at every tier.
    r = check_rbac(_sql("state_code, COUNT(DISTINCT hfr_id) AS n", "state_code"), "analyst")
    assert r.allowed


def test_senior_allowed_facility():
    r = check_rbac(_sql("facility_name, registered_count", None), "senior_analyst")
    assert r.allowed


def test_admin_allowed_everything():
    r = check_rbac(_sql("facility_name, hfr_id, district_name"), "admin")
    assert r.allowed


def test_rbac_denies_unparseable_sql():
    # Fails CLOSED: this is a security control and must not assume the validator
    # already vetted the input (the two use different sqlglot entry points).
    for bad in ("SELECT FROM WHERE ((((", "this is not sql at all"):
        assert not check_rbac(bad, "viewer").allowed


def test_rbac_blocked_columns_defaults_to_list():
    # Was declared list[str] but defaulted to None, so callers formatting it in
    # a log line printed "None" instead of an empty list.
    assert check_rbac(_sql("state_code"), "viewer").blocked_columns == []


def test_star_projection_is_stopped_by_the_validator():
    # RBAC cannot see through a star (no exp.Column nodes to enumerate), so the
    # validator is the layer that must stop it. Assert the pair holds together.
    from app.sql_safety.validator import validate_sql

    sql = f"SELECT * FROM {FAC} LIMIT 50"
    assert check_rbac(sql, "viewer").allowed, "RBAC is column-based; star is invisible to it"
    assert not validate_sql(sql).ok, "the validator must reject the star"


def test_viewer_blocked_on_numeric_district_column():
    # `district` (numeric LGD code column in linked_facility / scan_pay_count)
    r = check_rbac(
        "SELECT district, COUNT(*) c FROM `p.d.scan_pay_count` GROUP BY district",
        "viewer",
    )
    assert not r.allowed
