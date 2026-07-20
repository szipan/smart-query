"""软通道向量库同步（design.md §6）。

把 GlossaryTerm 经 Embedder 向量化后 upsert 到 VectorStore 的 glossary 命名空间；
删除/停用时从该命名空间删除。索引条目 entry_id 承载 term_id（可回溯，Req 4.3）。
向量化/写入失败向上抛出，由 GlossaryService 负责整体回滚（强一致，Req 4.4）。
"""

from __future__ import annotations

from knowledge_admin.deps import Embedder, VectorStore
from knowledge_admin.errors import IndexSyncError
from knowledge_admin.models import GlossaryTerm

GLOSSARY_NAMESPACE = "glossary"


class GlossaryIndexSync:
    def __init__(self, embedder: Embedder, vector_store: VectorStore,
                 namespace: str = GLOSSARY_NAMESPACE) -> None:
        self._embedder = embedder
        self._vs = vector_store
        self._ns = namespace

    def sync_upsert(self, term: GlossaryTerm) -> None:
        if not term.term_id:
            raise IndexSyncError("sync_upsert 需要已带 term_id 的术语")
        try:
            vector = self._embedder.embed([term.index_text()])[0]
            self._vs.upsert(self._ns, {
                "entry_id": term.term_id,      # 回溯到权威条目
                "term": term.term,
                "synonyms": list(term.synonyms),
                "definition": term.definition,
                "vector": vector,
            })
        except IndexSyncError:
            raise
        except Exception as e:  # noqa: BLE001 —— 统一包装为 IndexSyncError 供上层回滚
            raise IndexSyncError(f"向量库同步失败: {e!r}") from e

    def sync_delete(self, term_id: str) -> None:
        try:
            self._vs.delete(self._ns, term_id)
        except Exception as e:  # noqa: BLE001
            raise IndexSyncError(f"向量库删除失败: {e!r}") from e
