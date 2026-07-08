"""Task 7/8 单测：门面 IsolationGuard + 审计（Requirements 7/9/12/13）。"""

import pytest

from isolation_guard.errors import IsolationGuardError
from isolation_guard.guard import IsolationGuardFactory
from isolation_guard.models import CallerIdentity, GuardStatus
from isolation_guard.observer import InMemoryObserver
from isolation_guard.sungrow_policy import sungrow_table_policy


def _guard(observer=None):
    factory = IsolationGuardFactory(policy=sungrow_table_policy(), observer=observer)
    return factory.for_identity(CallerIdentity(user_id="1001"))


def test_guard_allows_and_injects() -> None:
    g = _guard()
    r = g.guard("SELECT ps_id FROM fact_power_station_kpi_month", "req-1")
    assert r.status is GuardStatus.ALLOWED
    assert "sys_user_ps_share" in r.safe_sql
    assert "fact_power_station_kpi_month" in r.injected_tables
    assert r.request_id == "req-1"


def test_guard_rejects_write() -> None:
    g = _guard()
    r = g.guard("DELETE FROM fact_power_station_kpi_month", "req-2")
    assert r.status is GuardStatus.REJECTED
    assert r.safe_sql is None
    assert r.rejection_kind == "ReadOnlyViolation"


def test_guard_rejects_unmapped_table_fail_closed() -> None:
    g = _guard()
    r = g.guard("SELECT * FROM some_unknown_fact", "req-3")
    assert r.status is GuardStatus.REJECTED
    assert r.rejection_kind == "UnmappedControlledTableError"


def test_validate_for_probe_returns_safe_sql() -> None:
    g = _guard()
    safe = g.validate_for_probe("SELECT ps_id FROM fact_power_station_kpi_month", "req-4")
    assert "ps_id IN (" in safe


def test_validate_for_probe_raises_on_invalid() -> None:
    g = _guard()
    with pytest.raises(IsolationGuardError):
        g.validate_for_probe("DROP TABLE fact_power_station_kpi_month", "req-5")


def test_both_entrypoints_produce_identical_safe_sql() -> None:
    g = _guard()
    sql = "SELECT ps_id FROM fact_power_station_kpi_month"
    via_guard = g.guard(sql, "r").safe_sql
    g2 = _guard()
    via_probe = g2.validate_for_probe(sql, "r")
    assert via_guard == via_probe


def test_scope_cached_within_request() -> None:
    g = _guard()
    g.guard("SELECT ps_id FROM fact_power_station_kpi_month", "r")
    first = g._scope_cache
    g.guard("SELECT ps_id FROM data_point_val", "r")
    assert g._scope_cache is first


def test_observer_receives_decision_and_is_masked() -> None:
    obs = InMemoryObserver()
    g = _guard(observer=obs)
    g.guard("SELECT ps_id FROM fact_power_station_kpi_month", "req-obs")
    assert len(obs.decisions) == 1
    d = obs.decisions[0]
    assert d.request_id == "req-obs"
    assert d.status is GuardStatus.ALLOWED
    assert not hasattr(d, "ps_ids")


def test_rejected_also_emits_decision() -> None:
    obs = InMemoryObserver()
    g = _guard(observer=obs)
    g.guard("DELETE FROM fact_power_station_kpi_month", "req-rej")
    assert obs.decisions[0].status is GuardStatus.REJECTED
    assert obs.decisions[0].rejection_kind == "ReadOnlyViolation"


def test_reinject_per_user() -> None:
    f = IsolationGuardFactory(policy=sungrow_table_policy())
    a = f.for_identity(CallerIdentity(user_id="1001")).guard(
        "SELECT ps_id FROM fact_power_station_kpi_month", "r").safe_sql
    b = f.for_identity(CallerIdentity(user_id="2002")).guard(
        "SELECT ps_id FROM fact_power_station_kpi_month", "r").safe_sql
    assert "1001" in a and "2002" in b
    assert a != b
