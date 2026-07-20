"""knowledge-admin 能力抽象（Protocol）。全部依赖注入，单测用内存 fake 替换。

- TermStore / EntityStore：权威存储（DynamoDB），各自封装业务键与（硬通道）别名唯一性。
- GlossarySync：软通道写入向量库（embed + upsert/delete）。
- Embedder / VectorStore：复用 adapters 的能力形态。
- AuthGuard：管理员鉴权（占位）。AdminObserver：审计。
"""

from __future__ import annotations

from typing import Any, Optional, Protocol

from knowledge_admin.models import EntityEntry, GlossaryTerm


class TermStore(Protocol):
    """软通道权威存储。create/update 保证 term（归一化）唯一。"""

    def create(self, term: GlossaryTerm) -> GlossaryTerm: ...
    def update(self, term: GlossaryTerm) -> GlossaryTerm: ...
    def delete(self, term_id: str) -> Optional[GlossaryTerm]: ...
    def get(self, term_id: str) -> Optional[GlossaryTerm]: ...
    def scan(self) -> list[GlossaryTerm]: ...


class EntityStore(Protocol):
    """硬通道权威存储。create/update 保证 (entity_type, entity_id) 唯一且别名跨实体全局唯一。"""

    def create(self, entry: EntityEntry) -> EntityEntry: ...
    def update(self, entry: EntityEntry) -> EntityEntry: ...
    def delete(self, entry_id: str) -> Optional[EntityEntry]: ...
    def get(self, entry_id: str) -> Optional[EntityEntry]: ...
    def scan(self) -> list[EntityEntry]: ...
    def scan_aliases(self) -> list[dict[str, Any]]:
        """返回扁平别名行 {alias_normalized, alias_original, canonical, entity_type, entity_id}，供匹配器加载。"""
        ...


class GlossarySync(Protocol):
    """软通道向量库同步。"""

    def sync_upsert(self, term: GlossaryTerm) -> None: ...
    def sync_delete(self, term_id: str) -> None: ...


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class VectorStore(Protocol):
    def upsert(self, namespace: str, entry: dict[str, Any]) -> None: ...
    def delete(self, namespace: str, entry_id: str) -> None: ...


class AdminPrincipal(Protocol):
    @property
    def actor_id(self) -> str: ...


class AuthGuard(Protocol):
    def require_admin(self, auth_header: Optional[str]) -> Any:
        """校验管理员身份，返回 principal；失败抛 Unauthorized/Forbidden。"""
        ...


class AdminObserver(Protocol):
    def record(self, *, operation: str, business_key: str, actor: str,
               extra: Optional[dict] = None) -> None: ...
