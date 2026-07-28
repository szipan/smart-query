"""Layer 3 数据模型（design.md §4）。

层边界：QueryContext / QueryRequest 绝不含 org_ids/ps_ids（数据范围解析属 Layer 5）。
本期：Answer 携带 safe_sql（无 rows），AnswerStatus.OK 语义为"L5 ALLOWED、已产出 safe_sql"。
"""

from __future__ import annotations

import json
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


@dataclass(frozen=True)
class SemanticSlots:
    """结构化语义槽位指纹（归一后），用于缓存命中的精确门禁（design.md §5.4；Req 17）。

    仅承载"区分是否同一问题"的关键槽位；不含 org/ps（层边界）。
    """

    comparison: Optional[str] = None        # GT/GE/LT/LE/EQ
    threshold_watts: Optional[int] = None   # 阈值归一为瓦的整数（功率类指标）
    metric: Optional[str] = None            # 标准指标词
    group_by: Optional[str] = None          # 聚合维度：电站/机型/单台/None
    agg_func: Optional[str] = None          # SUM/AVG/MAX/MIN/COUNT/NONE
    sort_direction: Optional[str] = None    # DESC/ASC
    sort_field: Optional[str] = None        # 标准排序字段
    output_entity: Optional[str] = None     # 输出主体：电站/机型
    product_line: tuple[str, ...] = ()      # 产品线取值集合（OR 用多元素）
    top_n: Optional[int] = None
    extra_filters: tuple[str, ...] = ()     # 归一后的附加过滤签名

    def to_json(self) -> str:
        return json.dumps({
            "comparison": self.comparison,
            "threshold_watts": self.threshold_watts,
            "metric": self.metric,
            "group_by": self.group_by,
            "agg_func": self.agg_func,
            "sort_direction": self.sort_direction,
            "sort_field": self.sort_field,
            "output_entity": self.output_entity,
            "product_line": sorted(self.product_line),
            "top_n": self.top_n,
            "extra_filters": sorted(self.extra_filters),
        }, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def from_json(s: Optional[str]) -> Optional["SemanticSlots"]:
        if not s:
            return None
        try:
            d = json.loads(s) if isinstance(s, str) else s
            if not isinstance(d, dict):
                return None
            return SemanticSlots(
                comparison=d.get("comparison"),
                threshold_watts=d.get("threshold_watts"),
                metric=d.get("metric"),
                group_by=d.get("group_by"),
                agg_func=d.get("agg_func"),
                sort_direction=d.get("sort_direction"),
                sort_field=d.get("sort_field"),
                output_entity=d.get("output_entity"),
                product_line=tuple(d.get("product_line") or ()),
                top_n=d.get("top_n"),
                extra_filters=tuple(d.get("extra_filters") or ()),
            )
        except Exception:
            return None


def slots_equal(a: Optional[SemanticSlots], b: Optional[SemanticSlots]) -> bool:
    """关键槽位全等（product_line/extra_filters 按集合比较）。任一侧为空 → 不等（fail-safe）。"""
    if a is None or b is None:
        return False
    return (
        a.comparison == b.comparison
        and a.threshold_watts == b.threshold_watts
        and a.metric == b.metric
        and a.group_by == b.group_by
        and a.agg_func == b.agg_func
        and a.sort_direction == b.sort_direction
        and a.sort_field == b.sort_field
        and a.output_entity == b.output_entity
        and set(a.product_line) == set(b.product_line)
        and a.top_n == b.top_n
        and set(a.extra_filters) == set(b.extra_filters)
    )


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
    canonical_question: Optional[str] = None   # by CanonicalRewriter（缓存键 + 传 L4）
    semantic_slots: Optional[SemanticSlots] = None   # by CanonicalRewriter（缓存精确门禁）
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
