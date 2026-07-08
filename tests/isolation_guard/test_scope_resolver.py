"""Task 5 单测：ScopeResolver 子查询构造 + PermissionFilterSpec 占位。

Requirements 1.4, 1.6, 1.7, 15.1~15.6, 18.1~18.3。
"""

from isolation_guard.models import CallerIdentity, ScopeDim
from isolation_guard.scope import PermissionFilterSpec, ScopeResolver


def _resolve(user_id="1001", spec=None):
    return ScopeResolver(filter_spec=spec).resolve(CallerIdentity(user_id=user_id))


def test_ps_subquery_contains_three_sources() -> None:
    scope = _resolve()
    sql = scope.source_for(ScopeDim.PS).subquery.sql(dialect="starrocks")
    assert "sys_user_ps_share" in sql
    assert "sys_org_all_sub_org" in sql
    assert "power_station_org" in sql
    assert "power_station_public_share" in sql
    assert "1001" in sql


def test_org_subquery_uses_closure() -> None:
    scope = _resolve()
    sql = scope.source_for(ScopeDim.ORG).subquery.sql(dialect="starrocks")
    assert "sys_user_org" in sql
    assert "sys_org_all_sub_org" in sql


def test_placeholder_defaults_to_deny_not_allow_all() -> None:
    scope = _resolve()
    sql = scope.source_for(ScopeDim.PS).subquery.sql(dialect="starrocks")
    assert "FALSE" in sql.upper()
    assert "TRUE" not in sql.upper()


def test_allow_dev_mode_opt_in() -> None:
    spec = PermissionFilterSpec(placeholder_mode="allow_dev")
    scope = _resolve(spec=spec)
    sql = scope.source_for(ScopeDim.PS).subquery.sql(dialect="starrocks")
    assert "TRUE" in sql.upper()


def test_configured_filter_replaces_placeholder() -> None:
    spec = PermissionFilterSpec(share_filter="is_active = 1", org_filter="1 = 1", public_filter="is_public_share = 1")
    scope = _resolve(spec=spec)
    sql = scope.source_for(ScopeDim.PS).subquery.sql(dialect="starrocks")
    assert "is_active" in sql
    assert "is_public_share" in sql


def test_string_user_id_rendered_as_string_literal() -> None:
    scope = _resolve(user_id="abc123x")
    sql = scope.source_for(ScopeDim.PS).subquery.sql(dialect="starrocks")
    assert "'abc123x'" in sql


def test_deterministic_same_user_same_sql() -> None:
    a = _resolve().source_for(ScopeDim.PS).subquery.sql(dialect="starrocks")
    b = _resolve().source_for(ScopeDim.PS).subquery.sql(dialect="starrocks")
    assert a == b


def test_resolver_does_not_touch_db() -> None:
    scope = _resolve()
    assert scope.ps_source is not None and scope.org_source is not None
