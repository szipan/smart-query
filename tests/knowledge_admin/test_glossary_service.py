"""GlossaryService 单测：CRUD + 唯一性 + 强一致回滚 + 搜索。"""

from __future__ import annotations

import pytest

from knowledge_admin.errors import DuplicateKeyError, IndexSyncError, NotFoundError
from knowledge_admin.models import GlossaryTerm
from knowledge_admin.service import GlossaryService
from knowledge_admin.sync import GLOSSARY_NAMESPACE, GlossaryIndexSync

from fakes import FakeEmbedder, FakeObserver, FakeTermStore, FakeVectorStore


def _svc(vs=None, obs=None):
    vs = vs or FakeVectorStore()
    store = FakeTermStore()
    sync = GlossaryIndexSync(FakeEmbedder(), vs)
    return GlossaryService(store, sync, obs), store, vs


def test_create_writes_store_and_vector() -> None:
    svc, store, vs = _svc()
    t = svc.create(GlossaryTerm(term="发电量", synonyms=["发电"], definition="p83022"))
    assert t.term_id and store.get(t.term_id) is not None
    assert (GLOSSARY_NAMESPACE, t.term_id) in vs.docs
    assert t.created_at and t.updated_at


def test_create_duplicate_rejected() -> None:
    svc, _, _ = _svc()
    svc.create(GlossaryTerm(term="发电量"))
    with pytest.raises(DuplicateKeyError):
        svc.create(GlossaryTerm(term=" 发电量 "))


def test_create_disabled_skips_vector() -> None:
    svc, _, vs = _svc()
    t = svc.create(GlossaryTerm(term="发电量", enabled=False))
    assert (GLOSSARY_NAMESPACE, t.term_id) not in vs.docs


def test_create_sync_failure_rolls_back_store() -> None:
    # 向量 upsert 失败 -> 权威存储不应残留
    svc, store, _ = _svc(vs=FakeVectorStore(fail_upsert=True))
    with pytest.raises(IndexSyncError):
        svc.create(GlossaryTerm(term="发电量"))
    assert store.scan() == []   # 回滚，无残留


def test_update_changes_store_and_vector() -> None:
    svc, store, vs = _svc()
    t = svc.create(GlossaryTerm(term="发电量", definition="old"))
    t.definition = "new"
    svc.update(t)
    assert store.get(t.term_id).definition == "new"
    assert vs.docs[(GLOSSARY_NAMESPACE, t.term_id)]["definition"] == "new"


def test_update_sync_failure_restores_old() -> None:
    svc, store, vs = _svc()
    t = svc.create(GlossaryTerm(term="发电量", definition="old"))
    vs.fail_upsert = True
    t2 = GlossaryTerm(term="发电量", definition="new", term_id=t.term_id)
    with pytest.raises(IndexSyncError):
        svc.update(t2)
    assert store.get(t.term_id).definition == "old"   # 回滚到旧值


def test_update_missing_raises_not_found() -> None:
    svc, _, _ = _svc()
    with pytest.raises(NotFoundError):
        svc.update(GlossaryTerm(term="x", term_id="nope"))


def test_delete_removes_store_and_vector() -> None:
    svc, store, vs = _svc()
    t = svc.create(GlossaryTerm(term="发电量"))
    n = svc.delete([t.term_id, "nonexistent"])
    assert n == 1
    assert store.get(t.term_id) is None
    assert (GLOSSARY_NAMESPACE, t.term_id) not in vs.docs


def test_set_enabled_toggles_vector() -> None:
    svc, _, vs = _svc()
    t = svc.create(GlossaryTerm(term="发电量"))
    svc.set_enabled(t.term_id, False)
    assert (GLOSSARY_NAMESPACE, t.term_id) not in vs.docs
    svc.set_enabled(t.term_id, True)
    assert (GLOSSARY_NAMESPACE, t.term_id) in vs.docs


def test_search_by_keyword_and_pagination() -> None:
    svc, _, _ = _svc()
    svc.create(GlossaryTerm(term="发电量", synonyms=["发电"]))
    svc.create(GlossaryTerm(term="装机容量"))
    svc.create(GlossaryTerm(term="可用率"))
    r = svc.search(word="发电", current_page=1, page_size=10)
    assert r["total_count"] == 1 and r["data"][0].term == "发电量"
    r_all = svc.search(current_page=1, page_size=2)
    assert r_all["total_count"] == 3 and r_all["total_pages"] == 2 and len(r_all["data"]) == 2


def test_observer_records_writes() -> None:
    obs = FakeObserver()
    svc, _, _ = _svc(obs=obs)
    t = svc.create(GlossaryTerm(term="发电量"))
    svc.delete([t.term_id])
    ops = [r["operation"] for r in obs.records]
    assert "glossary.create" in ops and "glossary.delete" in ops
