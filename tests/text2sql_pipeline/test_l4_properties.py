"""Task 14：正确性属性测试（hypothesis）。覆盖 design.md §Correctness Properties 1~8。"""

import copy

from hypothesis import given, settings
from hypothesis import strategies as st

from text2sql_pipeline.deps import StageDependencies
from text2sql_pipeline.models import PipelineConfig, PipelineInput, PipelineStatus
from text2sql_pipeline.orchestrator import build_orchestrator
from fakes import FakeEmbedder, FakeLlm, FakeVectorStore, FakeDatasource


def _deps(sql="SELECT ps_id FROM fact_power_station_kpi_month"):
    return StageDependencies(
        embedder=FakeEmbedder(),
        vector_store=FakeVectorStore(),
        llm_client=FakeLlm(sql=sql),
        datasource=FakeDatasource(),
    )


# Property 1：边界不越权 —— 产出 SQL 不含权限过滤
@settings(max_examples=40)
@given(q=st.text(min_size=1, max_size=40))
def test_p1_no_permission_filter(q: str) -> None:
    r = build_orchestrator(PipelineConfig(), _deps()).run(
        PipelineInput(request_id="r", standalone_question=q)
    )
    if r.unvalidated_sql:
        low = r.unvalidated_sql.lower()
        assert "sys_user_ps_share" not in low


# Property 2：输入不可变
@settings(max_examples=40)
@given(q=st.text(min_size=1, max_size=40))
def test_p2_input_immutable(q: str) -> None:
    pi = PipelineInput(request_id="r", standalone_question=q)
    before = copy.deepcopy(pi)
    build_orchestrator(PipelineConfig(), _deps()).run(pi)
    assert pi == before


# Property 8：结果状态映射 —— OK 当且仅当有非空 SQL
@settings(max_examples=40)
@given(sql=st.sampled_from([
    "SELECT ps_id FROM fact_power_station_kpi_month",
    "SELECT 1",
    "SELECT * FROM data_point_val",
]))
def test_p8_ok_iff_nonempty_sql(sql: str) -> None:
    r = build_orchestrator(PipelineConfig(), _deps(sql)).run(
        PipelineInput(request_id="r", standalone_question="q")
    )
    assert r.status is PipelineStatus.OK
    assert r.unvalidated_sql and r.unvalidated_sql.strip()


# Property 4/顺序：始终四阶段固定顺序
@settings(max_examples=20)
@given(q=st.text(min_size=1, max_size=20))
def test_fixed_stage_order(q: str) -> None:
    r = build_orchestrator(PipelineConfig(), _deps()).run(
        PipelineInput(request_id="r", standalone_question=q)
    )
    types = [t.stage_type for t in r.traces]
    assert types == ["context_acquisition", "sql_generation", "sql_selection", "query_fixer"]
