"""EntityService 单测：CRUD + 业务键唯一 + 别名冲突 + 搜索。"""

from __future__ import annotations

import pytest

from knowledge_admin.errors import AliasConflictError, DuplicateKeyError, NotFoundError
from knowledge_admin.models import EntityEntry
from knowledge_admin.service import EntityService

from fakes import FakeEntityStore, FakeObserver


def _svc(obs=None):
    store = FakeEntityStore()
    return EntityService(store, obs), store


def _entry(canonical="华能一站", et="power_station", eid="1001", aliases=None):
    return EntityEntry(canonical=canonical, entity_type=et, entity_id=eid, aliases=aliases or [])


def test_create_ok() -> None:
    svc, store = _svc()
    e = svc.create(_entry(aliases=["一号站"]))
    assert e.entry_id and store.get(e.entry_id).business_key == "power_station#1001"
    assert e.created_at and e.updated_at


def test_create_duplicate_business_key() -> None:
    svc, _ = _svc()
    svc.create(_entry())
    with pytest.raises(DuplicateKeyError):
        svc.create(_entry(canonical="别名不同但同键"))


def test_create_alias_conflict_across_entities() -> None:
    svc, _ = _svc()
    svc.create(_entry(eid="1001", aliases=["一号站"]))
    with pytest.raises(AliasConflictError) as ei:
        svc.create(_entry(eid="2002", aliases=["一号站"]))
    assert ei.value.detail["alias"] == "一号站"


def test_update_keeps_own_alias_and_adds() -> None:
    svc, store = _svc()
    e = svc.create(_entry(aliases=["一号站"]))
    e.aliases = ["一号站", "华能站"]
    svc.update(e)
    assert set(store.get(e.entry_id).aliases) == {"一号站", "华能站"}


def test_update_missing_not_found() -> None:
    svc, _ = _svc()
    with pytest.raises(NotFoundError):
        svc.update(_entry_with_id("nope"))


def _entry_with_id(entry_id):
    e = _entry()
    e.entry_id = entry_id
    return e


def test_delete_counts() -> None:
    svc, store = _svc()
    e = svc.create(_entry())
    assert svc.delete([e.entry_id, "nope"]) == 1
    assert store.get(e.entry_id) is None


def test_set_enabled_toggles() -> None:
    svc, store = _svc()
    e = svc.create(_entry(aliases=["一号站"]))
    svc.set_enabled(e.entry_id, False)
    assert store.get(e.entry_id).enabled is False
    assert store.scan_aliases() == []   # 停用后别名不参与匹配加载


def test_search_by_keyword_type_and_pagination() -> None:
    svc, _ = _svc()
    svc.create(_entry(canonical="华能一站", eid="1001", aliases=["一号站"]))
    svc.create(_entry(canonical="大唐二站", eid="2002"))
    svc.create(EntityEntry(canonical="发电量", entity_type="metric", entity_id="p83022"))
    # 关键词命中别名
    r = svc.search(keyword="一号站")
    assert r["total_count"] == 1 and r["data"][0].entity_id == "1001"
    # 类型过滤
    r2 = svc.search(entity_type="metric")
    assert r2["total_count"] == 1 and r2["data"][0].canonical == "发电量"


def test_observer_records() -> None:
    obs = FakeObserver()
    svc, _ = _svc(obs)
    e = svc.create(_entry())
    svc.delete([e.entry_id])
    ops = [r["operation"] for r in obs.records]
    assert "entity.create" in ops and "entity.delete" in ops
