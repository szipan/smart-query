"""knowledge-admin 测试用内存 fake（不连真实服务）。"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from knowledge_admin.errors import AliasConflictError, DuplicateKeyError, NotFoundError
from knowledge_admin.models import EntityEntry, GlossaryTerm, normalize


class FakeTermStore:
    """内存软通道存储，保证 term 归一化唯一。"""

    def __init__(self) -> None:
        self._by_id: dict[str, GlossaryTerm] = {}
        self._key_to_id: dict[str, str] = {}

    def create(self, term: GlossaryTerm) -> GlossaryTerm:
        key = term.business_key
        if key in self._key_to_id:
            raise DuplicateKeyError(f"term 已存在: {term.term}")
        term.term_id = term.term_id or uuid.uuid4().hex
        self._by_id[term.term_id] = term
        self._key_to_id[key] = term.term_id
        return term

    def update(self, term: GlossaryTerm) -> GlossaryTerm:
        if not term.term_id or term.term_id not in self._by_id:
            raise NotFoundError(f"term 不存在: {term.term_id}")
        key = term.business_key
        owner = self._key_to_id.get(key)
        if owner is not None and owner != term.term_id:
            raise DuplicateKeyError(f"term 已存在: {term.term}")
        old = self._by_id[term.term_id]
        self._key_to_id.pop(old.business_key, None)
        self._by_id[term.term_id] = term
        self._key_to_id[key] = term.term_id
        return term

    def delete(self, term_id: str) -> Optional[GlossaryTerm]:
        t = self._by_id.pop(term_id, None)
        if t is not None:
            self._key_to_id.pop(t.business_key, None)
        return t

    def get(self, term_id: str) -> Optional[GlossaryTerm]:
        return self._by_id.get(term_id)

    def scan(self) -> list[GlossaryTerm]:
        return list(self._by_id.values())


class FakeEntityStore:
    """内存硬通道存储：business key 唯一 + 别名跨实体全局唯一。"""

    def __init__(self) -> None:
        self._by_id: dict[str, EntityEntry] = {}
        self._key_to_id: dict[str, str] = {}       # business_key -> entry_id
        self._alias_owner: dict[str, str] = {}      # alias_normalized -> entry_id

    def _check_aliases(self, entry: EntityEntry, owner_id: Optional[str]) -> None:
        for a_key, a in zip(entry.alias_keys(), entry.aliases):
            existing = self._alias_owner.get(a_key)
            if existing is not None and existing != owner_id:
                other = self._by_id.get(existing)
                raise AliasConflictError(
                    f"别名 '{a}' 已属于其他实体",
                    detail={"alias": a, "owner": other.business_key if other else existing})

    def _reindex_aliases(self, entry: EntityEntry) -> None:
        # 移除该实体旧别名，再写入新别名
        self._alias_owner = {k: v for k, v in self._alias_owner.items() if v != entry.entry_id}
        for a_key in entry.alias_keys():
            self._alias_owner[a_key] = entry.entry_id

    def create(self, entry: EntityEntry) -> EntityEntry:
        if entry.business_key in self._key_to_id:
            raise DuplicateKeyError(f"实体已存在: {entry.business_key}")
        entry.entry_id = entry.entry_id or uuid.uuid4().hex
        self._check_aliases(entry, owner_id=entry.entry_id)
        self._by_id[entry.entry_id] = entry
        self._key_to_id[entry.business_key] = entry.entry_id
        self._reindex_aliases(entry)
        return entry

    def update(self, entry: EntityEntry) -> EntityEntry:
        if not entry.entry_id or entry.entry_id not in self._by_id:
            raise NotFoundError(f"实体不存在: {entry.entry_id}")
        owner = self._key_to_id.get(entry.business_key)
        if owner is not None and owner != entry.entry_id:
            raise DuplicateKeyError(f"实体已存在: {entry.business_key}")
        self._check_aliases(entry, owner_id=entry.entry_id)
        old = self._by_id[entry.entry_id]
        self._key_to_id.pop(old.business_key, None)
        self._by_id[entry.entry_id] = entry
        self._key_to_id[entry.business_key] = entry.entry_id
        self._reindex_aliases(entry)
        return entry

    def delete(self, entry_id: str) -> Optional[EntityEntry]:
        e = self._by_id.pop(entry_id, None)
        if e is not None:
            self._key_to_id.pop(e.business_key, None)
            self._alias_owner = {k: v for k, v in self._alias_owner.items() if v != entry_id}
        return e

    def get(self, entry_id: str) -> Optional[EntityEntry]:
        return self._by_id.get(entry_id)

    def scan(self) -> list[EntityEntry]:
        return list(self._by_id.values())

    def scan_aliases(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for e in self._by_id.values():
            if not e.enabled:
                continue
            for a_key, a in zip(e.alias_keys(), e.aliases):
                rows.append({"alias_normalized": a_key, "alias_original": a,
                             "canonical": e.canonical, "entity_type": e.entity_type,
                             "entity_id": e.entity_id})
        return rows


class FakeEmbedder:
    def __init__(self, dim: int = 8) -> None:
        self.dim = dim
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(len(t) % 7)] * self.dim for t in texts]


class FakeVectorStore:
    def __init__(self, fail_upsert: bool = False, fail_delete: bool = False) -> None:
        self.docs: dict[tuple[str, str], dict] = {}
        self.fail_upsert = fail_upsert
        self.fail_delete = fail_delete

    def upsert(self, namespace: str, entry: dict[str, Any]) -> None:
        if self.fail_upsert:
            raise RuntimeError("opensearch upsert down")
        self.docs[(namespace, entry.get("entry_id"))] = entry

    def delete(self, namespace: str, entry_id: str) -> None:
        if self.fail_delete:
            raise RuntimeError("opensearch delete down")
        self.docs.pop((namespace, entry_id), None)


class FakeObserver:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(self, *, operation: str, business_key: str, actor: str,
               extra: Optional[dict] = None) -> None:
        self.records.append({"operation": operation, "business_key": business_key,
                             "actor": actor, "extra": extra or {}})
