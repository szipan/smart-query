"""Layer 5 数据模型：身份、维度枚举、守卫结果与审计事件。

对应 design.md §4.1/§4.3 及 §5A（阳光云权限模型落地）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ScopeDim(str, Enum):
    """隔离维度：电站维度（对应可见 ps_id 集合）/ 组织维度（对应可见 org_id 集合）。"""

    PS = "ps"
    ORG = "org"


@dataclass(frozen=True)
class CallerIdentity:
    """可信调用方身份 —— 由 Layer 2 认证（阳光云 OAuth）透传而来。

    只承载"你是谁"，不含数据范围；范围解析（可见 ps_id/org_id）是 Layer 5 的职责。
    """

    user_id: str
    oauth_subject: Optional[str] = None
    tenant: Optional[str] = None
    # 原始声明只读留存（不含敏感明文凭证；凭证经 AgentCore Identity 托管，不落本模型）
    claims: dict = field(default_factory=dict)


class GuardStatus(str, Enum):
    ALLOWED = "allowed"      # 校验通过，safe_sql 可执行（本期：可返回，不实际执行）
    REJECTED = "rejected"    # 拒绝，rejection_reason 说明原因


@dataclass(frozen=True)
class GuardResult:
    """主守卫路径 guard(...) 的结构化结果（供下游 + 审计使用）。

    注意：scope 只落计数，不落具体 id（审计脱敏，Requirements 13.3）。
    """

    request_id: str
    status: GuardStatus
    safe_sql: Optional[str]
    original_sql: str
    dialect: str
    injected_tables: tuple[str, ...] = ()
    passthrough_tables: tuple[str, ...] = ()
    deny_all: bool = False
    rejection_reason: Optional[str] = None
    rejection_kind: Optional[str] = None
    elapsed_ms: float = 0.0


@dataclass(frozen=True)
class IsolationDecision:
    """审计事件：每次守卫决策留痕（安全红线可审计要求，Requirements 13）。

    仅记录 scope 计数，不记录任何具体 scope id 或敏感 SQL 明文。
    """

    request_id: str
    user_id: str
    status: GuardStatus
    tables_seen: tuple[str, ...]
    tables_injected: tuple[str, ...]
    deny_all: bool
    dialect: str
    rejection_kind: Optional[str]
    timestamp: float
