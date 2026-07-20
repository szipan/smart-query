"""GlossaryIndexSync 单测（mock Embedder/VectorStore）。"""

from __future__ import annotations

import pytest

from knowledge_admin.errors import IndexSyncError
from knowledge_admin.models import GlossaryTerm
from knowledge_admin.sync import GLOSSARY_NAMESPACE, GlossaryIndexSync

from fakes import FakeEmbedder, FakeVectorStore


def _term() -> GlossaryTerm:
    t = GlossaryTerm(term="发电量", synonyms=["发电"], definition="p83022").validate()
    t.term_id = "t1"
    return t


def test_sync_upsert_writes_vector_with_term_id() -> None:
    emb, vs = FakeEmbedder(), FakeVectorStore()
    GlossaryIndexSync(emb, vs).sync_upsert(_term())
    doc = vs.docs[(GLOSSARY_NAMESPACE, "t1")]
    assert doc["term"] == "发电量" and doc["entry_id"] == "t1"
    assert doc["synonyms"] == ["发电"] and doc["vector"]
    # 被向量化的文本含 term + 同义词 + 定义
    assert "发电量" in emb.calls[0][0] and "p83022" in emb.calls[0][0]


def test_sync_upsert_requires_term_id() -> None:
    t = GlossaryTerm(term="x").validate()  # 无 term_id
    with pytest.raises(IndexSyncError):
        GlossaryIndexSync(FakeEmbedder(), FakeVectorStore()).sync_upsert(t)


def test_sync_upsert_wraps_backend_failure() -> None:
    with pytest.raises(IndexSyncError):
        GlossaryIndexSync(FakeEmbedder(), FakeVectorStore(fail_upsert=True)).sync_upsert(_term())


def test_sync_delete_removes_doc() -> None:
    emb, vs = FakeEmbedder(), FakeVectorStore()
    sync = GlossaryIndexSync(emb, vs)
    sync.sync_upsert(_term())
    sync.sync_delete("t1")
    assert (GLOSSARY_NAMESPACE, "t1") not in vs.docs


def test_sync_delete_wraps_backend_failure() -> None:
    with pytest.raises(IndexSyncError):
        GlossaryIndexSync(FakeEmbedder(), FakeVectorStore(fail_delete=True)).sync_delete("t1")
