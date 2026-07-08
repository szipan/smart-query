"""Layer 4 数据模型（design.md §4）。

- 输入 PipelineInput：已由 Layer 3 改写为独立问题，**不含用户数据范围**。
- 流转 PipelineContext：追加式共享状态，各阶段只写自己负责的字段。
- 输出 PipelineResult：未校验 SQL + 元数据，移交 Layer 5。
- 配置 PipelineConfig：决定每个阶段选用哪个策略实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from text2sql_pipeline.config import DEFAULT_MODEL_ID


# ---------- 输入 ----------
@dataclass(frozen=True)
class ResolvedEntity:
    """Layer 3 硬归一化产出的实体（电站名/指标名 → 标准词/id）。"""

    surface: str
    canonical: str
    entity_type: str
    entity_id: Optional[str] = None


@dataclass(frozen=True)
class ResolvedTerm:
    """Layer 3 软归一化产出的术语（同义词 + 定义）。"""

    term: str
    definition: str
    synonyms: tuple[str, ...] = ()


@dataclass(frozen=True)
class PipelineInput:
    """Layer 4 入口输入 —— 已是独立、自包含问题。不携带 org_ids/ps_ids。"""

    request_id: str
    standalone_question: str
    resolved_entities: tuple[ResolvedEntity, ...] = ()
    resolved_terms: tuple[ResolvedTerm, ...] = ()
    datasource_id: str = "starrocks"
    model_id: Optional[str] = None
    locale: str = "zh-CN"


# ---------- 流转状态 ----------
@dataclass
class RetrievedContext:
    """上下文获取阶段的产出。"""

    schema_snippets: list[str] = field(default_factory=list)
    few_shot_examples: list[dict[str, str]] = field(default_factory=list)
    term_glossary: list[str] = field(default_factory=list)
    semantic_fragments: list[dict[str, Any]] = field(default_factory=list)
    strategy_name: str = ""
    debug: dict[str, Any] = field(default_factory=dict)


@dataclass
class SqlCandidate:
    sql: str
    generator_name: str
    model_id: str
    score: Optional[float] = None
    rationale: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StageTrace:
    """单阶段可观测记录。"""

    stage_type: str
    strategy_name: str
    started_at: float
    ended_at: float
    ok: bool
    error: Optional[str] = None
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineContext:
    """贯穿全流程的共享状态；各阶段追加写入，禁止跨阶段引用内部实现。"""

    input: PipelineInput
    retrieved_context: Optional[RetrievedContext] = None       # by ContextAcquisitionStage
    sql_candidates: list[SqlCandidate] = field(default_factory=list)  # by SqlGenerationStage
    selected_sql: Optional[SqlCandidate] = None                # by SqlSelectionStage
    final_sql: Optional[SqlCandidate] = None                   # by QueryFixerStage
    fix_attempts: int = 0
    traces: list[StageTrace] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------- 输出 ----------
class PipelineStatus(str, Enum):
    OK = "ok"
    NO_SQL = "no_sql"
    FIXER_EXHAUSTED = "fixer_exhausted"
    ERROR = "error"


@dataclass(frozen=True)
class PipelineResult:
    """Layer 4 最终产出 —— 未校验 SQL + 元数据，移交 Layer 5。"""

    request_id: str
    status: PipelineStatus
    unvalidated_sql: Optional[str]
    datasource_id: str
    candidates_considered: int
    fix_attempts: int
    stage_strategies: dict[str, str]
    traces: list[StageTrace]
    warnings: list[str]
    error: Optional[str] = None


# ---------- 配置 ----------
@dataclass(frozen=True)
class StageConfig:
    strategy: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineConfig:
    """决定每个阶段选用哪个实现 —— 配置驱动的核心。"""

    context_acquisition: StageConfig = StageConfig(strategy="rag")
    sql_generation: StageConfig = StageConfig(strategy="single_llm")
    sql_selection: StageConfig = StageConfig(strategy="passthrough")
    query_fixer: StageConfig = StageConfig(strategy="bounded_retry", params={"max_attempts": 2})
    default_model_id: str = DEFAULT_MODEL_ID
