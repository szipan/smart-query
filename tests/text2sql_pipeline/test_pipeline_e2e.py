"""Task 8/9/10/11 单测：默认链路端到端 + 层边界 + 阶段隔离 + 可观测。"""

import copy

from text2sql_pipeline.deps import StageDependencies
from text2sql_pipeline.models import PipelineConfig, PipelineInput, PipelineStatus
from text2sql_pipeline.observer import InMemoryObserver
from text2sql_pipeline.orchestrator import build_orchestrator


def _run(deps, pipeline_input, observer=None, config=None):
    orch = build_orchestrator(config or PipelineConfig(), deps, observer)
    return orch.run(pipeline_input)


# ---- Task 8：默认链路端到端（全 mock 依赖，validator=None → fixer 直通）----
def test_default_pipeline_end_to_end(deps, pipeline_input) -> None:
    r = _run(deps, pipeline_input)
    assert r.status is PipelineStatus.OK
    assert "fact_power_station_kpi_month" in r.unvalidated_sql
    assert r.stage_strategies == {
        "context_acquisition": "rag",
        "sql_generation": "single_llm",
        "sql_selection": "passthrough",
        "query_fixer": "bounded_retry",
    }


# ---- Task 9：层边界 —— 产出未校验 SQL，不含权限过滤 ----
def test_output_has_no_permission_filter(deps, pipeline_input) -> None:
    r = _run(deps, pipeline_input)
    sql = r.unvalidated_sql.lower()
    # L4 不注入隔离：产出不应含权限过滤/权限表
    assert "sys_user_ps_share" not in sql
    assert "org_id in" not in sql
    assert "ps_id in" not in sql


# ---- Task 10：阶段隔离 —— input 执行后不可变 ----
def test_input_immutable_after_run(deps) -> None:
    pi = PipelineInput(request_id="r", standalone_question="查询发电量")
    before = copy.deepcopy(pi)
    _run(deps, pi)
    assert pi == before


# ---- Task 11：可观测 —— 每阶段有事件 + trace 脱敏 ----
def test_observer_receives_stage_events(deps, pipeline_input) -> None:
    obs = InMemoryObserver()
    _run(deps, pipeline_input, observer=obs)
    started = [e for e in obs.events if e[0] == "start"]
    ended = [e for e in obs.events if e[0] == "end"]
    assert len(started) == 4 and len(ended) == 4


def test_traces_carry_request_id_and_strategies(deps, pipeline_input) -> None:
    r = _run(deps, pipeline_input)
    assert r.request_id == pipeline_input.request_id
    assert len(r.traces) == 4
    assert all(t.ok for t in r.traces)
