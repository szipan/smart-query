"""Task 10：正确性属性测试（hypothesis）。覆盖 design.md §Correctness Properties 1~10。"""

import sqlglot
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from sqlglot import exp

from isolation_guard.guard import IsolationGuardFactory
from isolation_guard.models import CallerIdentity, GuardStatus
from isolation_guard.sungrow_policy import sungrow_table_policy


def _is_plain_table_name(name: str) -> bool:
    """能被解析为单一普通表标识符才算有效表名（排除 SQL 保留字，如 then/when/select）。

    保留字不是合法的非限定表标识符（真实表需反引号），不属于本属性要覆盖的范围。
    """
    try:
        stmts = sqlglot.parse(f"SELECT * FROM {name}", dialect="starrocks")
    except Exception:
        return False
    if len(stmts) != 1 or stmts[0] is None:
        return False
    tables = list(stmts[0].find_all(exp.Table))
    return len(tables) == 1 and tables[0].name.lower() == name.lower()

CONTROLLED_PS = ["fact_power_station_kpi_month", "data_point_val", "power_statistical_data"]
CONTROLLED_KEY = ["bus_points_day", "bus_points_month"]
CONTROLLED_ORG = ["fact_organization_kpi_month", "fact_organization_kpi_year"]
UNCONTROLLED = ["sys_country", "sys_timezone"]
WRITE_STMTS = ["INSERT INTO {t} VALUES (1)", "UPDATE {t} SET a=1", "DELETE FROM {t}", "DROP TABLE {t}"]


def _guard(user_id="1001"):
    return IsolationGuardFactory(policy=sungrow_table_policy()).for_identity(
        CallerIdentity(user_id=user_id)
    )


@settings(max_examples=50)
@given(t=st.sampled_from(CONTROLLED_PS + CONTROLLED_ORG + CONTROLLED_KEY))
def test_p1_determinism(t: str) -> None:
    sql = f"SELECT * FROM {t}"
    a = _guard().guard(sql, "r").safe_sql
    b = _guard().guard(sql, "r").safe_sql
    assert a == b


@settings(max_examples=50)
@given(t=st.sampled_from(CONTROLLED_PS + CONTROLLED_ORG + CONTROLLED_KEY))
def test_p2_controlled_always_injected(t: str) -> None:
    r = _guard().guard(f"SELECT * FROM {t}", "r")
    assert r.status is GuardStatus.ALLOWED
    assert t in r.injected_tables
    assert " IN (" in r.safe_sql


@settings(max_examples=50)
@given(t=st.sampled_from(CONTROLLED_PS), tpl=st.sampled_from(WRITE_STMTS))
def test_p4_read_only_enforced(t: str, tpl: str) -> None:
    r = _guard().guard(tpl.format(t=t), "r")
    assert r.status is GuardStatus.REJECTED
    assert r.safe_sql is None


@settings(max_examples=30)
@given(name=st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=3, max_size=20))
def test_p8_fail_closed_on_unmapped(name: str) -> None:
    assume(_is_plain_table_name(name))  # 排除 SQL 保留字等非法标识符
    policy = sungrow_table_policy()
    if policy.classify(name).kind == "must_control_unmapped":
        r = _guard().guard(f"SELECT * FROM {name}", "r")
        assert r.status is GuardStatus.REJECTED
        assert r.rejection_kind == "UnmappedControlledTableError"


@settings(max_examples=50)
@given(uid=st.text(min_size=1, max_size=12))
def test_p9_injection_safe(uid: str) -> None:
    r = _guard(user_id=uid).guard("SELECT * FROM fact_power_station_kpi_month", "r")
    assert r.status is GuardStatus.ALLOWED
    import sqlglot

    parsed = sqlglot.parse(r.safe_sql, dialect="starrocks")
    assert len([s for s in parsed if s is not None]) == 1


@settings(max_examples=30)
@given(t=st.sampled_from(UNCONTROLLED))
def test_uncontrolled_passthrough(t: str) -> None:
    r = _guard().guard(f"SELECT * FROM {t}", "r")
    assert r.status is GuardStatus.ALLOWED
    assert t in r.passthrough_tables
    assert "sys_user_ps_share" not in r.safe_sql
