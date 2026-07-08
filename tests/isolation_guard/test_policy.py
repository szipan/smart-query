"""Task 3.2 单测：表策略与列绑定分类（Requirements 4.1, 4.2, 4.3, 4.5, 4.6）。"""

from isolation_guard.models import ScopeDim
from isolation_guard.policy import (
    ColTransform,
    ColumnBinding,
    TablePolicy,
    UnknownTablePolicy,
)


def _policy(**kw) -> TablePolicy:
    controlled = {
        "fact_power_station_kpi_month": ColumnBinding(ScopeDim.PS, "ps_id"),
        "bus_points_day": ColumnBinding(ScopeDim.PS, "ps_key", ColTransform.PS_ID_FROM_PS_KEY),
        "fact_organization_kpi_month": ColumnBinding(ScopeDim.ORG, "org_id"),
    }
    return TablePolicy(controlled=controlled, uncontrolled=frozenset({"sys_country"}), **kw)


def test_classify_controlled_returns_binding() -> None:
    p = _policy()
    tc = p.classify("fact_power_station_kpi_month")
    assert tc.kind == "controlled"
    assert tc.binding.dimension is ScopeDim.PS
    assert tc.binding.column == "ps_id"


def test_classify_ps_key_binding_transform() -> None:
    p = _policy()
    tc = p.classify("bus_points_day")
    assert tc.kind == "controlled"
    assert tc.binding.transform is ColTransform.PS_ID_FROM_PS_KEY


def test_classify_uncontrolled() -> None:
    p = _policy()
    assert p.classify("sys_country").kind == "uncontrolled"


def test_unknown_table_fail_closed_by_default() -> None:
    p = _policy()
    assert p.classify("unknown_fact").kind == "must_control_unmapped"


def test_unknown_table_pass_through_when_configured() -> None:
    p = _policy(unknown_table_policy=UnknownTablePolicy.PASS_THROUGH)
    assert p.classify("unknown_fact").kind == "uncontrolled"


def test_case_insensitive_match() -> None:
    p = _policy(case_insensitive=True)
    assert p.classify("FACT_POWER_STATION_KPI_MONTH").kind == "controlled"


def test_case_sensitive_miss_becomes_unmapped() -> None:
    p = _policy(case_insensitive=False)
    assert p.classify("FACT_POWER_STATION_KPI_MONTH").kind == "must_control_unmapped"
