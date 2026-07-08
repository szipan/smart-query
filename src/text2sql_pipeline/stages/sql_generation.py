"""SQL 生成阶段（design.md §3.2 / §7.6）。

默认策略 single_llm：组装 prompt，单次调用 LLM 生成一个候选 SQL。
参考 SQLBot `template/generate_sql`、`chat/task/llm.py` 的 prompt 组织思路，clean-room 重写。

层边界：system 明确禁止输出任何权限过滤条件（隔离由 Layer 5 负责，Requirements 4.6/7.1/7.2）。
"""

from __future__ import annotations

import re
from typing import Any

from text2sql_pipeline.config import DEFAULT_MODEL_ID
from text2sql_pipeline.errors import StagePreconditionError
from text2sql_pipeline.models import PipelineContext, SqlCandidate
from text2sql_pipeline.registry import register

_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)


def extract_sql(raw: str) -> str:
    """从 LLM 原始输出提取 SQL：优先取 markdown 代码块，否则取全文去空白。"""
    if not raw:
        return ""
    m = _FENCE_RE.search(raw)
    text = m.group(1) if m else raw
    return text.strip().rstrip(";").strip()


class SingleLlmGeneration:
    stage_type = "sql_generation"
    strategy_name = "single_llm"

    def __init__(self, deps, params: dict[str, Any]) -> None:
        self._llm = deps.llm_client
        self._dialect = getattr(deps.datasource, "dialect", "starrocks") if deps.datasource else "starrocks"
        self._default_model = params.get("default_model_id", DEFAULT_MODEL_ID)

    def _select_model(self, ctx: PipelineContext) -> str:
        # 请求显式指定 > 策略默认
        return ctx.input.model_id or self._default_model

    def _build_prompt(self, ctx: PipelineContext) -> tuple[str, str]:
        rc = ctx.retrieved_context
        if rc is None:
            raise StagePreconditionError(self.stage_type, "retrieved_context")
        system = (
            "你是资深数据分析工程师。只输出一条 " + self._dialect + " 方言的 SELECT 查询，"
            "不要输出解释。严禁添加任何权限过滤条件（如 org_id/ps_id 过滤），隔离由下游负责。"
        )
        parts = [f"方言: {self._dialect}", "相关 Schema:", *rc.schema_snippets,
                 "术语定义:", *rc.term_glossary]
        if rc.few_shot_examples:
            parts.append("参考示例:")
            parts += [f"Q: {ex.get('question','')}\nSQL: {ex.get('sql','')}" for ex in rc.few_shot_examples]
        if rc.semantic_fragments:
            parts.append(f"语义层片段: {rc.semantic_fragments}")
        parts.append(f"问题: {ctx.input.standalone_question}")
        return system, "\n".join(parts)

    def run(self, ctx: PipelineContext) -> PipelineContext:
        system, prompt = self._build_prompt(ctx)
        model_id = self._select_model(ctx)
        # 模型调用失败/超时 → 异常上抛，不追加部分候选（Requirements 4.7/4.8）
        raw = self._llm.complete(model_id=model_id, system=system, prompt=prompt)
        sql = extract_sql(raw)
        ctx.sql_candidates.append(
            SqlCandidate(sql=sql, generator_name=self.strategy_name, model_id=model_id)
        )
        return ctx


@register("sql_generation", "single_llm")
def _make_single_llm(deps, params):
    return SingleLlmGeneration(deps, params)
