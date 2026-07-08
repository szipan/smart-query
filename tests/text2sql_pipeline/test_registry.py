"""Task 5 单测：策略注册表（Requirements 9）。"""

import pytest

from text2sql_pipeline.deps import StageDependencies
from text2sql_pipeline.errors import DuplicateStrategyError, UnknownStrategyError
from text2sql_pipeline.registry import StrategyRegistry


class _Stage:
    stage_type = "context_acquisition"
    strategy_name = "dummy"

    def run(self, ctx):
        return ctx


def _factory(deps, params):
    return _Stage()


def test_register_and_create() -> None:
    reg = StrategyRegistry()
    reg.register("context_acquisition", "dummy", _factory)
    stage = reg.create("context_acquisition", "dummy", StageDependencies(), {})
    assert stage.strategy_name == "dummy"


def test_duplicate_registration_raises_and_keeps_first() -> None:
    reg = StrategyRegistry()
    reg.register("context_acquisition", "dummy", _factory)
    with pytest.raises(DuplicateStrategyError):
        reg.register("context_acquisition", "dummy", _factory)


def test_unknown_strategy_raises() -> None:
    reg = StrategyRegistry()
    with pytest.raises(UnknownStrategyError):
        reg.create("context_acquisition", "nope", StageDependencies(), {})


def test_available_lists_registered() -> None:
    reg = StrategyRegistry()
    reg.register("context_acquisition", "dummy", _factory)
    assert reg.available("context_acquisition") == ["dummy"]


def test_available_empty_for_unknown_stage_type() -> None:
    reg = StrategyRegistry()
    assert reg.available("nonexistent") == []


def test_empty_names_rejected() -> None:
    reg = StrategyRegistry()
    with pytest.raises(ValueError):
        reg.register("", "x", _factory)
