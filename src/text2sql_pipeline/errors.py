"""Layer 4 异常层次（Requirements 1.4, 1.5, 5.5, 9.3, 9.4）。"""


class PipelineError(Exception):
    """Layer 4 异常基类。"""


class StrategyResolutionError(PipelineError):
    """编排器在启动阶段解析策略实现失败（缺失阶段/实现名）。"""

    def __init__(self, stage_type: str, strategy_name: str) -> None:
        self.stage_type = stage_type
        self.strategy_name = strategy_name
        super().__init__(f"无法解析策略：stage_type={stage_type!r}, strategy={strategy_name!r}（未注册）")


class UnknownStrategyError(PipelineError):
    """以未注册的 (stage_type, strategy_name) 查找。"""

    def __init__(self, stage_type: str, strategy_name: str) -> None:
        self.stage_type = stage_type
        self.strategy_name = strategy_name
        super().__init__(f"未知策略：stage_type={stage_type!r}, strategy={strategy_name!r}")


class DuplicateStrategyError(PipelineError):
    """以已存在的 (stage_type, strategy_name) 重复注册。"""

    def __init__(self, stage_type: str, strategy_name: str) -> None:
        self.stage_type = stage_type
        self.strategy_name = strategy_name
        super().__init__(f"策略已存在：stage_type={stage_type!r}, strategy={strategy_name!r}")


class StagePreconditionError(PipelineError):
    """某阶段执行时所需前置字段缺失。"""

    def __init__(self, stage_type: str, missing_field: str) -> None:
        self.stage_type = stage_type
        self.missing_field = missing_field
        super().__init__(f"阶段 {stage_type!r} 前置条件失败：缺少字段 {missing_field!r}")


class ConfigurationError(PipelineError):
    """配置非法（如策略缺少必需依赖）。"""
