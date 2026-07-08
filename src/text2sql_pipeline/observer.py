"""可观测（design.md §7.3，Requirements 10）。

Observer 接收每个阶段的开始/结束/异常事件。默认实现收集到内存或忽略。
不记录用户查询数据字段值或结果集内容。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from text2sql_pipeline.models import PipelineContext


@runtime_checkable
class Observer(Protocol):
    def on_stage_start(self, stage_type: str, strategy_name: str, ctx: PipelineContext) -> None: ...
    def on_stage_end(self, stage_type: str, ctx: PipelineContext) -> None: ...
    def on_stage_error(self, stage_type: str, error: Exception, ctx: PipelineContext) -> None: ...


class NullObserver:
    def on_stage_start(self, stage_type: str, strategy_name: str, ctx: PipelineContext) -> None:
        return None

    def on_stage_end(self, stage_type: str, ctx: PipelineContext) -> None:
        return None

    def on_stage_error(self, stage_type: str, error: Exception, ctx: PipelineContext) -> None:
        return None


class InMemoryObserver:
    """收集阶段事件（测试/开发用）。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def on_stage_start(self, stage_type: str, strategy_name: str, ctx: PipelineContext) -> None:
        self.events.append(("start", stage_type))

    def on_stage_end(self, stage_type: str, ctx: PipelineContext) -> None:
        self.events.append(("end", stage_type))

    def on_stage_error(self, stage_type: str, error: Exception, ctx: PipelineContext) -> None:
        self.events.append(("error", stage_type))
