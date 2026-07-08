"""Layer 5 自定义异常层次（Requirements 12.1）。

所有异常继承 IsolationGuardError，便于门面统一 fail-closed 捕获：
- guard(...) 捕获基类 → 转为 REJECTED 结构化结果
- validate_for_probe(...) 直接向上抛（Layer 4 依赖异常语义）
"""


class IsolationGuardError(Exception):
    """本层所有异常的基类。捕获它即可统一 fail-closed。"""


class ReadOnlyViolation(IsolationGuardError):
    """非 SELECT / 多语句 / 解析失败 / 注入后复核失败。"""


class UnmappedControlledTableError(IsolationGuardError):
    """必控却未映射的表（fail-closed 核心异常）。"""

    def __init__(self, table_name: str) -> None:
        self.table_name = table_name
        super().__init__(
            f"表 '{table_name}' 必须受控但未在 TablePolicy 中映射（fail-closed 拒绝）"
        )


class PermissionResolutionError(IsolationGuardError):
    """身份不可解析：token 失效 / 权限系统不可达 / 无授权范围解析失败。"""


class ConfigurationError(IsolationGuardError):
    """配置自身非法（如隔离列名与 scope 字段不一致、scope id 为 bool 等）。"""
