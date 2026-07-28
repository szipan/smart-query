"""槽位门禁属性测试（Property 14/15/16）。"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from query_engine.config import CACHE_NAMESPACE
from query_engine.models import SemanticSlots, slots_equal
from query_engine.nodes.semantic_cache import SemanticCache

from qe_fakes import FakeEmbedder, FakeVectorStore


@st.composite
def _slots(draw) -> SemanticSlots:
    return SemanticSlots(
        comparison=draw(st.sampled_from([None, "GT", "GE", "LT", "LE", "EQ"])),
        threshold_watts=draw(st.sampled_from([None, 1_000_000, 1_000_000_000])),
        metric=draw(st.sampled_from([None, "装机容量", "发电量"])),
        group_by=draw(st.sampled_from([None, "电站", "机型", "单台"])),
        agg_func=draw(st.sampled_from([None, "SUM", "AVG"])),
        sort_direction=draw(st.sampled_from([None, "DESC", "ASC"])),
        sort_field=draw(st.sampled_from([None, "装机容量", "电站名称"])),
        output_entity=draw(st.sampled_from([None, "电站", "机型"])),
        product_line=draw(st.sampled_from([(), ("户用",), ("工商业",), ("户用", "工商业")])),
        top_n=draw(st.sampled_from([None, 10])),
        extra_filters=draw(st.sampled_from([(), ("build_year=2025",)])),
    )


def _cache_with(entry_slots: SemanticSlots, sim: float = 0.99) -> SemanticCache:
    vs = FakeVectorStore(data={CACHE_NAMESPACE: [
        {"similarity": sim, "pre_isolation_sql": "SELECT 1", "slots_json": entry_slots.to_json()}]})
    return SemanticCache(FakeEmbedder(), vs)


# Property 14/16：命中 当且仅当 关键槽位全等（且相似度达阈值）
@settings(max_examples=120)
@given(a=_slots(), b=_slots())
def test_hit_iff_slots_equal_when_similar(a: SemanticSlots, b: SemanticSlots) -> None:
    res = _cache_with(b, sim=0.99).lookup("q", a)
    assert res.hit == slots_equal(a, b)


# Property 15：请求侧 slots 为空 → 恒未命中（即便相似度极高、条目槽位齐全）
@settings(max_examples=60)
@given(b=_slots())
def test_none_request_slots_always_miss(b: SemanticSlots) -> None:
    assert _cache_with(b, sim=0.999).lookup("q", None).hit is False


# Property 14：相似度低于阈值 → 未命中（即便槽位全等）
@settings(max_examples=60)
@given(a=_slots())
def test_below_threshold_miss_even_if_slots_equal(a: SemanticSlots) -> None:
    assert _cache_with(a, sim=0.5).lookup("q", a).hit is False
