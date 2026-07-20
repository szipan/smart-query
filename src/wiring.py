"""真实依赖组装（wiring）：把 Bedrock / OpenSearch / DynamoDB 适配器接到四层核心。

不改动任何一层的核心逻辑，只在此处：
1. 用环境变量（AwsSettings）构造真实能力实现；
2. 复用集成测试已验证的跨层适配思路（clean-room，生产版）：
   - RealLayer4Adapter：L3 的 Layer4Client -> 真实 L4 PipelineOrchestrator。
   - RealGuardFactoryAdapter：L3 的 Layer5GuardFactory -> 真实 L5 IsolationGuardFactory。
   - L3EngineClient：L2 的 EngineClient -> 真实 L3 QueryOrchestrator。
3. 模型切换：把 settings.llm_model_id 注入每个请求的 model_id（单一切换点）。
"""

from __future__ import annotations

from typing import Any, Optional

from adapters.agentcore_memory import AgentCoreMemory
from adapters.bedrock import BedrockEmbedder, BedrockLlmClient
from adapters.dynamodb import DynamoDbCounterStore
from adapters.entity_dictionary import build_entity_dictionary_from_dynamodb
from adapters.opensearch import OpenSearchVectorStore, make_opensearch_client
from adapters.settings import AwsSettings

_UNSET = object()   # 区分「未提供」与「显式 None（关闭硬通道）」

# L2
from api_gateway.errors import DownstreamError
from api_gateway.handler import GatewayDependencies, ThinLambda
from api_gateway.ratelimit import RateLimiter
from api_gateway.router import RequestRouter, RuleRegistry

# L3
from query_engine.deps import EngineDependencies, Layer4Request
from query_engine.models import CallerIdentity as QeIdentity, QueryRequest as QeRequest
from query_engine.orchestrator import QueryOrchestrator

# L4
from text2sql_pipeline.deps import StageDependencies
from text2sql_pipeline.models import (
    PipelineConfig,
    PipelineInput,
    ResolvedEntity as PLEntity,
    ResolvedTerm as PLTerm,
)
from text2sql_pipeline.orchestrator import build_orchestrator

# L5
from isolation_guard.guard import IsolationGuardFactory
from isolation_guard.models import CallerIdentity as IsoIdentity
from isolation_guard.sungrow_policy import sungrow_table_policy


# ---------- 轻量能力（本期不接 AgentCore Memory / 无真实数据源执行） ----------
class InMemoryMemory:
    """进程内多轮 Memory（本期占位；未来替换为 AgentCore Memory SDK）。"""

    def __init__(self) -> None:
        self._turns: dict[str, list[dict]] = {}

    def _key(self, actor_id: str, session_id: Optional[str]) -> str:
        return f"{actor_id}#{session_id or '-'}"

    def read_recent_turns(self, actor_id: str, session_id: Optional[str], limit: int) -> list[dict]:
        return self._turns.get(self._key(actor_id, session_id), [])[-limit:]

    def write_turn(self, actor_id: str, session_id: Optional[str], question: str, answer_summary: str) -> None:
        self._turns.setdefault(self._key(actor_id, session_id), []).append(
            {"question": question, "answer_summary": answer_summary})


class _StarrocksDialect:
    """L4 只需要 datasource.dialect（RAG 走 vector_store，不查真实库）。"""

    dialect = "starrocks"

    def get_schema_snippet(self, table_names: list[str]) -> list[str]:
        return []


# ---------- 跨层适配器（生产版） ----------
class RealLayer4Adapter:
    def __init__(self, orchestrator) -> None:
        self._o = orchestrator

    def generate(self, request: Layer4Request):
        pi = PipelineInput(
            request_id=request.request_id,
            standalone_question=request.standalone_question,
            resolved_entities=tuple(
                PLEntity(e.surface, e.canonical, e.entity_type, e.entity_id)
                for e in request.resolved_entities
            ),
            resolved_terms=tuple(
                PLTerm(t.term, t.definition, t.synonyms) for t in request.resolved_terms
            ),
            datasource_id=request.datasource_id,
            model_id=request.model_id,
        )
        return self._o.run(pi)


class RealGuardFactoryAdapter:
    def __init__(self, iso_factory) -> None:
        self._f = iso_factory

    def for_identity(self, identity):
        return self._f.for_identity(IsoIdentity(user_id=identity.user_id, claims=identity.claims))


