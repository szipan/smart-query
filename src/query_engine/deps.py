"""能力抽象与下游客户端契约（design.md §3 / §7.1）。

L3 只依赖这些 Protocol，不 import L4/L5 的具体类型；跨层对接由 wiring 层的适配器完成。
状态比较用字符串（L4 PipelineStatus / L5 GuardStatus 均为 str Enum，可直接与字符串比较）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from query_engine.models import CallerIdentity, ResolvedEntity, ResolvedTerm


# ---------- 基础能力 ----------
class MemoryClient(Protocol):
    def read_recent_turns(self, actor_id: str, session_id: Optional[str], limit: int) -> list[dict]: ...
    def write_turn(self, actor_id: str, session_id: Optional[str], question: str, answer_summary: str) -> None: ...


class LlmClient(Protocol):
    def complete(self, *, system: str, prompt: str, model_id: Optional[str] = None,
                 max_tokens: int = 1024, temperature: float = 0.0) -> str: ...


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class VectorStore(Protocol):
    def similarity_search(self, vector: list[float], top_k: int, namespace: str) -> list[dict[str, Any]]: ...
    def upsert(self, namespace: str, entry: dict[str, Any]) -> None: ...
    def delete(self, namespace: str, entry_id: str) -> None: ...


class EntityDictionary(Protocol):
    """硬归一化字典：对问题做确定性精确/别名匹配，返回命中的实体（不调 LLM）。"""

    def match(self, question: str) -> list[dict[str, Any]]: ...


class TermReplacer(Protocol):
    """规范化改写确定性替换源：读软术语库登记的 synonyms→标准词，
    以 Aho-Corasick 精确匹配把变体词替换为标准词（确定性、不调 LLM、无随机）。"""

    def replace(self, text: str) -> str: ...


# ---------- 下游层客户端 ----------
@dataclass(frozen=True)
class Layer4Request:
    request_id: str
    standalone_question: str
    resolved_entities: tuple[ResolvedEntity, ...] = ()
    resolved_terms: tuple[ResolvedTerm, ...] = ()
    datasource_id: str = "starrocks"
    model_id: Optional[str] = None


class Layer4Result(Protocol):
    """Layer 4 产出（鸭子类型；真实 PipelineResult 亦满足）。status 与 'ok'/'no_sql' 等可比。"""

    status: Any
    unvalidated_sql: Optional[str]


class Layer4Client(Protocol):
    def generate(self, request: Layer4Request) -> Layer4Result: ...


class Layer5Guard(Protocol):
    """Layer 5 门面（鸭子类型；真实 IsolationGuard 亦满足）。"""

    def guard(self, sql: str, request_id: str) -> Any: ...


class Layer5GuardFactory(Protocol):
    def for_identity(self, identity: CallerIdentity) -> Layer5Guard: ...


class DatasourceAdapter(Protocol):
    """Layer 6/7 执行适配。**本期不使用**（不执行 SQL），保留接口待未来接入。"""

    dialect: str

    def execute(self, safe_sql: str) -> Any: ...


# ---------- 依赖聚合 ----------
@dataclass
class EngineDependencies:
    memory: Optional[MemoryClient] = None
    llm: Optional[LlmClient] = None
    embedder: Optional[Embedder] = None
    vector_store: Optional[VectorStore] = None
    entity_dictionary: Optional[EntityDictionary] = None
    term_replacer: Optional[TermReplacer] = None   # 规范化改写确定性替换源（synonyms→标准词）
    canonical_model_id: Optional[str] = None       # 槽位抽取专用模型（独立于主生成模型；None=用默认）
    layer4: Optional[Layer4Client] = None
    guard_factory: Optional[Layer5GuardFactory] = None
    datasource: Optional[DatasourceAdapter] = None  # 本期可为 None（不执行）
