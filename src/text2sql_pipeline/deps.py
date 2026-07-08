"""能力抽象接口与阶段协议（design.md §5.2 / §7.1）。

外部能力以 Protocol 定义、依赖注入给各阶段，从而可 mock、可替换。
本期 `DatasourceAdapter.dry_run` 保留接口但不使用（不执行 SQL）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol, runtime_checkable

from text2sql_pipeline.models import PipelineContext


# ---------- 外部能力抽象（复用系统既有接口） ----------
class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class VectorStore(Protocol):
    def similarity_search(self, vector: list[float], top_k: int,
                          namespace: str) -> list[dict[str, Any]]: ...


class LlmClient(Protocol):
    def complete(self, *, model_id: str, system: str, prompt: str,
                 max_tokens: int = 2048, temperature: float = 0.0) -> str: ...


class DatasourceAdapter(Protocol):
    dialect: str

    def dry_run(self, sql: str) -> None:
        """只读探测执行。**本期不使用**（不执行 SQL），保留接口待未来接入 L6/7。"""
        ...

    def get_schema_snippet(self, table_names: list[str]) -> list[str]: ...


class SemanticLayerStore(Protocol):
    def fetch_fragments(self, fragment_ids: list[str]) -> list[dict[str, Any]]: ...


class IsolationValidator(Protocol):
    """Layer 5 暴露给 Query Fixer 的最小校验入口（隔离注入 + 只读校验）。"""

    def validate_for_probe(self, sql: str, request_id: str) -> str: ...


# ---------- 注入给各阶段的依赖集合 ----------
@dataclass
class StageDependencies:
    embedder: Optional[Embedder] = None
    vector_store: Optional[VectorStore] = None
    llm_client: Optional[LlmClient] = None
    datasource: Optional[DatasourceAdapter] = None
    semantic_layer: Optional[SemanticLayerStore] = None
    # Layer 5 校验入口（仅供 Query Fixer 探测/静态校验使用）
    validator: Optional[IsolationValidator] = None


# ---------- 阶段统一协议 ----------
@runtime_checkable
class PipelineStage(Protocol):
    """所有阶段的统一契约：读取 ctx 所需输入，追加写入本阶段产出，返回 ctx。"""

    stage_type: str
    strategy_name: str

    def run(self, ctx: PipelineContext) -> PipelineContext: ...
