"""隔离注入器（design.md §5 + §5A.4）。

在已通过只读校验的查询 AST 上，把每个受控物理表替换为"带隔离过滤的子查询"：
    T  ->  (SELECT * FROM T WHERE <隔离谓词>) AS <原别名>

- 非受控表原样透传；必控未映射表 fail-closed 抛 UnmappedControlledTableError。
- CTE 名规避：CTE 引用不是物理表，绝不包裹。
- 三种列绑定：ps_id（IDENTITY）、ps_key（前缀取 ps_id）、org_id（IDENTITY）。
- 本期默认子查询注入（做法二）：谓词右侧为权限子查询；空可见集在执行期天然 deny-all。
- 确定性、不调 LLM、不访问数据库。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlglot import exp

from isolation_guard.errors import UnmappedControlledTableError
from isolation_guard.policy import ColTransform, ColumnBinding, TablePolicy
from isolation_guard.scope import DataScope


@dataclass
class InjectionReport:
    """注入产物的可观测信息（供门面填充 GuardResult）。"""

    injected_tables: list[str] = field(default_factory=list)
    passthrough_tables: list[str] = field(default_factory=list)


def _isolation_column(binding: ColumnBinding) -> exp.Expression:
    """按列绑定构造隔离列表达式。"""
    if binding.transform is ColTransform.PS_ID_FROM_PS_KEY:
        # CAST(split_part(ps_key,'_',1) AS BIGINT) —— 前缀段即 ps_id
        return exp.Cast(
            this=exp.func(
                "split_part",
                exp.column(binding.column),
                exp.Literal.string("_"),
                exp.Literal.number(1),
            ),
            to=exp.DataType.build("bigint"),
        )
    return exp.column(binding.column)


def _bare_table(node: exp.Table) -> exp.Table:
    """复制表节点并去掉别名，用于子查询内部的 FROM（保留 db/catalog 限定）。"""
    t = node.copy()
    t.set("alias", None)
    return t


class SqlglotIsolationInjector:
    """确定性 AST 隔离注入器。"""

    def inject(
        self,
        ast: exp.Expression,
        scope: DataScope,
        policy: TablePolicy,
        dialect: str,
    ) -> tuple[exp.Expression, InjectionReport]:
        cte_names = {c.alias for c in ast.find_all(exp.CTE)}
        report = InjectionReport()

        def rewrite(node: exp.Expression) -> exp.Expression:
            if not isinstance(node, exp.Table):
                return node
            if node.name in cte_names:
                return node  # CTE 引用，非物理表

            cls = policy.classify(node.name)
            if cls.kind == "uncontrolled":
                report.passthrough_tables.append(node.name)
                return node
            if cls.kind == "must_control_unmapped":
                raise UnmappedControlledTableError(node.name)

            # controlled：包裹为带隔离谓词的子查询
            binding = cls.binding
            column = _isolation_column(binding)
            source = scope.source_for(binding.dimension)
            predicate = source.build_in_predicate(column)
            alias = node.alias_or_name

            inner = exp.select("*").from_(_bare_table(node)).where(predicate)
            report.injected_tables.append(node.name)
            return inner.subquery(alias=alias)

        new_ast = ast.transform(rewrite)
        return new_ast, report
