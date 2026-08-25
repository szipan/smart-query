"""Legacy/Query 路由决策（design.md §4.5/§7；Requirements 5）。

补齐设计缺口：matcher 用**可序列化规格**（type: keyword/regex/prefix + 参数），
加载器由配置（文件/DynamoDB）构造谓词，支持热更新不改代码。
默认 fail-to-query：未命中任何 Legacy 规则 → QUERY。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from api_gateway.errors import GatewayError


class ConfigurationError(GatewayError):
    code = "BAD_REQUEST"
    http_status = 400


class RouteTarget(str, Enum):
    QUERY = "query"
    LEGACY = "legacy"


@dataclass(frozen=True)
class RouteDecision:
    target: RouteTarget
    rule_id: Optional[str] = None


def build_matcher(spec: dict) -> Callable[[str], bool]:
    """从可序列化 matcher 规格构造谓词。"""
    t = spec.get("type")
    if t == "keyword":
        kws = list(spec.get("keywords", []))
        return lambda q: any(k in q for k in kws)
    if t == "prefix":
        p = str(spec.get("prefix", ""))
        return lambda q: q.startswith(p)
    if t == "regex":
        flags = re.IGNORECASE if spec.get("ignorecase") else 0
        pat = re.compile(spec["pattern"], flags)
        return lambda q: pat.search(q) is not None
    raise ConfigurationError(f"未知 matcher 类型: {t!r}")


@dataclass(frozen=True)
class RoutingRule:
    rule_id: str
    matcher_spec: dict
    target: RouteTarget = RouteTarget.LEGACY
    priority: int = 100
    description: str = ""


class RuleRegistry:
    """持有按 priority 排序的规则（含编译后的谓词）。可从配置热重载。"""

    def __init__(self, rules: Optional[list[RoutingRule]] = None) -> None:
        self._rules: list[tuple[RoutingRule, Callable[[str], bool]]] = []
        for r in rules or []:
            self.add(r)

    def add(self, rule: RoutingRule) -> None:
        self._rules.append((rule, build_matcher(rule.matcher_spec)))
        self._rules.sort(key=lambda rm: rm[0].priority)

    @classmethod
    def from_config(cls, config: list[dict]) -> "RuleRegistry":
        return cls([RoutingRule(
            rule_id=c["rule_id"], matcher_spec=c["matcher"],
            target=RouteTarget(c.get("target", "legacy")),
            priority=int(c.get("priority", 100)),
            description=c.get("description", "")) for c in config])

    def ordered(self) -> list[tuple[RoutingRule, Callable[[str], bool]]]:
        return list(self._rules)


class RequestRouter:
    def __init__(self, registry: RuleRegistry) -> None:
        self._registry = registry

    def route(self, question: str) -> RouteDecision:
        for rule, matcher in self._registry.ordered():  # 按优先级，首个命中即返回
            if matcher(question):
                return RouteDecision(target=RouteTarget.LEGACY, rule_id=rule.rule_id)
        return RouteDecision(target=RouteTarget.QUERY, rule_id=None)  # 默认 fail-to-query
