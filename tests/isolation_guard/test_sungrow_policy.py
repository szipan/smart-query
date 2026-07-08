"""Task 9 单测：sungrow 表登记（Requirements 4.1, 4.2, 4.3, 14.1）。"""

from isolation_guard.models import ScopeDim
from isolation_guard.policy import ColTransform
from isolation_guard.sungrow_policy import sungrow_table_policy

P = sungrow_table_policy()


def test_ps_id_table_controlled() -> None:
    tc = P.classify("fact_power_station_kpi_month")
    assert tc.kind == "controlled"
    assert tc.binding.dimension is ScopeDim.PS
    assert tc.binding.transform is ColTransform.IDENTITY


def test_ps_key_table_uses_prefix_transform() -> None:
    tc = P.classify("bus_points_day")
    assert tc.kind == "controlled"
    assert tc.binding.column == "ps_key"
    assert tc.binding.transform is ColTransform.PS_ID_FROM_PS_KEY


def test_org_table_controlled() -> None:
    tc = P.classify("fact_organization_kpi_month")
    assert tc.kind == "controlled"
    assert tc.binding.dimension is ScopeDim.ORG


def test_dimension_table_uncontrolled() -> None:
    assert P.classify("sys_country").kind == "uncontrolled"


def test_unregistered_table_fail_closed() -> None:
    assert P.classify("some_new_unregistered_table").kind == "must_control_unmapped"
