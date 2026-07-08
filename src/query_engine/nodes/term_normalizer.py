"""术语归一化节点（design.md §3；Requirements 3/4）。

- 硬通道 HardEntityResolver：经确定性字典把关键实体映射到标准 id，不调 LLM、无随机。
- 软通道 SoftGlossaryResolver：经 Embedder + 术语库向量检索取同义词/定义。
  软通道参考 SQLBot terminology 的召回思路，clean-room 重写（Req 15.1）。
"""

from __future__ import annotations

from typing import Optional

from query_engine.config import GLOSSARY_NAMESPACE
from query_engine.deps import Embedder, EntityDictionary, VectorStore
from query_engine.models import QueryContext, ResolvedEntity, ResolvedTerm


class HardEntityResolver:
    def __init__(self, dictionary: Optional[EntityDictionary]) -> None:
        self._dict = dictionary

    def resolve(self, question: str) -> list[ResolvedEntity]:
        if self._dict is None:
            return []
        hits = self._dict.match(question)  # 确定性；无命中返回空
        return [
            ResolvedEntity(
                surface=h.get("surface", ""),
                canonical=h.get("canonical", ""),
                entity_type=h.get("entity_type", ""),
                entity_id=h.get("entity_id"),
            )
            for h in hits
        ]


class SoftGlossaryResolver:
    def __init__(self, embedder: Optional[Embedder], vector_store: Optional[VectorStore],
                 top_k: int = 5) -> None:
        self._embedder = embedder
        self._vs = vector_store
        self._top_k = top_k

    def resolve(self, question: str) -> list[ResolvedTerm]:
        if self._embedder is None or self._vs is None:
            return []
        try:
            vec = self._embedder.embed([question])[0]
            hits = self._vs.similarity_search(vec, self._top_k, GLOSSARY_NAMESPACE)
        except Exception:  # 检索失败 → 不产出，不中断（Req 4.3）
            return []
        return [
            ResolvedTerm(
                term=h.get("term", ""),
                definition=h.get("definition", ""),
                synonyms=tuple(h.get("synonyms", ())),
            )
            for h in hits
        ]


class TermNormalizer:
    """编排硬 + 软两个子解析器，写入 resolved_entities / resolved_terms。"""

    def __init__(self, hard: HardEntityResolver, soft: SoftGlossaryResolver) -> None:
        self._hard = hard
        self._soft = soft

    def run(self, ctx: QueryContext) -> QueryContext:
        q = ctx.standalone_question or ctx.request.raw_question
        ctx.resolved_entities = self._hard.resolve(q)
        ctx.resolved_terms = self._soft.resolve(q)
        return ctx
