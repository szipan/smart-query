"""流水线编排器（design.md §7.3，Requirements 1/10/11）。

按固定顺序 context_acquisition → sql_generation → sql_selection → query_fixer 驱动；
具体实现由 PipelineConfig + StrategyRegistry 解析。前置字段缺失/异常即终止并记录 trace。
"""

from __future__ import annotations

import time

from text2sql_pipeline.deps import PipelineStage, StageDependencies
from text2sql_pipeline.errors import StagePreconditionError, StrategyResolutionError, UnknownStrategyError
from text2sql_pipeline.models import (
    PipelineConfig,
    PipelineContext,
    PipelineInput,
    PipelineResult,
    PipelineStatus,
    StageTrace,
)
from text2sql_pipeline.observer import NullObserver, Observer
from text2sql_pipeline.registry import REGISTRY, StrategyRegistry

STAGE_ORDER = ["context_acquisition", "sql_generation", "sql_selection", "query_fixer"]


class PipelineOrchestrator:
    """按固定阶段顺序驱动，具体实现由配置 + 注册表解析。"""

    def __init__(self, registry: StrategyRegistry, deps: StageDependencies,
                 config: PipelineConfig, observer: Observer | None = None) -> None:
        self._registry = registry
        self._deps = deps
        self._config = config
        self._obs = observer or NullObserver()

    def _resolve_stages(self) -> list[PipelineStage]:
        cfg_map = {
            "context_acquisition": self._config.context_acquisition,
            "sql_generation": self._config.sql_generation,
            "sql_selection": self._config.sql_selection,
            "query_fixer": self._config.query_fixer,
        }
        stages: list[PipelineStage] = []
        for stage_type in STAGE_ORDER:
            sc = cfg_map[stage_type]
            try:
                stages.append(self._registry.create(stage_type, sc.strategy, self._deps, sc.params))
            except UnknownStrategyError:
                # 启动期解析失败：在执行任何阶段前终止
                raise StrategyResolutionError(stage_type, sc.strategy)
        return stages

    def run(self, pipeline_input: PipelineInput) -> PipelineResult:
        ctx = PipelineContext(input=pipeline_input)
        stages = self._resolve_stages()  # 任一阶段未注册 → StrategyResolutionError（执行前终止）
        stage_strategies: dict[str, str] = {}

        for stage in stages:
            started = time.monotonic()
            self._obs.on_stage_start(stage.stage_type, stage.strategy_name, ctx)
            ok, err = True, None
            try:
                ctx = stage.run(ctx)
            except StagePreconditionError as e:
                ok, err = False, str(e)
                ctx.warnings.append(str(e))
                self._trace(ctx, stage, started, ok, err)
                break
            except Exception as e:  # 阶段内部异常：记录并终止
                ok, err = False, repr(e)
                self._obs.on_stage_error(stage.stage_type, e, ctx)
                self._trace(ctx, stage, started, ok, err)
                break
            self._trace(ctx, stage, started, ok, err)
            stage_strategies[stage.stage_type] = stage.strategy_name
            self._obs.on_stage_end(stage.stage_type, ctx)

        return self._assemble_result(ctx, stage_strategies)

    def _trace(self, ctx: PipelineContext, stage: PipelineStage,
               started: float, ok: bool, err) -> None:
        ctx.traces.append(StageTrace(
            stage_type=stage.stage_type, strategy_name=stage.strategy_name,
            started_at=started, ended_at=time.monotonic(), ok=ok, error=err))

    def _assemble_result(self, ctx: PipelineContext,
                         stage_strategies: dict[str, str]) -> PipelineResult:
        had_error = any(not t.ok for t in ctx.traces)
        final = ctx.final_sql or ctx.selected_sql
        max_attempts = self._config.query_fixer.params.get("max_attempts", 2)

        def _nonempty(c) -> bool:
            return c is not None and c.sql is not None and c.sql.strip() != ""

        if had_error:
            status = PipelineStatus.ERROR
            sql = None
        elif not any(_nonempty(c) for c in ctx.sql_candidates):
            status = PipelineStatus.NO_SQL
            sql = None
        elif ctx.final_sql is None and ctx.fix_attempts >= max_attempts:
            status = PipelineStatus.FIXER_EXHAUSTED
            sql = None
        elif _nonempty(final):
            status = PipelineStatus.OK
            sql = final.sql
        else:
            status = PipelineStatus.NO_SQL
            sql = None

        return PipelineResult(
            request_id=ctx.input.request_id, status=status,
            unvalidated_sql=sql, datasource_id=ctx.input.datasource_id,
            candidates_considered=len(ctx.sql_candidates),
            fix_attempts=ctx.fix_attempts, stage_strategies=stage_strategies,
            traces=ctx.traces, warnings=ctx.warnings,
            error=next((t.error for t in ctx.traces if not t.ok), None))


def build_orchestrator(config: PipelineConfig, deps: StageDependencies,
                       observer: Observer | None = None) -> PipelineOrchestrator:
    """组装入口：导入触发默认策略注册，返回编排器（Task 8）。"""
    # 导入以触发 @register 装饰器把默认策略登记到 REGISTRY
    import text2sql_pipeline.stages  # noqa: F401

    return PipelineOrchestrator(REGISTRY, deps, config, observer)
