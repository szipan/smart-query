"""策略注册表（design.md §7.2，Requirements 9）。

按 (stage_type, strategy_name) 注册与创建阶段实现。新增策略 = 注册一个实现，编排器无感。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from text2sql_pipeline.deps import PipelineStage, StageDependencies
from text2sql_pipeline.errors import DuplicateStrategyError, UnknownStrategyError

StageFactory = Callable[..., PipelineStage]


class StrategyRegistry:
    """按 (stage_type, strategy_name) 注册与创建阶段实现。"""

    def __init__(self) -> None:
        self._factories: dict[str, dict[str, StageFactory]] = defaultdict(dict)

    def register(self, stage_type: str, strategy_name: str, factory: StageFactory) -> None:
        if not stage_type or not strategy_name:
            raise ValueError("stage_type 与 strategy_name 均须为非空字符串")
        if strategy_name in self._factories[stage_type]:
            raise DuplicateStrategyError(stage_type, strategy_name)
        self._factories[stage_type][strategy_name] = factory

    def create(self, stage_type: str, strategy_name: str,
               deps: StageDependencies, params: dict[str, Any]) -> PipelineStage:
        try:
            factory = self._factories[stage_type][strategy_name]
        except KeyError:
            raise UnknownStrategyError(stage_type, strategy_name)
        stage = factory(deps=deps, params=params)
        assert isinstance(stage, PipelineStage), "工厂必须返回 PipelineStage"
        return stage

    def available(self, stage_type: str) -> list[str]:
        return sorted(self._factories.get(stage_type, {}).keys())


# 全局注册表 + 装饰器
REGISTRY = StrategyRegistry()


def register(stage_type: str, strategy_name: str):
    """装饰器：把一个工厂函数注册为策略。"""

    def deco(factory: StageFactory) -> StageFactory:
        REGISTRY.register(stage_type, strategy_name, factory)
        return factory

    return deco
