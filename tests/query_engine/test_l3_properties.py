"""Task 14：L3 正确性属性测试（hypothesis）。覆盖 design.md §Correctness Properties。"""

import copy

from hypothesis import given, settings
from hypothesis import strategies as st

from query_engine.deps import EngineDependencies
from query_engine.models import AnswerStatus, CallerIdentity, QueryRequest, QuerySource, SemanticSlots
from query_engine.orchestrator import QueryOrchestrator

_CANON_JSON = ('{"canonical":"canon","slots":{"comparison":"GT","threshold":"1000000W",'
               '"metric":"装机容量","sort_direction":"DESC","output_entity":"电站","product_line":["户用"]}}')
# 归一后 group_by/agg_func 会按默认值填充（output_entity / SUM）
_SLOTS = SemanticSlots(comparison="GT", threshold_watts=1000000, metric="装机容量",
                       group_by="电站", agg_func="SUM", sort_direction="DESC",
                       output_entity="电站", product_line=("户用",))

from qe_fakes import (
    FakeEmbedder,
    FakeEntityDictionary,
    FakeGuard,
    FakeGuardFactory,
    FakeLayer4Client,
    FakeLlm,
    FakeMemory,
    FakeVectorStore,
)
from query_engine.config import CACHE_NAMESPACE


def _deps(cache_hit=False):
    vs = FakeVectorStore(
        data={CACHE_NAMESPACE: [{"similarity": 0.99, "pre_isolation_sql": "SELECT ps_id FROM t",
                                 "slots_json": _SLOTS.to_json()}]} if cache_hit else {}
    )
    return EngineDependencies(
        memory=FakeMemory(), llm=FakeLlm(out=_CANON_JSON), embedder=FakeEmbedder(), vector_store=vs,
        entity_dictionary=FakeEntityDictionary(), layer4=FakeLayer4Client(),
        guard_factory=FakeGuardFactory(), datasource=None,
    )


def _req(q):
    return QueryRequest(request_id="r", identity=CallerIdentity(user_id="1001"), raw_question=q)


# Property 8：request 不可变
@settings(max_examples=40)
@given(q=st.text(min_size=1, max_size=30))
def test_request_immutable(q: str) -> None:
    req = _req(q)
    before = copy.deepcopy(req)
    QueryOrchestrator(_deps()).run(req)
    assert req == before


# Property 6：固定编排顺序（前三节点固定）
@settings(max_examples=30)
@given(q=st.text(min_size=1, max_size=30))
def test_fixed_order(q: str) -> None:
    ans = QueryOrchestrator(_deps()).run(_req(q))
    nodes = [t.node for t in ans.request_traces]
    assert nodes[:4] == [
        "context_rewrite", "term_normalization", "canonical_rewrite", "semantic_cache_lookup",
    ]


# Property 1：缓存命中仍过 L5
# 用不含比较/阈值/排序/TopN 关键词的固定问题，使规则型抽取不改动槽位、与缓存条目一致
def test_cache_hit_still_guarded() -> None:
    ans = QueryOrchestrator(_deps(cache_hit=True)).run(_req("查询户用电站的装机容量"))
    nodes = [t.node for t in ans.request_traces]
    assert ans.sql_source is QuerySource.CACHE_HIT
    assert "layer5_guard" in nodes


# Property 10：答案状态映射 —— 正常路径 OK 且有 safe_sql、无 rows
@settings(max_examples=30)
@given(q=st.text(min_size=1, max_size=30))
def test_ok_maps_to_safe_sql(q: str) -> None:
    ans = QueryOrchestrator(_deps()).run(_req(q))
    assert ans.status is AnswerStatus.OK
    assert ans.safe_sql is not None and ans.rows is None


# Property 4：L3 不产出带范围的上下文（QueryContext 无 scope 字段）
def test_no_scope_in_context() -> None:
    from query_engine.models import QueryContext
    ctx = QueryContext(request=_req("q"))
    assert not hasattr(ctx, "org_ids") and not hasattr(ctx, "ps_ids")
