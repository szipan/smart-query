"""Task 6 单测：隔离注入器（Requirements 3.x, 4.4, 16.x, 17.x）。"""

import sqlglot
import pytest

from isolation_guard.errors import UnmappedControlledTableError
from isolation_guard.injector import SqlglotIsolationInjector
from isolation_guard.models import CallerIdentity
from isolation_guard.scope import ScopeResolver
from isolation_guard.sungrow_policy import sungrow_table_policy

D = "starrocks"
INJ = SqlglotIsolationInjector()
POLICY = sungrow_table_policy()


def _scope(user_id="1001"):
    return ScopeResolver().resolve(CallerIdentity(user_id=user_id))


def _inject(sql: str):
    ast = sqlglot.parse_one(sql, dialect=D)
    new_ast, report = INJ.inject(ast, _scope(), POLICY, D)
    return new_ast.sql(dialect=D), report


def test_ps_id_table_wrapped_with_subquery_predicate() -> None:
    out, rep = _inject("SELECT ps_id FROM fact_power_station_kpi_month WHERE date_id = 20260501")
    assert "(SELECT * FROM fact_power_station_kpi_month WHERE ps_id IN (" in out
    assert "sys_user_ps_share" in out
    assert "fact_power_station_kpi_month" in rep.injected_tables


def test_alias_preserved() -> None:
    out, _ = _inject("SELECT g.ps_id FROM fact_power_station_kpi_month g")
    assert ") AS g" in out


def test_no_alias_uses_table_name_as_alias() -> None:
    out, _ = _inject("SELECT ps_id FROM fact_power_station_kpi_month")
    assert ") AS fact_power_station_kpi_month" in out


def test_ps_key_table_uses_prefix_cast() -> None:
    out, _ = _inject("SELECT * FROM bus_points_day WHERE day = 20260501")
    assert "CAST(SPLIT_PART(ps_key, '_', 1) AS BIGINT) IN (" in out


def test_org_table_uses_org_subquery() -> None:
    out, _ = _inject("SELECT p83024 FROM fact_organization_kpi_month")
    assert "org_id IN (" in out
    assert "sys_user_org" in out


def test_uncontrolled_passthrough() -> None:
    out, rep = _inject("SELECT * FROM sys_country")
    assert "sys_country" in rep.passthrough_tables
    assert "sys_user_ps_share" not in out


def test_unmapped_table_fail_closed() -> None:
    with pytest.raises(UnmappedControlledTableError):
        _inject("SELECT * FROM some_unknown_fact")


def test_join_each_controlled_table_wrapped() -> None:
    out, rep = _inject(
        "SELECT * FROM fact_power_station_kpi_month k "
        "JOIN fact_organization_kpi_month o ON k.ps_id = o.org_id"
    )
    assert "fact_power_station_kpi_month" in rep.injected_tables
    assert "fact_organization_kpi_month" in rep.injected_tables
    assert "ps_id IN (" in out and "org_id IN (" in out


def test_cte_reference_not_wrapped() -> None:
    out, rep = _inject(
        "WITH c AS (SELECT * FROM fact_power_station_kpi_month) SELECT * FROM c"
    )
    assert "fact_power_station_kpi_month" in rep.injected_tables
    assert "c" not in rep.injected_tables


def test_self_join_both_wrapped_with_aliases() -> None:
    out, rep = _inject(
        "SELECT * FROM fact_power_station_kpi_month a "
        "JOIN fact_power_station_kpi_month b ON a.ps_id = b.ps_id"
    )
    assert ") AS a" in out and ") AS b" in out
    assert rep.injected_tables.count("fact_power_station_kpi_month") == 2


def test_deterministic_output() -> None:
    a, _ = _inject("SELECT ps_id FROM fact_power_station_kpi_month")
    b, _ = _inject("SELECT ps_id FROM fact_power_station_kpi_month")
    assert a == b
