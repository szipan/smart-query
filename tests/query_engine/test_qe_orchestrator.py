"""Task 6~13 单测：编排器（分支/委派/边界/本期执行截断）。Requirements 1/6/7/8/9/10/11/13。"""

import copy

from query_engine.config import CACHE_NAMESPACE
from query_engine.models import AnswerStatus, QuerySource, SemanticSlots
from query_engine.orchestrator import QueryOrchestrator

# 规范化改写 LLM 结构化输出（用于让 canonical_rewrite 产出 slots）
_CANON_JSON = ('{"canonical":"canon","slots":{"comparison":"GT","threshold":"1000000W",'
               '"metric":"装机容量","group_by":"电站","agg_func":"SUM","sort_direction":"DESC",'
               '"sort_field":"装机容量","output_entity":"电站","product_line":["户用"]}}')
_SLOTS = SemanticSlots(comparison="GT", threshold_watts=1000000, metric="装机容量",
                       group_by="电站", agg_func="SUM", sort_direction="DESC",
                       sort_field="装机容量", output_entity="电站", product_line=("户用",))

from qe_fakes import (
    FakeEmbedder,
    FakeEntityDictionary,
    FakeGuard,
    FakeGuardFactory,
    FakeSqlGeneratorClient,
    FakeLlm,
    FakeMemory,
    FakeVectorStore,
)
from query_engine.deps import EngineDependencies


def _deps(**over):
    base = dict(
        memory=FakeMemory(), llm=FakeLlm(), embedder=FakeEmbedder(),
        vector_store=FakeVectorStore(), entity_dictionary=FakeEntityDictionary(),
        sql_generator=FakeSqlGeneratorClient(), guard_factory=FakeGuardFactory(), datasource=None,
    )
    base.update(over)
    return EngineDependencies(**base)


def test_miss_path_ok_returns_safe_sql_no_rows(deps, request_obj) -> None:
    ans = QueryOrchestrator(deps).run(request_obj)
    assert ans.status is AnswerStatus.OK
    assert ans.safe_sql is not None
    assert ans.rows is None                 # 本期不执行
    assert ans.sql_source is QuerySource.PIPELINE


def test_fixed_node_order(deps, request_obj) -> None:
    ans = QueryOrchestrator(deps).run(request_obj)
    nodes = [t.node for t in ans.request_traces]
    assert nodes[:4] == [
        "context_rewrite", "term_normalization", "canonical_rewrite", "semantic_cache_lookup",
    ]
    assert "sql_generator" in nodes and "security_guard" in nodes


def test_cache_hit_skips_layer4_but_still_guards() -> None:
    vs = FakeVectorStore(data={CACHE_NAMESPACE: [
        {"similarity": 0.99, "pre_isolation_sql": "SELECT ps_id FROM t",
         "slots_json": _SLOTS.to_json()}]})
    l4 = FakeSqlGeneratorClient()
    d = _deps(vector_store=vs, sql_generator=l4, llm=FakeLlm(out=_CANON_JSON))
    from query_engine.models import CallerIdentity, QueryRequest
    req = QueryRequest(request_id="r", identity=CallerIdentity(user_id="1001"), raw_question="q")
    ans = QueryOrchestrator(d).run(req)
    assert ans.status is AnswerStatus.OK
    assert ans.sql_source is QuerySource.CACHE_HIT
    assert l4.last_request is None                     # L4 被短路
    nodes = [t.node for t in ans.request_traces]
    assert "security_guard" in nodes                     # 命中仍过 security-guard


def test_no_sql_answer() -> None:
    d = _deps(sql_generator=FakeSqlGeneratorClient(status="no_sql"))
    from query_engine.models import CallerIdentity, QueryRequest
    req = QueryRequest(request_id="r", identity=CallerIdentity(user_id="1001"), raw_question="q")
    ans = QueryOrchestrator(d).run(req)
    assert ans.status is AnswerStatus.NO_SQL
    assert ans.safe_sql is None


def test_upstream_error_answer() -> None:
    d = _deps(sql_generator=FakeSqlGeneratorClient(status="error"))
    from query_engine.models import CallerIdentity, QueryRequest
    req = QueryRequest(request_id="r", identity=CallerIdentity(user_id="1001"), raw_question="q")
    ans = QueryOrchestrator(d).run(req)
    assert ans.status is AnswerStatus.UPSTREAM_ERROR


def test_rejected_answer() -> None:
    d = _deps(guard_factory=FakeGuardFactory(FakeGuard(status="rejected")))
    from query_engine.models import CallerIdentity, QueryRequest
    req = QueryRequest(request_id="r", identity=CallerIdentity(user_id="1001"), raw_question="q")
    ans = QueryOrchestrator(d).run(req)
    assert ans.status is AnswerStatus.REJECTED
    assert ans.safe_sql is None


def test_store_on_pipeline_success() -> None:
    vs = FakeVectorStore()
    d = _deps(vector_store=vs, llm=FakeLlm(out=_CANON_JSON))
    from query_engine.models import CallerIdentity, QueryRequest
    req = QueryRequest(request_id="r", identity=CallerIdentity(user_id="1001"), raw_question="q")
    QueryOrchestrator(d).run(req)
    # 未命中 ∧ L5 通过 → 存隔离前 SQL（本期放宽条件）
    assert any(ns == CACHE_NAMESPACE for ns, _ in vs.upserts)


def test_identity_passed_to_guard_factory(deps, request_obj) -> None:
    QueryOrchestrator(deps).run(request_obj)
    assert deps.guard_factory.identities[0].user_id == "1001"


def test_write_memory_on_finish(deps, request_obj) -> None:
    QueryOrchestrator(deps).run(request_obj)
    assert deps.memory.written  # 收尾写回


def test_request_immutable_after_run(deps, request_obj) -> None:
    before = copy.deepcopy(request_obj)
    QueryOrchestrator(deps).run(request_obj)
    assert request_obj == before


def test_context_has_no_scope_fields() -> None:
    from query_engine.models import QueryContext, CallerIdentity, QueryRequest
    ctx = QueryContext(request=QueryRequest(request_id="r", identity=CallerIdentity(user_id="1"), raw_question="q"))
    assert not hasattr(ctx, "org_ids")
    assert not hasattr(ctx, "ps_ids")
