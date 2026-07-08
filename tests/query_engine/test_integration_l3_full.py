"""集成测试 L3 ↔ L4 ↔ L5：编排器串起真实的 text2sql-pipeline 与 isolation-guard。

验证端到端契约：L3 编排 → L4 生成未校验 SQL → L5 注入隔离 → L3 产出 safe_sql（本期无 rows）。
"""

from __future__ import annotations

# 真实 L4 / L5
from isolation_guard.guard import IsolationGuardFactory
from isolation_guard.models import CallerIdentity as IsoIdentity
from isolation_guard.sungrow_policy import sungrow_table_policy
from text2sql_pipeline.deps import StageDependencies
from text2sql_pipeline.models import PipelineConfig, PipelineInput
from text2sql_pipeline.orchestrator import build_orchestrator

# 被测 L3
from query_engine.deps import EngineDependencies, Layer4Request
from query_engine.models import AnswerStatus, CallerIdentity, QueryRequest, QuerySource
from query_engine.orchestrator import QueryOrchestrator

from qe_fakes import FakeEntityDictionary, FakeMemory


# ---- L4 需要的最小 mock 外部能力（LlmClient 带 model_id，与 L3 的不同）----
class _L4Embedder:
    def embed(self, texts):
        return [[0.1, 0.2] for _ in texts]


class _L4VectorStore:
    def similarity_search(self, vector, top_k, namespace):
        if namespace == "schema":
            return [{"text": "CREATE TABLE fact_power_station_kpi_month (ps_id INT, p83022 DECIMAL)"}]
        return []


class _L4Llm:
    def __init__(self, sql):
        self._sql = sql

    def complete(self, *, model_id, system, prompt, max_tokens=2048, temperature=0.0):
        return f"```sql\n{self._sql}\n```"


class _L4Datasource:
    dialect = "starrocks"

    def dry_run(self, sql):
        raise NotImplementedError

    def get_schema_snippet(self, table_names):
        return []


# ---- L3 侧适配器：把真实 L4/L5 适配到 L3 的 Protocol ----
class RealLayer4Adapter:
    def __init__(self, orchestrator):
        self._o = orchestrator

    def generate(self, request: Layer4Request):
        from text2sql_pipeline.models import ResolvedEntity as E, ResolvedTerm as T
        pi = PipelineInput(
            request_id=request.request_id,
            standalone_question=request.standalone_question,
            resolved_entities=tuple(E(e.surface, e.canonical, e.entity_type, e.entity_id) for e in request.resolved_entities),
            resolved_terms=tuple(T(t.term, t.definition, t.synonyms) for t in request.resolved_terms),
            datasource_id=request.datasource_id,
            model_id=request.model_id,
        )
        return self._o.run(pi)  # PipelineResult 满足 Layer4Result 鸭子类型


class RealGuardFactoryAdapter:
    def __init__(self, iso_factory):
        self._f = iso_factory

    def for_identity(self, identity: CallerIdentity):
        return self._f.for_identity(IsoIdentity(user_id=identity.user_id, claims=identity.claims))


def _build_l3(l4_sql: str) -> EngineDependencies:
    l4_deps = StageDependencies(
        embedder=_L4Embedder(), vector_store=_L4VectorStore(),
        llm_client=_L4Llm(l4_sql), datasource=_L4Datasource(), validator=None,
    )
    l4_orch = build_orchestrator(PipelineConfig(), l4_deps)
    iso_factory = IsolationGuardFactory(policy=sungrow_table_policy())

    return EngineDependencies(
        memory=FakeMemory(),
        llm=None,                     # 无历史时不需要改写 LLM
        embedder=None,                # L3 缓存/术语本用例不启用（None → 缓存未命中、软术语空）
        vector_store=None,
        entity_dictionary=FakeEntityDictionary(),
        layer4=RealLayer4Adapter(l4_orch),
        guard_factory=RealGuardFactoryAdapter(iso_factory),
        datasource=None,
    )


def test_l3_end_to_end_produces_isolated_safe_sql() -> None:
    deps = _build_l3("SELECT ps_id, p83022 FROM fact_power_station_kpi_month WHERE date_id = 20260501")
    req = QueryRequest(request_id="r", identity=CallerIdentity(user_id="1001"), raw_question="本月各电站发电量")
    ans = QueryOrchestrator(deps).run(req)

    assert ans.status is AnswerStatus.OK
    assert ans.sql_source is QuerySource.PIPELINE
    assert ans.rows is None                                   # 本期不执行
    # 真实 L5 已注入隔离子查询
    assert "sys_user_ps_share" in ans.safe_sql
    assert "ps_id IN (" in ans.safe_sql


def test_l3_rejects_when_l4_emits_write() -> None:
    # L4 生成写操作 → L4 内部 fixer(无 validator) 透传 → L3 送真实 L5 → REJECTED
    deps = _build_l3("DROP TABLE fact_power_station_kpi_month")
    req = QueryRequest(request_id="r", identity=CallerIdentity(user_id="1001"), raw_question="删表")
    ans = QueryOrchestrator(deps).run(req)
    assert ans.status is AnswerStatus.REJECTED
    assert ans.safe_sql is None
