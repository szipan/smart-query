"""Layer 2 测试共享 fixture。"""

from __future__ import annotations

import pytest

from api_gateway.handler import GatewayDependencies
from api_gateway.ratelimit import InMemoryCounterStore, RateLimiter
from api_gateway.router import RequestRouter, RoutingRule, RuleRegistry, RouteTarget

from ag_fakes import FakeEngineClient, FakeLegacyClient


@pytest.fixture
def legacy_registry() -> RuleRegistry:
    return RuleRegistry([
        RoutingRule(rule_id="legacy_export", matcher_spec={"type": "keyword", "keywords": ["导出报表"]},
                    target=RouteTarget.LEGACY, priority=10, description="导出类走 legacy"),
    ])


@pytest.fixture
def deps(legacy_registry) -> GatewayDependencies:
    return GatewayDependencies(
        rate_limiter=RateLimiter(InMemoryCounterStore(), default_limit=100),
        router=RequestRouter(legacy_registry),
        engine_client=FakeEngineClient(),
        legacy_client=FakeLegacyClient(),
    )
