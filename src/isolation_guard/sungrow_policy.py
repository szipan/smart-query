"""阳光云（sungrow）表隔离策略登记（design.md §5A.2，Requirements 4/14.1）。

依据 `sungrow sql ddl.docx` / `Starrocks SQL DML.docx` 的真实表结构初始登记。
未登记的表按 TablePolicy 默认 fail-closed 拒绝（新表未登记前不会被放行）。

> 注意：此清单为**初始登记**，随业务接入逐表补充；ps_type 本期不作隔离维度。
"""

from __future__ import annotations

from isolation_guard.models import ScopeDim
from isolation_guard.policy import ColTransform, ColumnBinding, TablePolicy

_PS = ScopeDim.PS
_ORG = ScopeDim.ORG
_IDENT = ColTransform.IDENTITY
_FROM_KEY = ColTransform.PS_ID_FROM_PS_KEY


# 受控表 → 列绑定
_CONTROLLED: dict[str, ColumnBinding] = {
    # —— 电站维度 · 直接 ps_id 列（IDENTITY）——
    "fact_power_station_kpi_month": ColumnBinding(_PS, "ps_id", _IDENT),
    "fact_power_station_kpi_year": ColumnBinding(_PS, "ps_id", _IDENT),
    "fact_power_cusp_peak_valley_flat_report_month": ColumnBinding(_PS, "ps_id", _IDENT),
    "fact_power_cusp_peak_valley_flat_report_year": ColumnBinding(_PS, "ps_id", _IDENT),
    "power_statistical_data": ColumnBinding(_PS, "ps_id", _IDENT),
    "data_point_val": ColumnBinding(_PS, "ps_id", _IDENT),
    "data_point_source": ColumnBinding(_PS, "ps_id", _IDENT),
    "data_history_source": ColumnBinding(_PS, "ps_id", _IDENT),
    "power_station": ColumnBinding(_PS, "ps_id", _IDENT),

    # —— 电站维度 · 仅 ps_key 列，前缀取 ps_id（PS_ID_FROM_PS_KEY）——
    "bus_points_day": ColumnBinding(_PS, "ps_key", _FROM_KEY),
    "bus_points_month": ColumnBinding(_PS, "ps_key", _FROM_KEY),
    "bus_points_year": ColumnBinding(_PS, "ps_key", _FROM_KEY),

    # —— 组织维度 · org_id 列（IDENTITY）——
    "fact_organization_kpi_month": ColumnBinding(_ORG, "org_id", _IDENT),
    "fact_organization_kpi_year": ColumnBinding(_ORG, "org_id", _IDENT),
    "fact_organization_kpi_15_min": ColumnBinding(_ORG, "org_id", _IDENT),
    "fact_organization_kpi_hour": ColumnBinding(_ORG, "org_id", _IDENT),
}


# 非受控白名单（维表/字典表，无租户数据，原样透传）
_UNCONTROLLED: frozenset[str] = frozenset({
    "sys_country",
    "sys_timezone",
    "sys_area_code",
    "sys_code_value",
    "sys_administrative_division_config",
    "sys_translation",
})


def sungrow_table_policy() -> TablePolicy:
    """返回阳光云初始表策略；未登记表默认 fail-closed。"""
    return TablePolicy(
        controlled=dict(_CONTROLLED),
        uncontrolled=_UNCONTROLLED,
    )
