"""数据范围（scope）源与 DataScope（design.md §4.1 + §5A.3）。

ScopeSource 有两种实现：
- SubqueryScopeSource（本期默认）：把权限子查询作为 IN 右侧嵌入（做法二），零 DB 连接。
- MaterializedScopeSource（做法一）：物化 id 列表。本期仅占位，不实现渲染（未来配置切换）。

ScopeResolver 负责构造 SubqueryScopeSource；本模块只定义源与其 IN 谓词渲染。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Optional

import sqlglot
from sqlglot import exp

from isolation_guard.config import DIALECT
from isolation_guard.errors import ConfigurationError
from isolation_guard.models import CallerIdentity, ScopeDim


class ScopeSource(abc.ABC):
    """某个维度（PS/ORG）的隔离注入源，负责生成 `<column> IN (...)` 谓词。"""

    dimension: ScopeDim

    @abc.abstractmethod
    def build_in_predicate(self, column: exp.Expression) -> exp.Expression:
        """给定隔离列表达式，返回完整的 IN 谓词表达式。"""
        raise NotImplementedError


@dataclass(frozen=True)
class SubqueryScopeSource(ScopeSource):
    """做法二（默认）：`<column> IN (<权限子查询>)`。

    subquery 为一条 SELECT 表达式（由 ScopeResolver 构造，含 user_id 字面量与权限表 join）。
    本期不执行、零 DB 连接：这里只是把子查询作为文本/AST 嵌入输出 SQL。
    """

    dimension: ScopeDim
    subquery: exp.Expression

    def build_in_predicate(self, column: exp.Expression) -> exp.Expression:
        # 用 .subquery() 包裹以生成带括号的 `IN (SELECT ...)`
        return exp.In(this=column, query=self.subquery.copy().subquery())


@dataclass(frozen=True)
class MaterializedScopeSource(ScopeSource):
    """做法一：`<column> IN (<字面量列表>)`。本期仅占位，不实现渲染。"""

    dimension: ScopeDim
    ids: frozenset = field(default_factory=frozenset)

    def build_in_predicate(self, column: exp.Expression) -> exp.Expression:
        raise NotImplementedError(
            "MaterializedScopeSource（做法一）本期不实现；默认使用 SubqueryScopeSource。"
        )


@dataclass(frozen=True)
class DataScope:
    """调用方数据范围：可见 ps_id 源 + 可见 org_id 源。"""

    ps_source: ScopeSource
    org_source: ScopeSource
    resolved_at: float = 0.0

    def source_for(self, dimension: ScopeDim) -> ScopeSource:
        if dimension is ScopeDim.PS:
            return self.ps_source
        if dimension is ScopeDim.ORG:
            return self.org_source
        raise ConfigurationError(f"未知隔离维度: {dimension}")


# ============================================================================
# Task 5：ScopeResolver（本期：构造子查询源，零 DB 连接）
# ============================================================================


@dataclass(frozen=True)
class PermissionFilterSpec:
    """授权行过滤规则（占位）。

    权限表标志位（is_active / share_type / is_public_share / is_under_share /
    is_channel_partner 等）的取舍规则**业务尚未确定**，此处以可配置占位形式存在：
    - 每个槽位（share / org / public）可注入一段 SQL 条件片段（业务给出规则后填入）。
    - 未配置槽位 → 按 placeholder_mode 处理：
        * "deny"（默认，安全）：该槽位过滤为 FALSE（deny-all，不放行全部授权行）。
        * "allow_dev"：该槽位过滤为 TRUE（**仅供本地开发显式 opt-in，不安全**）。

    Requirements 18.1/18.2/18.3。
    """

    share_filter: Optional[str] = None
    org_filter: Optional[str] = None
    public_filter: Optional[str] = None
    placeholder_mode: str = "deny"

    def predicate_for(self, slot: str) -> str:
        configured = {
            "share": self.share_filter,
            "org": self.org_filter,
            "public": self.public_filter,
        }.get(slot)
        if configured:
            return configured
        # 占位：默认 deny-all（不默认放行全部授权行，Req 18.2）
        return "TRUE" if self.placeholder_mode == "allow_dev" else "FALSE"


def _uid_literal(user_id: str) -> exp.Expression:
    """把 user_id 渲染为类型化 sqlglot 字面量（防注入；数值→number，否则→string）。"""
    s = str(user_id)
    if s.lstrip("-").isdigit():
        return exp.Literal.number(s)
    return exp.Literal.string(s)


class ScopeResolver:
    """解析可信身份 → DataScope（可见 ps_id 源 + 可见 org_id 源）。

    本期：产出 SubqueryScopeSource（做法二），**不连库、不执行**，仅构造子查询 AST。
    权限表 join 的真实计算，由未来执行这条输出 SQL 的 StarRocks 兑现。
    """

    def __init__(self, filter_spec: Optional[PermissionFilterSpec] = None,
                 dialect: str = DIALECT) -> None:
        self._spec = filter_spec or PermissionFilterSpec()
        self._dialect = dialect

    def resolve(self, identity: CallerIdentity) -> DataScope:
        uid = _uid_literal(identity.user_id).sql(dialect=self._dialect)
        ps_sub = sqlglot.parse_one(self._ps_subquery_sql(uid), dialect=self._dialect)
        org_sub = sqlglot.parse_one(self._org_subquery_sql(uid), dialect=self._dialect)
        return DataScope(
            ps_source=SubqueryScopeSource(dimension=ScopeDim.PS, subquery=ps_sub),
            org_source=SubqueryScopeSource(dimension=ScopeDim.ORG, subquery=org_sub),
        )

    def _ps_subquery_sql(self, uid_sql: str) -> str:
        """可见 ps_id 集合：直接分享 ∪ 组织下辖电站（闭包展开）∪ 公开分享。"""
        share = self._spec.predicate_for("share")
        org = self._spec.predicate_for("org")
        public = self._spec.predicate_for("public")
        return (
            f"SELECT ps_id FROM sys_user_ps_share "
            f"WHERE user_id = {uid_sql} AND ({share}) "
            f"UNION "
            f"SELECT pso.ps_id FROM sys_user_org uo "
            f"JOIN sys_org_all_sub_org sub ON sub.org_id = uo.org_id "
            f"JOIN power_station_org pso ON pso.org_id = sub.sub_org_id "
            f"WHERE uo.user_id = {uid_sql} AND ({org}) "
            f"UNION "
            f"SELECT ps_id FROM power_station_public_share WHERE ({public})"
        )

    def _org_subquery_sql(self, uid_sql: str) -> str:
        """可见 org_id 集合：用户所属组织经闭包表展开。"""
        org = self._spec.predicate_for("org")
        return (
            f"SELECT sub.sub_org_id AS org_id FROM sys_user_org uo "
            f"JOIN sys_org_all_sub_org sub ON sub.org_id = uo.org_id "
            f"WHERE uo.user_id = {uid_sql} AND ({org})"
        )
