"""Task 1 单测：工程骨架与常量。"""

from isolation_guard import config


def test_dialect_is_starrocks() -> None:
    # 本期 dialect 硬编码为 starrocks（Requirements 11.1）
    assert config.DIALECT == "starrocks"
