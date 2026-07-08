"""Task 13：L2 正确性属性测试（hypothesis）。覆盖 design.md §Correctness Properties。"""

from hypothesis import given, settings
from hypothesis import strategies as st

from api_gateway.handler import GatewayDependencies, GatewayRequest, ThinLambda
from api_gateway.ratelimit import InMemoryCounterStore, RateLimiter
from api_gateway.router import RequestRouter, RoutingRule, RuleRegistry, RouteTarget

from ag_fakes import FakeEngineClient, FakeLegacyClient


def _deps(limit=100):
    rules = [RoutingRule("legacy", {"type": "keyword", "keywords": ["导出报表"]}, RouteTarget.LEGACY, 10)]
    return GatewayDependencies(
        rate_limiter=RateLimiter(InMemoryCounterStore(), default_limit=limit),
        router=RequestRouter(RuleRegistry(rules)),
        engine_client=FakeEngineClient(),
        legacy_client=FakeLegacyClient(),
    )


# Property 6：路由确定性 —— 目标恒 ∈ {query, legacy}
@settings(max_examples=60)
@given(q=st.text(min_size=1, max_size=50))
def test_p6_route_deterministic(q: str) -> None:
    router = _deps().router
    d = router.route(q)
    assert d.target in (RouteTarget.QUERY, RouteTarget.LEGACY)
    if "导出报表" not in q:
        assert d.target is RouteTarget.QUERY  # 未命中必 QUERY


# Property 9：request_id 贯通 —— 响应 request_id 恒等于入口
@settings(max_examples=50)
@given(rid=st.text(min_size=1, max_size=20).filter(lambda s: s.strip()))
def test_p9_request_id_propagation(rid: str) -> None:
    resp = ThinLambda(_deps()).handle(
        GatewayRequest(body={"question": "q"}, authorizer_claims={"sub": "1"}, request_id=rid)
    )
    assert resp.body["request_id"] == rid


# Property 5/7：超限必 429 且不调下游
@settings(max_examples=30)
@given(n=st.integers(min_value=1, max_value=5))
def test_p5_over_limit_429_no_downstream(n: int) -> None:
    deps = _deps(limit=n)
    tl = ThinLambda(deps)
    for _ in range(n):
        assert tl.handle(GatewayRequest(body={"question": "q"}, authorizer_claims={"sub": "u"})).status_code == 200
    before = len(deps.engine_client.calls)
    resp = tl.handle(GatewayRequest(body={"question": "q"}, authorizer_claims={"sub": "u"}))
    assert resp.status_code == 429
    assert len(deps.engine_client.calls) == before  # 超限未再调下游


# Property 8：错误信封统一 —— 失败路径带 request_id
@settings(max_examples=40)
@given(rid=st.text(min_size=1, max_size=20).filter(lambda s: s.strip()))
def test_p8_error_envelope_has_request_id(rid: str) -> None:
    # 空 question → BAD_REQUEST 信封
    resp = ThinLambda(_deps()).handle(
        GatewayRequest(body={"question": "   "}, authorizer_claims={"sub": "1"}, request_id=rid)
    )
    assert resp.status_code == 400
    assert resp.body["error"]["request_id"] == rid
    assert resp.body["error"]["code"] == "BAD_REQUEST"
