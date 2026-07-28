"""语义缓存（design.md §3/§5.4/§6；Requirements 5/6/7/17）。

命中判定 = **向量召回 + 槽位门禁（Slot_Gate）**：
- lookup：向量化 canonical_question，在 semantic_cache 命名空间取 top_k 候选；对相似度 ≥ 阈值者
  逐个比对 SemanticSlots，返回**首个关键槽位全等**的候选为命中。任一侧 slots 为空 → 未命中（fail-safe）。
- store：**只存隔离前 SQL 模板，绝不存 safe_sql**，并把 SemanticSlots 序列化为 slots_json 一并写入。
- invalidate：按 entry_id 失效。

两条红线不变：命中仍过 Layer 5；缓存只存隔离前 SQL。
"""

from __future__ import annotations

from typing import Optional

from query_engine.config import CACHE_NAMESPACE, CACHE_SIMILARITY_THRESHOLD, CACHE_TOP_K
from query_engine.deps import Embedder, VectorStore
from query_engine.models import CacheLookup, SemanticSlots, slots_equal


class SemanticCache:
    def __init__(self, embedder: Optional[Embedder], vector_store: Optional[VectorStore],
                 threshold: float = CACHE_SIMILARITY_THRESHOLD, top_k: int = CACHE_TOP_K) -> None:
        self._embedder = embedder
        self._vs = vector_store
        self._threshold = threshold
        self._top_k = top_k

    def lookup(self, question: str, slots: Optional[SemanticSlots]) -> CacheLookup:
        # 无嵌入/向量库 → 不可查；无 slots → fail-safe 未命中（Property 15）
        if self._embedder is None or self._vs is None or slots is None:
            return CacheLookup(hit=False)
        vec = self._embedder.embed([question])[0]
        hits = self._vs.similarity_search(vec, self._top_k, CACHE_NAMESPACE)
        best_sim: Optional[float] = None
        for top in hits:
            sim = float(top.get("similarity", top.get("score", 0.0)))
            if best_sim is None:
                best_sim = sim
            if sim < self._threshold:
                continue
            cand = SemanticSlots.from_json(top.get("slots_json"))
            if cand is not None and slots_equal(slots, cand):   # 关键槽位全等（Property 14/16）
                return CacheLookup(
                    hit=True,
                    pre_isolation_sql=top.get("pre_isolation_sql"),
                    similarity=sim,
                    entry_id=top.get("entry_id"),
                )
        # 未命中：附带最相似候选的相似度，便于观测
        return CacheLookup(hit=False, similarity=best_sim)

    def store(self, question: str, pre_isolation_sql: str,
              slots: Optional[SemanticSlots]) -> None:
        """写入隔离前 SQL 模板（绝不写 safe_sql）+ 槽位指纹。slots 为空则不入缓存（无法门禁）。"""
        if self._embedder is None or self._vs is None or slots is None:
            return
        vec = self._embedder.embed([question])[0]
        self._vs.upsert(CACHE_NAMESPACE, {
            "vector": vec,
            "question": question,
            "pre_isolation_sql": pre_isolation_sql,
            "slots_json": slots.to_json(),
        })

    def invalidate(self, entry_id: str) -> None:
        if self._vs is not None:
            self._vs.delete(CACHE_NAMESPACE, entry_id)
