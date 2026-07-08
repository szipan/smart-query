"""集成测试 L4 ↔ L5：Query Fixer 用真实 Layer 5 IsolationGuard 做静态校验。

验证跨层契约：L4 产出未校验 SQL；L5 validate_for_probe 能接受合规 SELECT、拒绝非法 SQL。
"""

from isolation_guard.guard import IsolationGuardFactory
from isolation_guard.models import CallerIdentity
from isolation_guard.sungrow_policy import sungrow_table_policy

from text2sql_pipeline.deps import StageDependencies
from text2sql_pipeline.models import PipelineConfig, PipelineInput, PipelineStatus
from text2sql_pipeline.orchestrator import build_orchestrator
from l4_fakes import FakeEmbedder, FakeLlm, FakeVectorStore, FakeDatasource


def _deps_with_real_l5(sql: str) -> StageDependencies:
    guard = IsolationGuardFactory(policy=sungrow_table_policy()).for_identity(
        CallerIdentity(user_id="1001")
    )
    return StageDependencies(
        embedder=FakeEmbedder(),
        vector_store=FakeVectorStore(),
        llm_client=FakeLlm(sql=sql),
        datasource=FakeDatasource(),
        validator=guard,  # 真实 Layer 5
    )


def test_l4_output_passes_real_l5_static_check() -> None:
    sql = "SELECT ps_id, p83022 FROM fact_power_station_kpi_month WHERE date_id = 20260501"
    deps = _deps_with_real_l5(sql)
    orch = build_orchestrator(PipelineConfig(), deps)
    r = orch.run(PipelineInput(request_id="r", standalone_question="本月各电站发电量"))

    assert r.status is PipelineStatus.OK
    # L4 产出未校验 SQL（不含隔离）
    assert "fact_power_station_kpi_month" in r.unvalidated_sql
    assert "sys_user_ps_share" not in r.unvalidated_sql

    # 而真实 L5 对同一 SQL 校验会注入隔离子查询（证明契约可用）
    safe = deps.validator.validate_for_probe(r.unvalidated_sql, "r")
    assert "sys_user_ps_share" in safe
    assert "ps_id IN (" in safe


def test_l4_illegal_sql_exhausts_via_real_l5() -> None:
    # LLM 产出非法（写操作）→ 真实 L5 静态校验拒绝 → Fixer 修复用尽（修复仍返回同样非法）
    deps = _deps_with_real_l5("DROP TABLE fact_power_station_kpi_month")
    orch = build_orchestrator(PipelineConfig(), deps)
    r = orch.run(PipelineInput(request_id="r", standalone_question="删表"))
    assert r.status is PipelineStatus.FIXER_EXHAUSTED
    assert r.unvalidated_sql is None
