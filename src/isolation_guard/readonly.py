"""只读强制校验（design.md §6）。

借鉴 pandas-ai 的只读校验思路（关键字/AST 校验），重新实现，不复制其源码。
用 sqlglot 解析为 AST：强制单条语句、顶层为查询、树内不得含任何写/DDL/命令节点。
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from isolation_guard.errors import ReadOnlyViolation


class SqlglotReadOnlyValidator:
    """只读校验器：parse_single（单语句）+ assert_read_only（禁写/DDL/命令）。"""

    # 树内任意位置出现即拒绝
    FORBIDDEN = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Alter,
        exp.TruncateTable,
        exp.Command,   # SET / USE / CALL / EXPLAIN 等裸命令
        exp.Into,      # SELECT ... INTO（物化写）
    )

    def parse_single(self, sql: str, dialect: str) -> exp.Expression:
        """解析并强制单条非空语句；空 / 多语句 / 解析失败 → ReadOnlyViolation。"""
        if not sql or not sql.strip():
            raise ReadOnlyViolation("空 SQL")
        try:
            stmts = sqlglot.parse(sql, dialect=dialect)
        except Exception as e:  # 语法非法：无法证明安全 → fail-closed
            raise ReadOnlyViolation(f"SQL 解析失败: {e!r}") from e
        real = [s for s in stmts if s is not None]
        if len(real) != 1:
            raise ReadOnlyViolation(f"只允许单条语句，检测到 {len(real)} 条")
        return real[0]

    def assert_read_only(self, ast: exp.Expression) -> None:
        """顶层必须是 Select/Union；树内禁止任何 FORBIDDEN 节点，否则 ReadOnlyViolation。"""
        root = ast.this if isinstance(ast, exp.Subquery) else ast
        if not isinstance(root, (exp.Select, exp.Union)):
            raise ReadOnlyViolation(f"只允许 SELECT/UNION，拒绝 {type(root).__name__}")
        if list(ast.find_all(*self.FORBIDDEN)):
            raise ReadOnlyViolation("检测到非 SELECT 操作")
