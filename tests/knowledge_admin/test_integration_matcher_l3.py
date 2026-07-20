"""集成：Aho-Corasick 匹配器 ↔ L3 HardEntityResolver 契约，以及经 wiring 接入 L3。"""

from __future__ import annotations

from adapters.entity_dictionary import AhoCorasickEntityDictionary
from knowledge_admin.models import EntityEntry
from knowledge_admin.service import EntityService
from query_engine.nodes.term_normalizer import HardEntityResolver

from fakes import FakeEntityStore


def _dict_from_entities(entries: list[EntityEntry]) -> AhoCorasickEntityDictionary:
    store = FakeEntityStore()
    svc = EntityService(store)
    for e in entries:
        svc.create(e)
    # 匹配器直接以 store.scan_aliases 为 loader（与生产同源）
    return AhoCorasickEntityDictionary(store.scan_aliases)


def test_hard_resolver_uses_matcher_output() -> None:
    d = _dict_from_entities([
        EntityEntry(canonical="华能一站电站", entity_type="power_station", entity_id="1001",
                    aliases=["华能一站", "一号站"]),
        EntityEntry(canonical="满发小时", entity_type="metric", entity_id="p83025",
                    aliases=["等效利用小时"]),
    ])
    resolver = HardEntityResolver(d)
    entities = resolver.resolve("华能一站上个月等效利用小时是多少")

    by_id = {e.entity_id: e for e in entities}
    assert "1001" in by_id and "p83025" in by_id
    assert by_id["1001"].entity_type == "power_station"
    assert by_id["1001"].canonical == "华能一站电站"
    # 确定性：同输入同输出
    assert resolver.resolve("华能一站等效利用小时") == resolver.resolve("华能一站等效利用小时")


def test_disabled_entity_not_matched() -> None:
    store = FakeEntityStore()
    svc = EntityService(store)
    e = svc.create(EntityEntry(canonical="华能一站", entity_type="power_station",
                               entity_id="1001", aliases=["一号站"]))
    svc.set_enabled(e.entry_id, False)
    d = AhoCorasickEntityDictionary(store.scan_aliases)
    assert HardEntityResolver(d).resolve("一号站发电量") == []


def test_no_entities_resolver_returns_empty() -> None:
    d = AhoCorasickEntityDictionary(FakeEntityStore().scan_aliases)
    assert HardEntityResolver(d).resolve("任意问题") == []
