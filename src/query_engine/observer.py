"""可观测（design.md §11，Requirements 14）。日志脱敏，不落用户数据范围/凭证/结果内容。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from query_engine.models import NodeTrace


@runtime_checkable
class EngineObserver(Protocol):
    def on_node(self, trace: NodeTrace) -> None: ...


class InMemoryEngineObserver:
    def __init__(self) -> None:
        self.traces: list[NodeTrace] = []

    def on_node(self, trace: NodeTrace) -> None:
        self.traces.append(trace)


class NullEngineObserver:
    def on_node(self, trace: NodeTrace) -> None:
        return None
