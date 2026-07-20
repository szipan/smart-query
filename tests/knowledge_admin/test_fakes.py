"""内存 fake 存储的契约/唯一性单测（间接验证 Protocol 语义）。"""

from __future__ import annotations

import pytest

from knowledge_admin.errors import AliasConflictError, DuplicateKeyError, NotFoundError
from knowledge_admin.models import EntityEntry, GlossaryTerm

from fakes import FakeEntityStore, FakeTermStore


def test_term_store_create_get_scan_delete() -> None:
    s = FakeTermStore()
    t = s.create(GlossaryTerm(term="发电量").validate())
    assert t.term_id and s.get(t.term_id).term == "发电量"
    assert len(s.scan()) == 1
    s.delete(t.term_id)
    assert s.get(t.term_id) is None


def test_term_store_duplicate_rejected() -> None:
    s = FakeTermStore()
    s.create(GlossaryTerm(term="发电量").validate())
    with pytest.raises(DuplicateKeyError):
        s.create(GlossaryTerm(term=" 发电量 ").validate())  # 归一化后同键


def test_term_update_not_found() -> None:
    s = FakeTermStore()
    with pytest.raises(NotFoundError):
        s.update(GlossaryTerm(term="x", term_id="nope").validate())


def test_entity_business_key_unique() -> None:
    s = FakeEntityStore()
    s.create(EntityEntry(canonical="华能一站", entity_type="power_station", entity_id="1001").validate())
    with pytest.raises(DuplicateKeyError):
        s.create(EntityEntry(canonical="另一个", entity_type="power_station", entity_id="1001").validate())


def test_entity_alias_global_unique_across_entities() -> None:
    s = FakeEntityStore()
    s.create(EntityEntry(canonical="华能一站", entity_type="power_station", entity_id="1001",
                         aliases=["一号站"]).validate())
    with pytest.raises(AliasConflictError):
        s.create(EntityEntry(canonical="大唐二站", entity_type="power_station", entity_id="2002",
                             aliases=["一号站"]).validate())


def test_entity_update_can_keep_own_alias() -> None:
    s = FakeEntityStore()
    e = s.create(EntityEntry(canonical="华能一站", entity_type="power_station", entity_id="1001",
                             aliases=["一号站"]).validate())
    e.aliases = ["一号站", "华能站"]
    s.update(e)  # 自己的别名不算冲突
    assert set(a["alias_original"] for a in s.scan_aliases()) == {"一号站", "华能站"}


def test_scan_aliases_skips_disabled() -> None:
    s = FakeEntityStore()
    e = s.create(EntityEntry(canonical="华能一站", entity_type="power_station", entity_id="1001",
                             aliases=["一号站"], enabled=False).validate())
    assert s.scan_aliases() == []
