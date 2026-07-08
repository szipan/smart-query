"""Task 6~13 单测：编排器（分支/委派/边界/本期执行截断）。Requirements 1/6/7/8/9/10/11/13。"""

import copy

from query_engine.config import CACHE_NAMESPACE
from query_engine.models import AnswerStatus, QuerySource
from query_engine.orchestrator import QueryOrchestrator

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
from query_engine.deps import EngineDependencies


def _deps(**over):
    base = dict(
        memory=FakeMemory(), llm=FakeLlm(), embedder=FakeEmbedder(),
        vector_store=FakeVectorStore(), entity_dictionary=FakeEntityDictionary(),
        layer4=FakeLayer4Client(), guard_factory=FakeGuardFactory(), datasource=None,
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
    assert nodes[:3] == ["context_rewrite", "term_normalization", "semantic_cache_lookup"]
    assert "layer4_pipeline" in nodes and "layer5_guard" in nodes


def test_cache_hit_skips_layer4_but_still_guards() -> None:
    vs = FakeVectorStore(data={CACHE_NAMESPACE: [{"similarity": 0.99, "pre_isolation_sql": "SELECT ps_id FROM t"}]})
    l4 = FakeLayer4Client()
    d = _deps(vector_store=vs, layer4=l4)
    from query_engine.models import CallerIdentity, QueryRequest
    req = QueryRequest(request_id="r", identity=CallerIdentity(user_id="1001"), raw_question="q")
    ans = QueryOrchestrator(d).run(req)
    assert ans.status is AnswerStatus.OK
    assert ans.sql_source is QuerySource.CACHE_HIT
    assert l4.last_request is None                     # L4 被短路
    nodes = [t.node for t in ans.request_traces]
    assert "layer5_guard" in nodes                     # 命中仍过 L5


def test_no_sql_answer() -> None:
    d = _deps(layer4=FakeLayer4Client(status="no_sql"))
    from query_engine.models import CallerIdentity, QueryRequest
    req = QueryRequest(request_id="r", identity=CallerIdentity(user_id="1001"), raw_question="q")
    ans = QueryOrchestrator(d).run(req)
    assert ans.status is AnswerStatus.NO_SQL
    assert ans.safe_sql is None


def test_upstream_error_answer() -> None:
    d = _deps(layer4=FakeLayer4Client(status="error"))
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
    d = _deps(vector_store=vs)
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
