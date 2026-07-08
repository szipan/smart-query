"""SQL 选择阶段（design.md §3.3 / §7.7）。

默认策略 passthrough：取候选生成顺序的第一个。候选为空 → StagePreconditionError。
"""

from __future__ import annotations

from typing import Any

from text2sql_pipeline.errors import StagePreconditionError
from text2sql_pipeline.models import PipelineContext
from text2sql_pipeline.registry import register


class PassthroughSelection:
    stage_type = "sql_selection"
    strategy_name = "passthrough"

    def __init__(self, deps, params: dict[str, Any]) -> None:
        pass

    def run(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.sql_candidates:
            raise StagePreconditionError(self.stage_type, "sql_candidates")
        ctx.selected_sql = ctx.sql_candidates[0]
        return ctx


@register("sql_selection", "passthrough")
def _make_passthrough(deps, params):
    return PassthroughSelection(deps, params)
