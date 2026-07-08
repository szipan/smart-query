"""集成测试 L2 ↔ L3：EngineClient 适配真实 query-engine 编排器。

验证跨层契约：L2 认证/限流/路由后透传身份调 L3，L3 返回的答案被整形为统一信封。
（L3 内部的 L4/L5 用进程内 fake，聚焦 L2↔L3 契约；L3↔L4↔L5 全链路已在 query_engine 集成测试覆盖。）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from api_gateway.errors import DownstreamError
from api_gateway.handler import GatewayDependencies, GatewayRequest, ThinLambda
from api_gateway.ratelimit import InMemoryCounterStore, RateLimiter
from api_gateway.router import RequestRouter, RuleRegistry

from query_engine.deps import EngineDependencies
from query_engine.models import CallerIdentity as QeIdentity, QueryRequest as QeRequest
from query_engine.orchestrator import QueryOrchestrator


# ---- L3 内部的进程内 fake（L4/L5/memory 等）----
@dataclass
class _L4Res:
    status: str
    unvalidated_sql: Optional[str]


class _L4:
    def generate(self, request):
        return _L4Res("ok", "SELECT ps_id FROM fact_power_station_kpi_month")


@dataclass
class _Gr:
    status: str
    safe_sql: Optional[str]


class _Guard:
    def guard(self, sql, request_id):
        return _Gr("allowed", "SELECT ps_id FROM (isolated) t")


class _GuardFactory:
    def for_identity(self, identity):
        return _Guard()


class _Memory:
    def read_recent_turns(self, *a):
        return []

    def write_turn(self, *a):
        pass


def _l3_orchestrator() -> QueryOrchestrator:
    deps = EngineDependencies(
        memory=_Memory(), llm=None, embedder=None, vector_store=None,
        entity_dictionary=None, layer4=_L4(), guard_factory=_GuardFactory(), datasource=None,
    )
    return QueryOrchestrator(deps)


# ---- L2 侧 EngineClient 适配器：把 L2 请求转 L3，运行 L3，返回数据 ----
class L3EngineClient:
    def __init__(self, orchestrator: QueryOrchestrator) -> None:
        self._o = orchestrator

    def invoke(self, req, identity):
        qr = QeRequest(
            request_id=identity.request_id,
            identity=QeIdentity(user_id=identity.user_id, claims=identity.raw_claims),
            raw_question=req.question,
            session_id=req.session_id,
        )
        ans = self._o.run(qr)
        if ans.status.value != "ok":
            raise DownstreamError(f"engine status {ans.status.value}")
        return {"safe_sql": ans.safe_sql, "sql_source": ans.sql_source.value if ans.sql_source else None}


def _deps() -> GatewayDependencies:
    return GatewayDependencies(
        rate_limiter=RateLimiter(InMemoryCounterStore(), default_limit=100),
        router=RequestRouter(RuleRegistry([])),
        engine_client=L3EngineClient(_l3_orchestrator()),
    )


def test_l2_to_l3_end_to_end() -> None:
    resp = ThinLambda(_deps()).handle(
        GatewayRequest(body={"question": "本月各电站发电量"},
                       authorizer_claims={"sub": "1001"}, request_id="req-xyz")
    )
    assert resp.status_code == 200
    assert resp.body["status"] == "ok"
    assert resp.body["source"] == "query"
    assert resp.body["request_id"] == "req-xyz"
    # L3 返回的 safe_sql 被整形进 data
    assert resp.body["data"]["safe_sql"]
    assert resp.body["data"]["sql_source"] == "pipeline"
