"""语义缓存（design.md §3/§6；Requirements 5/6/7）。

- lookup：向量化 standalone_question，在 semantic_cache 命名空间 top_k=1 检索；相似度 ≥ 阈值判命中。
- store：**只存隔离前 SQL 模板，绝不存 safe_sql**。
  本期条件放宽为"未命中 ∧ Layer 5 通过"（完整设计还要求执行成功；本期不执行，见 tasks 说明）。
- invalidate：按 entry_id 失效。
"""

from __future__ import annotations

from typing import Optional

from query_engine.config import CACHE_NAMESPACE, CACHE_SIMILARITY_THRESHOLD
from query_engine.deps import Embedder, VectorStore
from query_engine.models import CacheLookup


class SemanticCache:
    def __init__(self, embedder: Optional[Embedder], vector_store: Optional[VectorStore],
                 threshold: float = CACHE_SIMILARITY_THRESHOLD) -> None:
        self._embedder = embedder
        self._vs = vector_store
        self._threshold = threshold

    def lookup(self, question: str) -> CacheLookup:
        if self._embedder is None or self._vs is None:
            return CacheLookup(hit=False)
        vec = self._embedder.embed([question])[0]
        hits = self._vs.similarity_search(vec, 1, CACHE_NAMESPACE)
        if not hits:
            return CacheLookup(hit=False)
        top = hits[0]
        sim = float(top.get("similarity", top.get("score", 0.0)))
        if sim >= self._threshold:
            return CacheLookup(
                hit=True,
                pre_isolation_sql=top.get("pre_isolation_sql"),
                similarity=sim,
                entry_id=top.get("entry_id"),
            )
        return CacheLookup(hit=False, similarity=sim)

    def store(self, question: str, pre_isolation_sql: str) -> None:
        """写入隔离前 SQL 模板（绝不写 safe_sql）。"""
        if self._embedder is None or self._vs is None:
            return
        vec = self._embedder.embed([question])[0]
        self._vs.upsert(CACHE_NAMESPACE, {
            "vector": vec,
            "question": question,
            "pre_isolation_sql": pre_isolation_sql,
        })

    def invalidate(self, entry_id: str) -> None:
        if self._vs is not None:
            self._vs.delete(CACHE_NAMESPACE, entry_id)
