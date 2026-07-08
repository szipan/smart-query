"""Task 11：边界与集成校验。

- L5 本期零 DB 连接：门面不持有任何数据库连接对象。
- 端到端拼装：输入隔离前 SQL → 输出带权限子查询的 safe_sql（sungrow 真实表名 + 复杂结构）。
"""

import sqlglot

from isolation_guard.guard import IsolationGuardFactory
from isolation_guard.models import CallerIdentity, GuardStatus
from isolation_guard.sungrow_policy import sungrow_table_policy


def _guard(user_id="1001"):
    return IsolationGuardFactory(policy=sungrow_table_policy()).for_identity(
        CallerIdentity(user_id=user_id)
    )


def test_guard_holds_no_db_connection() -> None:
    g = _guard()
    for attr in vars(g):
        val = getattr(g, attr)
        assert not hasattr(val, "connect"), f"{attr} 疑似持有 DB 连接"
        assert not hasattr(val, "execute"), f"{attr} 疑似可执行 SQL"


def test_end_to_end_complex_query() -> None:
    sql = (
        "WITH recent AS (SELECT * FROM data_point_val WHERE minutes > 0) "
        "SELECT r.ps_id, o.p83024, c.name "
        "FROM recent r "
        "JOIN fact_organization_kpi_month o ON r.ps_id = o.org_id "
        "JOIN sys_country c ON c.id = 1 "
        "JOIN bus_points_day b ON b.ps_key = r.ps_key"
    )
    r = _guard().guard(sql, "req-e2e")
    assert r.status is GuardStatus.ALLOWED
    out = r.safe_sql
    assert "data_point_val" in r.injected_tables
    assert "fact_organization_kpi_month" in r.injected_tables
    assert "bus_points_day" in r.injected_tables
    assert "sys_country" in r.passthrough_tables
    assert "ps_id IN (" in out
    assert "org_id IN (" in out
    assert "CAST(SPLIT_PART(ps_key, '_', 1) AS BIGINT) IN (" in out
    parsed = sqlglot.parse(out, dialect="starrocks")
    assert len([s for s in parsed if s is not None]) == 1
