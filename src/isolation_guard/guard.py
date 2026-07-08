"""Layer 5 门面 IsolationGuard（design.md §7，Requirements 7/9/12）。

编排三步（每请求绑定身份，DataScope 惰性解析并缓存）：
  ① 权限解析（ScopeResolver）→ ③ 只读校验 → ② 隔离注入 → 注入后复核 → safe_sql
双入口共享内核 _apply，保证不可绕过：
  - guard(sql, request_id) -> GuardResult：捕获异常转 REJECTED（fail-closed）
  - validate_for_probe(sql, request_id) -> str：复用内核，异常向上抛（供 Layer 4）

本期：不执行 SQL、零 DB 连接（scope 为子查询源）；deny_all 在子查询模式下无法静态判定，置 False。
"""

from __future__ import annotations

import time
from typing import Optional

from isolation_guard.config import DIALECT
from isolation_guard.errors import IsolationGuardError, PermissionResolutionError
from isolation_guard.injector import SqlglotIsolationInjector
from isolation_guard.models import CallerIdentity, GuardResult, GuardStatus, IsolationDecision
from isolation_guard.observer import IsolationObserver, NullObserver
from isolation_guard.policy import TablePolicy
from isolation_guard.readonly import SqlglotReadOnlyValidator
from isolation_guard.scope import DataScope, ScopeResolver


class IsolationGuard:
    """每请求构造，绑定 CallerIdentity；DataScope 惰性解析并缓存。"""

    def __init__(
        self,
        *,
        identity: CallerIdentity,
        resolver: ScopeResolver,
        injector: SqlglotIsolationInjector,
        validator: SqlglotReadOnlyValidator,
        policy: TablePolicy,
        dialect: str = DIALECT,
        observer: Optional[IsolationObserver] = None,
    ) -> None:
        self._identity = identity
        self._resolver = resolver
        self._injector = injector
        self._validator = validator
        self._policy = policy
        self._dialect = dialect
        self._obs = observer or NullObserver()
        self._scope_cache: Optional[DataScope] = None

    # —— 内核：三步全部在此，双入口共享，保证不可绕过 ——
    def _apply(self, sql: str, request_id: str) -> tuple[str, dict]:
        scope = self._resolve_scope()

        ast = self._validator.parse_single(sql, self._dialect)
        self._validator.assert_read_only(ast)

        injected_ast, report = self._injector.inject(ast, scope, self._policy, self._dialect)

        # 纵深复核：注入后仍须是只读查询
        self._validator.assert_read_only(injected_ast)

        safe_sql = injected_ast.sql(dialect=self._dialect)
        meta = {
            "injected_tables": tuple(report.injected_tables),
            "passthrough_tables": tuple(report.passthrough_tables),
            # 子查询模式下空可见集在执行期天然 deny-all，无法静态判定 → False
            "deny_all": False,
        }
        return safe_sql, meta

    def _resolve_scope(self) -> DataScope:
        if self._scope_cache is None:
            try:
                self._scope_cache = self._resolver.resolve(self._identity)
            except IsolationGuardError:
                raise
            except Exception as e:  # 外部异常 → fail-closed
                raise PermissionResolutionError(f"身份解析失败: {e!r}") from e
        return self._scope_cache

    # —— 入口 A：主守卫路径 ——
    def guard(self, sql: str, request_id: str) -> GuardResult:
        started = time.monotonic()
        try:
            safe_sql, meta = self._apply(sql, request_id)
            result = GuardResult(
                request_id=request_id,
                status=GuardStatus.ALLOWED,
                safe_sql=safe_sql,
                original_sql=sql,
                dialect=self._dialect,
                injected_tables=meta["injected_tables"],
                passthrough_tables=meta["passthrough_tables"],
                deny_all=meta["deny_all"],
                elapsed_ms=(time.monotonic() - started) * 1000,
            )
        except IsolationGuardError as e:
            result = GuardResult(
                request_id=request_id,
                status=GuardStatus.REJECTED,
                safe_sql=None,
                original_sql=sql,
                dialect=self._dialect,
                rejection_reason=str(e),
                rejection_kind=type(e).__name__,
                elapsed_ms=(time.monotonic() - started) * 1000,
            )
        except Exception as e:  # 未预期异常 → fail-closed 拒绝
            result = GuardResult(
                request_id=request_id,
                status=GuardStatus.REJECTED,
                safe_sql=None,
                original_sql=sql,
                dialect=self._dialect,
                rejection_reason=f"未预期错误: {e!r}",
                rejection_kind="UnexpectedError",
                elapsed_ms=(time.monotonic() - started) * 1000,
            )
        self._obs.on_decision(self._to_decision(result))
        return result

    # —— 入口 B：Layer 4 Query Fixer 探测（异常向上抛）——
    def validate_for_probe(self, sql: str, request_id: str) -> str:
        safe_sql, meta = self._apply(sql, request_id)  # 不合规抛异常，交由 Layer 4
        self._obs.on_decision(
            IsolationDecision(
                request_id=request_id,
                user_id=self._identity.user_id,
                status=GuardStatus.ALLOWED,
                tables_seen=meta["passthrough_tables"] + meta["injected_tables"],
                tables_injected=meta["injected_tables"],
                deny_all=meta["deny_all"],
                dialect=self._dialect,
                rejection_kind=None,
                timestamp=time.time(),
            )
        )
        return safe_sql

    def _to_decision(self, r: GuardResult) -> IsolationDecision:
        return IsolationDecision(
            request_id=r.request_id,
            user_id=self._identity.user_id,
            status=r.status,
            tables_seen=r.passthrough_tables + r.injected_tables,
            tables_injected=r.injected_tables,
            deny_all=r.deny_all,
            dialect=r.dialect,
            rejection_kind=r.rejection_kind,
            timestamp=time.time(),
        )


class IsolationGuardFactory:
    """按当前请求身份构造 IsolationGuard 实例（供 Layer 3 的 for_identity 契约）。

    共享的无状态依赖（resolver/injector/validator/policy/observer/dialect）在工厂持有，
    每请求 for_identity(identity) 产出一个绑定身份的 guard。
    """

    def __init__(
        self,
        *,
        policy: TablePolicy,
        resolver: Optional[ScopeResolver] = None,
        injector: Optional[SqlglotIsolationInjector] = None,
        validator: Optional[SqlglotReadOnlyValidator] = None,
        dialect: str = DIALECT,
        observer: Optional[IsolationObserver] = None,
    ) -> None:
        self._policy = policy
        self._resolver = resolver or ScopeResolver(dialect=dialect)
        self._injector = injector or SqlglotIsolationInjector()
        self._validator = validator or SqlglotReadOnlyValidator()
        self._dialect = dialect
        self._observer = observer

    def for_identity(self, identity: CallerIdentity) -> IsolationGuard:
        return IsolationGuard(
            identity=identity,
            resolver=self._resolver,
            injector=self._injector,
            validator=self._validator,
            policy=self._policy,
            dialect=self._dialect,
            observer=self._observer,
        )
