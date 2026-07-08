"""Task 6 单测：编排器（固定顺序、状态映射、异常终止）。Requirements 1/10/11。"""

import pytest

from text2sql_pipeline.deps import StageDependencies
from text2sql_pipeline.errors import StagePreconditionError, StrategyResolutionError
from text2sql_pipeline.models import (
    PipelineConfig,
    PipelineContext,
    PipelineInput,
    PipelineStatus,
    SqlCandidate,
    StageConfig,
)
from text2sql_pipeline.orchestrator import PipelineOrchestrator
from text2sql_pipeline.registry import StrategyRegistry


class _Stub:
    def __init__(self, stage_type, strategy_name, fn):
        self.stage_type = stage_type
        self.strategy_name = strategy_name
        self._fn = fn

    def run(self, ctx: PipelineContext) -> PipelineContext:
        return self._fn(ctx)


def _registry(order_log=None, fixer_sets_final=True, gen_candidate=True, raise_in=None):
    reg = StrategyRegistry()

    def mk(stage_type, name, fn):
        reg.register(stage_type, name, lambda deps, params: _Stub(stage_type, name, fn))

    def ca(ctx):
        if order_log is not None:
            order_log.append("context_acquisition")
        if raise_in == "context_acquisition":
            raise RuntimeError("boom")
        from text2sql_pipeline.models import RetrievedContext
        ctx.retrieved_context = RetrievedContext(strategy_name="rag")
        return ctx

    def gen(ctx):
        if order_log is not None:
            order_log.append("sql_generation")
        if gen_candidate:
            ctx.sql_candidates.append(SqlCandidate("SELECT 1", "g", "m"))
        return ctx

    def sel(ctx):
        if order_log is not None:
            order_log.append("sql_selection")
        if not ctx.sql_candidates:
            raise StagePreconditionError("sql_selection", "sql_candidates")
        ctx.selected_sql = ctx.sql_candidates[0]
        return ctx

    def fix(ctx):
        if order_log is not None:
            order_log.append("query_fixer")
        if fixer_sets_final:
            ctx.final_sql = ctx.selected_sql
        else:
            ctx.fix_attempts = 2
        return ctx

    mk("context_acquisition", "rag", ca)
    mk("sql_generation", "single_llm", gen)
    mk("sql_selection", "passthrough", sel)
    mk("query_fixer", "bounded_retry", fix)
    return reg


def _orch(reg, config=None):
    return PipelineOrchestrator(reg, StageDependencies(), config or PipelineConfig())


def _input():
    return PipelineInput(request_id="r", standalone_question="q")


def test_fixed_order() -> None:
    log = []
    r = _orch(_registry(order_log=log)).run(_input())
    assert log == ["context_acquisition", "sql_generation", "sql_selection", "query_fixer"]
    assert r.status is PipelineStatus.OK
    assert r.unvalidated_sql == "SELECT 1"


def test_status_no_sql_when_no_candidate() -> None:
    r = _orch(_registry(gen_candidate=False)).run(_input())
    # 选择阶段前置失败（无候选）→ 终止 → 有 error trace → ERROR
    assert r.status is PipelineStatus.ERROR


def test_status_fixer_exhausted() -> None:
    r = _orch(_registry(fixer_sets_final=False)).run(_input())
    assert r.status is PipelineStatus.FIXER_EXHAUSTED
    assert r.unvalidated_sql is None


def test_stage_exception_terminates_with_error() -> None:
    r = _orch(_registry(raise_in="context_acquisition")).run(_input())
    assert r.status is PipelineStatus.ERROR
    assert any(not t.ok for t in r.traces)


def test_unknown_strategy_raises_resolution_error() -> None:
    reg = _registry()
    bad = PipelineConfig(context_acquisition=StageConfig(strategy="does_not_exist"))
    with pytest.raises(StrategyResolutionError):
        _orch(reg, bad).run(_input())


def test_traces_recorded_for_each_stage() -> None:
    r = _orch(_registry()).run(_input())
    assert len(r.traces) == 4
    assert all(t.ok for t in r.traces)
    assert set(r.stage_strategies.keys()) == {
        "context_acquisition", "sql_generation", "sql_selection", "query_fixer"
    }
