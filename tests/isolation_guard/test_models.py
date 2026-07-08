"""Task 3.1 / 3.3 单测：身份、数据范围、结果与审计模型。"""

import sqlglot

from isolation_guard.models import (
    CallerIdentity,
    GuardResult,
    GuardStatus,
    IsolationDecision,
    ScopeDim,
)
from isolation_guard.scope import (
    DataScope,
    MaterializedScopeSource,
    SubqueryScopeSource,
)


def _sub(sql: str = "SELECT ps_id FROM sys_user_ps_share WHERE user_id = 1"):
    return sqlglot.parse_one(sql, dialect="starrocks")


def test_caller_identity_defaults() -> None:
    ident = CallerIdentity(user_id="1001")
    assert ident.user_id == "1001"
    assert ident.claims == {}
    assert ident.tenant is None


def test_datascope_source_for_returns_matching_dimension() -> None:
    ps = SubqueryScopeSource(dimension=ScopeDim.PS, subquery=_sub())
    org = SubqueryScopeSource(dimension=ScopeDim.ORG, subquery=_sub("SELECT org_id FROM sys_user_org"))
    scope = DataScope(ps_source=ps, org_source=org)
    assert scope.source_for(ScopeDim.PS) is ps
    assert scope.source_for(ScopeDim.ORG) is org


def test_subquery_scope_source_builds_in_predicate_with_parens() -> None:
    from sqlglot import exp

    src = SubqueryScopeSource(dimension=ScopeDim.PS, subquery=_sub())
    pred = src.build_in_predicate(exp.column("ps_id"))
    rendered = pred.sql(dialect="starrocks")
    assert rendered.startswith("ps_id IN (")
    assert "sys_user_ps_share" in rendered


def test_materialized_scope_source_not_implemented_this_phase() -> None:
    from sqlglot import exp

    src = MaterializedScopeSource(dimension=ScopeDim.PS, ids=frozenset({1, 2}))
    try:
        src.build_in_predicate(exp.column("ps_id"))
        assert False, "本期应抛 NotImplementedError"
    except NotImplementedError:
        pass


def test_guard_result_audit_counts_only() -> None:
    r = GuardResult(
        request_id="r1",
        status=GuardStatus.ALLOWED,
        safe_sql="SELECT 1",
        original_sql="SELECT 1",
        dialect="starrocks",
    )
    assert r.status is GuardStatus.ALLOWED
    assert r.deny_all is False


def test_isolation_decision_has_no_scope_ids() -> None:
    d = IsolationDecision(
        request_id="r1",
        user_id="1001",
        status=GuardStatus.ALLOWED,
        tables_seen=("gen",),
        tables_injected=("gen",),
        deny_all=False,
        dialect="starrocks",
        rejection_kind=None,
        timestamp=0.0,
    )
    assert not hasattr(d, "org_ids")
    assert not hasattr(d, "ps_ids")
