"""Query Fixer 阶段（design.md §3.4 / §7.8）。

**本期为静态校验版（无 dry_run 执行探测）**：
- 未注入 validator → 跳过校验，selected_sql 直接作 final_sql（Req 6.4）。
- 注入了 validator（Layer 5）→ 调 validate_for_probe 做静态校验（隔离注入 + 只读校验，**不 dry_run**）：
    * 通过（无异常）→ final_sql = 候选。
    * 静态失败（抛异常）→ 把错误回灌 LLM 生成修正候选并重试，fix_attempts 递增，上限 max_attempts。
- dry_run 执行探测路径保留但本期禁用（待 L6/7 执行能力接入再启用）。

参考 SQLBot `db.py::check_sql_read` 的只读判定思路（仅思路，不抄源码）。
"""

from __future__ import annotations

from typing import Any

from text2sql_pipeline.errors import StagePreconditionError
from text2sql_pipeline.models import PipelineContext, SqlCandidate
from text2sql_pipeline.registry import register
from text2sql_pipeline.stages.sql_generation import extract_sql


class BoundedRetryFixer:
    stage_type = "query_fixer"
    strategy_name = "bounded_retry"

    def __init__(self, deps, params: dict[str, Any]) -> None:
        self._llm = deps.llm_client
        self._validator = deps.validator  # Layer 5 校验入口（可为 None → 跳过校验）
        self._max_attempts = int(params.get("max_attempts", 2))

    def run(self, ctx: PipelineContext) -> PipelineContext:
        if ctx.selected_sql is None:
            raise StagePreconditionError(self.stage_type, "selected_sql")
        candidate = ctx.selected_sql

        # 未注入 validator：本期不做任何探测，直接透传（Req 6.4）
        if self._validator is None:
            ctx.final_sql = candidate
            return ctx

        # 静态校验版：仅 validate_for_probe（无 dry_run）
        for attempt in range(self._max_attempts + 1):
            try:
                self._validator.validate_for_probe(candidate.sql, ctx.input.request_id)
                ctx.final_sql = candidate       # 静态校验通过
                ctx.fix_attempts = attempt
                return ctx
            except Exception as static_err:  # 隔离/只读静态失败
                ctx.fix_attempts = attempt
                if attempt >= self._max_attempts:
                    ctx.warnings.append(f"Query Fixer 用尽重试（静态校验）：{static_err}")
                    break
                candidate = self._repair(ctx, candidate, str(static_err))
        # 用尽仍失败：final_sql 保持 None → 编排器判 FIXER_EXHAUSTED
        return ctx

    def _repair(self, ctx: PipelineContext, cand: SqlCandidate, err: str) -> SqlCandidate:
        system = "你是 SQL 修复专家。根据报错修正 SQL，只输出修正后的 SELECT，不要权限过滤条件。"
        prompt = (f"原 SQL:\n{cand.sql}\n\n报错:\n{err}\n\n问题:\n{ctx.input.standalone_question}")
        fixed = extract_sql(self._llm.complete(model_id=cand.model_id, system=system, prompt=prompt))
        return SqlCandidate(sql=fixed, generator_name=cand.generator_name,
                            model_id=cand.model_id, metadata={"repaired_from": cand.sql})


@register("query_fixer", "bounded_retry")
def _make_fixer(deps, params):
    return BoundedRetryFixer(deps, params)
