"""上下文获取阶段（design.md §3.1 / §7.4）。

默认策略 rag：问题向量化 → VectorStore 检索 schema 片段 + few-shot 示例。
参考 SQLBot `datasource/embedding`、`data_training` 的召回思路，clean-room 重写，不复制源码。
"""

from __future__ import annotations

from typing import Any

from text2sql_pipeline.errors import ConfigurationError
from text2sql_pipeline.models import PipelineContext, RetrievedContext
from text2sql_pipeline.registry import register


class RagContextAcquisition:
    stage_type = "context_acquisition"
    strategy_name = "rag"

    def __init__(self, deps, params: dict[str, Any]) -> None:
        if deps.embedder is None or deps.vector_store is None:
            raise ConfigurationError("rag 策略需要注入 embedder 与 vector_store")
        self._embedder = deps.embedder
        self._vs = deps.vector_store
        self._top_k = params.get("top_k", 8)

    def run(self, ctx: PipelineContext) -> PipelineContext:
        q = ctx.input.standalone_question
        # 把已解析实体纳入检索语境，提升 schema linking 命中
        enriched = q + " " + " ".join(e.canonical for e in ctx.input.resolved_entities)

        # 检索失败则不写入部分结果（retrieved_context 保持 None）—— 异常上抛由编排器记录
        vector = self._embedder.embed([enriched])[0]
        schema_hits = self._vs.similarity_search(vector, self._top_k, "schema")
        example_hits = self._vs.similarity_search(vector, self._top_k, "examples")

        rc = RetrievedContext(strategy_name=self.strategy_name)
        rc.schema_snippets = [h.get("text", "") for h in schema_hits]
        rc.few_shot_examples = [
            {"question": h.get("question", ""), "sql": h.get("sql", "")} for h in example_hits
        ]
        rc.term_glossary = [t.definition for t in ctx.input.resolved_terms]
        rc.debug = {"schema_hits": len(schema_hits), "example_hits": len(example_hits)}
        ctx.retrieved_context = rc
        return ctx


@register("context_acquisition", "rag")
def _make_rag(deps, params):
    return RagContextAcquisition(deps, params)
