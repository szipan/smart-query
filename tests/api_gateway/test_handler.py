"""Task 8/9/12 单测：薄 Lambda 编排（顺序、限流早于路由、错误信封、边界）。"""

from api_gateway.handler import GatewayDependencies, GatewayRequest, ThinLambda
from api_gateway.observer import InMemoryGatewayObserver
from api_gateway.ratelimit import InMemoryCounterStore, RateLimiter
from api_gateway.router import RequestRouter, RoutingRule, RuleRegistry, RouteTarget

from ag_fakes import FakeEngineClient, FakeLegacyClient


def _req(question="本月发电量", claims=None, stream=False, request_id="req-1"):
    body = {"question": question}
    if stream:
        body["stream"] = True
    return GatewayRequest(body=body, authorizer_claims=claims or {"sub": "1001"}, request_id=request_id)


def test_query_path_ok(deps) -> None:
    resp = ThinLambda(deps).handle(_req())
    assert resp.status_code == 200
    assert resp.body["status"] == "ok"
    assert resp.body["source"] == "query"
    assert resp.body["request_id"] == "req-1"
    assert deps.engine_client.calls


def test_legacy_path(deps) -> None:
    resp = ThinLambda(deps).handle(_req(question="帮我导出报表"))
    assert resp.status_code == 200
    assert resp.body["source"] == "legacy"
    assert deps.legacy_client.calls


def test_missing_identity_401(deps) -> None:
    resp = ThinLambda(deps).handle(GatewayRequest(body={"question": "q"}, authorizer_claims={}))
    assert resp.status_code == 401
    assert resp.body["error"]["code"] == "UNAUTHORIZED"


def test_bad_request_empty_question(deps) -> None:
    resp = ThinLambda(deps).handle(_req(question="  "))
    assert resp.status_code == 400
    assert resp.body["error"]["code"] == "BAD_REQUEST"


def test_stream_true_returns_400(deps) -> None:
    resp = ThinLambda(deps).handle(_req(stream=True))
    assert resp.status_code == 400
    assert resp.body["error"]["code"] == "BAD_REQUEST"


def test_rate_limited_429_with_retry_after() -> None:
    deps = GatewayDependencies(
        rate_limiter=RateLimiter(InMemoryCounterStore(), default_limit=1),
        router=RequestRouter(RuleRegistry([])),
        engine_client=FakeEngineClient(),
    )
    tl = ThinLambda(deps)
    assert tl.handle(_req()).status_code == 200
    resp = tl.handle(_req())
    assert resp.status_code == 429
    assert resp.body["error"]["code"] == "RATE_LIMITED"
    assert resp.headers.get("Retry-After")


def test_rate_limit_before_route_and_downstream() -> None:
    # 超限时不应调用下游（Property 7：限流早于路由与下游）
    engine = FakeEngineClient()
    deps = GatewayDependencies(
        rate_limiter=RateLimiter(InMemoryCounterStore(), default_limit=0),  # 立即超限
        router=RequestRouter(RuleRegistry([])),
        engine_client=engine,
    )
    resp = ThinLambda(deps).handle(_req())
    assert resp.status_code == 429
    assert engine.calls == []      # 下游未被调用


def test_downstream_error_502(deps) -> None:
    deps.engine_client = FakeEngineClient(raise_downstream=True)
    resp = ThinLambda(deps).handle(_req())
    assert resp.status_code == 502
    assert resp.body["error"]["code"] == "DOWNSTREAM_ERROR"
    # 不泄露内部细节
    assert "5xx" not in resp.body["error"]["message"]


def test_orchestration_order_via_observer(deps) -> None:
    obs = InMemoryGatewayObserver()
    deps.observer = obs
    ThinLambda(deps).handle(_req())
    steps = [s for s, _, _ in obs.records]
    # 限流严格早于路由
    assert steps.index("rate_limit") < steps.index("route")


def test_request_id_generated_when_absent(deps) -> None:
    resp = ThinLambda(deps).handle(GatewayRequest(body={"question": "q"}, authorizer_claims={"sub": "1"}, request_id=None))
    assert resp.body["request_id"]  # 自动生成
