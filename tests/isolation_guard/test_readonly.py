"""Task 4 单测：只读校验器（Requirements 2.1~2.8）。"""

import pytest

from isolation_guard.errors import ReadOnlyViolation
from isolation_guard.readonly import SqlglotReadOnlyValidator

V = SqlglotReadOnlyValidator()
D = "starrocks"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT * FROM t WHERE a = 1",
        "WITH c AS (SELECT * FROM t) SELECT * FROM c",
        "SELECT a FROM t UNION SELECT a FROM s",
    ],
)
def test_valid_read_only_passes(sql: str) -> None:
    ast = V.parse_single(sql, D)
    V.assert_read_only(ast)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET a = 1",
        "DELETE FROM t",
        "DROP TABLE t",
        "CREATE TABLE t (a int)",
        "ALTER TABLE t ADD COLUMN b int",
        "TRUNCATE TABLE t",
    ],
)
def test_write_and_ddl_rejected(sql: str) -> None:
    with pytest.raises(ReadOnlyViolation):
        ast = V.parse_single(sql, D)
        V.assert_read_only(ast)


def test_multi_statement_rejected() -> None:
    with pytest.raises(ReadOnlyViolation):
        V.parse_single("SELECT 1; SELECT 2", D)


def test_empty_rejected() -> None:
    with pytest.raises(ReadOnlyViolation):
        V.parse_single("   ", D)


def test_syntax_error_rejected() -> None:
    with pytest.raises(ReadOnlyViolation):
        V.parse_single("SELECT FROM WHERE )(", D)


def test_command_rejected() -> None:
    with pytest.raises(ReadOnlyViolation):
        ast = V.parse_single("SET x = 1", D)
        V.assert_read_only(ast)
