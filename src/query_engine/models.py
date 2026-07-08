"""Layer 3 数据模型（design.md §4）。

层边界：QueryContext / QueryRequest 绝不含 org_ids/ps_ids（数据范围解析属 Layer 5）。
本期：Answer 携带 safe_sql（无 rows），AnswerStatus.OK 语义为"L5 ALLOWED、已产出 safe_sql"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


@dataclass(frozen=True)
class CallerIdentity:
    """可信调用方身份（由 Layer 2 认证透传）。原样透传给 Layer 5，本层不解析范围。"""

    user_id: str
    tenant_id: Optional[str] = None
    claims: dict = field(default_factory=dict)


@dataclass(frozen=True)
class QueryRequest:
    """Layer 3 入口输入。不含 org_ids/ps_ids。"""

    request_id: str
    identity: CallerIdentity
    raw_question: str
    session_id: Optional[str] = None
    datasource_id: str = "starrocks"
    model_id: Optional[str] = None
    locale: str = "zh-CN"


@dataclass(frozen=True)
class ResolvedEntity:
    surface: str
    canonical: str
    entity_type: str
    entity_id: Optional[str] = None


@dataclass(frozen=True)
class ResolvedTerm:
    term: str
    definition: str
    synonyms: tuple[str, ...] = ()


class QuerySource(str, Enum):
    CACHE_HIT = "cache_hit"
    PIPELINE = "pipeline"


@dataclass(frozen=True)
class CacheLookup:
    hit: bool
    pre_isolation_sql: Optional[str] = None
    similarity: Optional[float] = None
    entry_id: Optional[str] = None


@dataclass
class NodeTrace:
    node: str
    started_at: float
    ended_at: float
    ok: bool
    error: Optional[str] = None
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryContext:
    """贯穿全流程的请求级共享状态；追加式写入，request 不可变；无 org_ids/ps_ids。"""

    request: QueryRequest
    standalone_question: Optional[str] = None
    resolved_entities: list[ResolvedEntity] = field(default_factory=list)
    resolved_terms: list[ResolvedTerm] = field(default_factory=list)
    cache_lookup: Optional[CacheLookup] = None
    pre_isolation_sql: Optional[str] = None
    sql_source: Optional[QuerySource] = None
    guard_result: Any = None                 # Layer 5 GuardResult（鸭子类型，不 import L5）
    safe_sql: Optional[str] = None           # 本期产出（无 rows）
    memory_degraded: bool = False
    traces: list[NodeTrace] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class AnswerStatus(str, Enum):
    OK = "ok"                     # 本期：L5 ALLOWED、已产出 safe_sql（无实际执行）
    NO_SQL = "no_sql"             # Layer 4 无法生成 SQL
    REJECTED = "rejected"         # Layer 5 拒绝
    UPSTREAM_ERROR = "error"      # Layer 4 / 执行层错误


@dataclass(frozen=True)
class Answer:
    """Layer 3 最终产出信封。本期 rows 恒为 None，safe_sql 承载已隔离校验的 SQL。"""

    request_id: str
    status: AnswerStatus
    safe_sql: Optional[str] = None
    rows: Any = None                          # 本期不执行，恒 None
    natural_language: Optional[str] = None
    sql_source: Optional[QuerySource] = None
    message: Optional[str] = None
    request_traces: tuple[NodeTrace, ...] = ()
