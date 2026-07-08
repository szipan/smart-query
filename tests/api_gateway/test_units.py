"""Task 2/3/4/5/6 单测：信封、身份、认证、限流、路由。"""

import threading

import pytest

from api_gateway.auth import Authorizer
from api_gateway.envelope import parse_query_request
from api_gateway.errors import BadRequest, RateLimitExceeded, StreamingNotAvailable, Unauthorized
from api_gateway.identity import build_identity
from api_gateway.ratelimit import InMemoryCounterStore, RateLimiter
from api_gateway.router import RequestRouter, RoutingRule, RuleRegistry, RouteTarget

from ag_fakes import FakeVerifier


# ---- 信封/校验 ----
def test_parse_valid() -> None:
    qr = parse_query_request({"question": "本月发电量"})
    assert qr.question == "本月发电量" and qr.stream is False


def test_parse_empty_question_bad_request() -> None:
    with pytest.raises(BadRequest):
        parse_query_request({"question": "  "})


def test_parse_too_long_bad_request() -> None:
    with pytest.raises(BadRequest):
        parse_query_request({"question": "x" * 2001})


def test_parse_stream_true_rejected() -> None:
    with pytest.raises(StreamingNotAvailable):
        parse_query_request({"question": "q", "stream": True})


# ---- 身份 ----
def test_build_identity_ok() -> None:
    idc = build_identity({"sub": "1001", "scope": "read write", "tenant_id": "t1"}, "r1")
    assert idc.user_id == "1001"
    assert idc.scopes == ("read", "write")
    assert idc.request_id == "r1"


def test_build_identity_missing_user_unauthorized() -> None:
    with pytest.raises(Unauthorized):
        build_identity({}, "r1")


def test_identity_does_not_resolve_scope_ranges() -> None:
    idc = build_identity({"sub": "1001"}, "r1")
    assert not hasattr(idc, "org_ids")
    assert not hasattr(idc, "ps_ids")


# ---- 认证（选项 A）----
def test_authorizer_valid_token() -> None:
    claims = Authorizer(FakeVerifier()).authorize("Bearer good")
    assert claims["sub"] == "1001"


@pytest.mark.parametrize("hdr", [None, "", "good", "Basic x", "Bearer "])
def test_authorizer_invalid(hdr) -> None:
    with pytest.raises(Unauthorized):
        Authorizer(FakeVerifier()).authorize(hdr)


def test_authorizer_bad_token(hdr="Bearer wrong") -> None:
    with pytest.raises(Unauthorized):
        Authorizer(FakeVerifier()).authorize(hdr)


# ---- 限流 ----
def test_rate_limiter_allows_then_blocks() -> None:
    rl = RateLimiter(InMemoryCounterStore(), default_limit=2)
    assert rl.check_and_increment("u", "r").allowed
    assert rl.check_and_increment("u", "r").allowed
    d = rl.check_and_increment("u", "r")
    assert d.allowed is False and d.retry_after > 0


def test_rate_limiter_atomic_under_concurrency() -> None:
    rl = RateLimiter(InMemoryCounterStore(), default_limit=50)
    allowed = []
    lock = threading.Lock()

    def worker():
        d = rl.check_and_increment("u", "r")
        with lock:
            allowed.append(d.allowed)

    threads = [threading.Thread(target=worker) for _ in range(200)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 恰好 50 次放行，无丢失/超发
    assert sum(1 for a in allowed if a) == 50


# ---- 路由 ----
def _router(rules):
    return RequestRouter(RuleRegistry(rules))


def test_route_miss_defaults_query() -> None:
    r = _router([]).route("随便问点啥")
    assert r.target is RouteTarget.QUERY and r.rule_id is None


def test_route_keyword_hit_legacy() -> None:
    rules = [RoutingRule("r1", {"type": "keyword", "keywords": ["导出报表"]}, RouteTarget.LEGACY, 10)]
    r = _router(rules).route("帮我导出报表")
    assert r.target is RouteTarget.LEGACY and r.rule_id == "r1"


def test_route_priority_first_match() -> None:
    rules = [
        RoutingRule("low", {"type": "keyword", "keywords": ["报表"]}, RouteTarget.LEGACY, 20),
        RoutingRule("high", {"type": "keyword", "keywords": ["报表"]}, RouteTarget.LEGACY, 5),
    ]
    assert _router(rules).route("看报表").rule_id == "high"


def test_route_regex_and_prefix() -> None:
    rr = RuleRegistry.from_config([
        {"rule_id": "re", "matcher": {"type": "regex", "pattern": "导出.*excel", "ignorecase": True}},
        {"rule_id": "pre", "matcher": {"type": "prefix", "prefix": "SQL:"}, "priority": 5},
    ])
    router = RequestRouter(rr)
    assert router.route("请导出为EXCEL").rule_id == "re"
    assert router.route("SQL: select 1").rule_id == "pre"
