"""可观测 / 审计（design.md §11，Requirements 13）。

IsolationObserver 接收每次守卫决策的 IsolationDecision 事件。
默认实现把事件收集到内存列表（生产可换为结构化日志/指标后端）。
审计只落计数与表名/状态，绝不落具体 scope id 或敏感 SQL 明文。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from isolation_guard.models import IsolationDecision


@runtime_checkable
class IsolationObserver(Protocol):
    def on_decision(self, decision: IsolationDecision) -> None: ...


class InMemoryObserver:
    """默认观察者：收集决策事件到内存（测试/开发用）。"""

    def __init__(self) -> None:
        self.decisions: list[IsolationDecision] = []

    def on_decision(self, decision: IsolationDecision) -> None:
        self.decisions.append(decision)


class NullObserver:
    """空观察者：不记录（当不需要审计时使用）。"""

    def on_decision(self, decision: IsolationDecision) -> None:  # noqa: ARG002
        return None