class L3EngineClient:
    """L2 EngineClient：把网关请求转为 L3 请求并运行，注入默认 model_id。"""

    def __init__(self, orchestrator: QueryOrchestrator, default_model_id: str) -> None:
        self._o = orchestrator
        self._model_id = default_model_id

    def invoke(self, req, identity) -> dict:
        qr = QeRequest(
            request_id=identity.request_id,
            identity=QeIdentity(user_id=identity.user_id, claims=identity.raw_claims),
            raw_question=req.question,
            session_id=req.session_id,
            model_id=self._model_id,
        )
        ans = self._o.run(qr)
        if ans.status.value != "ok":
            raise DownstreamError(f"engine status {ans.status.value}: {ans.message or ''}")
        return {
            "safe_sql": ans.safe_sql,
            "sql_source": ans.sql_source.value if ans.sql_source else None,
        }


# ---------- 组装入口 ----------
def build_embedder(settings: AwsSettings, client: Any = None) -> BedrockEmbedder:
    return BedrockEmbedder(client=client, region=settings.region,
                           model_id=settings.embed_model_id, dimensions=settings.embed_dimensions)


def build_llm(settings: AwsSettings, client: Any = None) -> BedrockLlmClient:
    return BedrockLlmClient(client=client, region=settings.region,
                            default_model_id=settings.llm_model_id)


def build_vector_store(settings: AwsSettings, client: Any = None) -> OpenSearchVectorStore:
    if client is None:
        if not settings.opensearch_endpoint:
            raise ValueError("缺少 SMARTQUERY_OPENSEARCH_ENDPOINT")
        client = make_opensearch_client(endpoint=settings.opensearch_endpoint, region=settings.region)
    return OpenSearchVectorStore(client=client, index=settings.opensearch_index)


def build_counter_store(settings: AwsSettings, client: Any = None) -> DynamoDbCounterStore:
    return DynamoDbCounterStore(table_name=settings.ddb_table, client=client, region=settings.region)


def build_memory(settings: AwsSettings, client: Any = None):
    """有 memory_id → AgentCore Memory（多实例持久）；否则退回进程内占位并告警。"""
    if settings.memory_id:
        return AgentCoreMemory(memory_id=settings.memory_id, client=client, region=settings.region)
    import warnings
    warnings.warn("未设置 SMARTQUERY_MEMORY_ID，回退进程内 InMemoryMemory（非持久，仅本地）")
    return InMemoryMemory()


def build_entity_dictionary(settings: AwsSettings, client: Any = None):
    """硬通道运行时匹配器（Aho-Corasick，从 DynamoDB entity 表加载 enabled 实体别名）。"""
    if client is None:
        import boto3
        client = boto3.client("dynamodb", region_name=settings.region)
    return build_entity_dictionary_from_dynamodb(client, settings.entity_table)


def build_engine(settings: AwsSettings, *, embedder=None, vector_store=None, llm=None,
                 memory=None, entity_dictionary=_UNSET) -> QueryOrchestrator:
    """组装 L3 QueryOrchestrator（内部含真实 L4 与 L5）。

    entity_dictionary：未提供 → 从 DynamoDB 构造 Aho-Corasick 匹配器；显式 None → 关闭硬通道。
    """
    embedder = embedder or build_embedder(settings)
    vector_store = vector_store or build_vector_store(settings)
    llm = llm or build_llm(settings)
    if entity_dictionary is _UNSET:
        entity_dictionary = build_entity_dictionary(settings)

    l4_deps = StageDependencies(
        embedder=embedder, vector_store=vector_store, llm_client=llm,
        datasource=_StarrocksDialect(), validator=None,
    )
    l4_orch = build_orchestrator(PipelineConfig(), l4_deps)
    iso_factory = IsolationGuardFactory(policy=sungrow_table_policy())

    engine_deps = EngineDependencies(
        memory=memory or build_memory(settings),
        llm=llm, embedder=embedder, vector_store=vector_store,
        entity_dictionary=entity_dictionary,
        layer4=RealLayer4Adapter(l4_orch),
        guard_factory=RealGuardFactoryAdapter(iso_factory),
        datasource=None,
    )
    return QueryOrchestrator(engine_deps)


def build_gateway(settings: AwsSettings, *, engine: QueryOrchestrator = None,
                  counter_store=None) -> ThinLambda:
    """组装 L2 ThinLambda（限流用真实 DynamoDB CounterStore）。"""
    engine = engine or build_engine(settings)
    counter_store = counter_store or build_counter_store(settings)
    deps = GatewayDependencies(
        rate_limiter=RateLimiter(counter_store, default_limit=settings.user_rate_limit),
        router=RequestRouter(RuleRegistry([])),
        engine_client=L3EngineClient(engine, settings.llm_model_id),
    )
    return ThinLambda(deps)
