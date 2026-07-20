"""管理面业务服务（design.md §5, §6）。

- GlossaryService（软通道）：CRUD + 启用/停用 + 分页搜索；写入与向量库同步保持强一致
  （同步失败回滚权威存储，Req 4.4）。
- EntityService（硬通道）：见任务 6（本文件后续追加）。
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Optional

from knowledge_admin.deps import AdminObserver, EntityStore, GlossarySync, TermStore
from knowledge_admin.errors import NotFoundError
from knowledge_admin.models import EntityEntry, GlossaryTerm, normalize


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _page(items: list, current_page: int, page_size: int) -> dict:
    current_page = max(1, int(current_page))
    page_size = max(1, int(page_size))
    total = len(items)
    total_pages = (total + page_size - 1) // page_size
    start = (current_page - 1) * page_size
    return {
        "current_page": current_page,
        "page_size": page_size,
        "total_count": total,
        "total_pages": total_pages,
        "data": items[start:start + page_size],
    }


class GlossaryService:
    def __init__(self, store: TermStore, sync: GlossarySync,
                 observer: Optional[AdminObserver] = None) -> None:
        self._store = store
        self._sync = sync
        self._obs = observer

    # ---- 写 ----
    def create_or_update(self, term: GlossaryTerm, actor: str = "system") -> GlossaryTerm:
        return self.update(term, actor) if term.term_id else self.create(term, actor)

    def create(self, term: GlossaryTerm, actor: str = "system") -> GlossaryTerm:
        term.validate()
        term.created_at = term.updated_at = _now()
        created = self._store.create(term)   # 可能抛 DuplicateKeyError
        try:
            if created.enabled:
                self._sync.sync_upsert(created)
        except Exception:
            self._store.delete(created.term_id)   # 回滚权威（Req 4.4）
            raise
        self._record("create", created, actor)
        return created

    def update(self, term: GlossaryTerm, actor: str = "system") -> GlossaryTerm:
        term.validate()
        if not term.term_id:
            raise NotFoundError("更新缺少 term_id")
        old = self._store.get(term.term_id)
        if old is None:
            raise NotFoundError(f"term 不存在: {term.term_id}")
        old_snapshot = copy.deepcopy(old)
        term.created_at = old.created_at
        term.updated_at = _now()
        self._store.update(term)   # 可能抛 DuplicateKeyError
        try:
            if term.enabled:
                self._sync.sync_upsert(term)
            else:
                self._sync.sync_delete(term.term_id)
        except Exception:
            self._store.update(old_snapshot)   # 回滚权威
            raise
        self._record("update", term, actor)
        return term

    def delete(self, term_ids: list[str], actor: str = "system") -> int:
        deleted = 0
        for tid in term_ids:
            existing = self._store.get(tid)
            if existing is None:
                continue
            self._sync.sync_delete(tid)     # 先删索引；失败则中止（权威未动，保持一致）
            self._store.delete(tid)
            self._record("delete", existing, actor)
            deleted += 1
        return deleted

    def set_enabled(self, term_id: str, enabled: bool, actor: str = "system") -> GlossaryTerm:
        term = self._store.get(term_id)
        if term is None:
            raise NotFoundError(f"term 不存在: {term_id}")
        term.enabled = enabled
        term.updated_at = _now()
        self._store.update(term)
        if enabled:
            self._sync.sync_upsert(term)
        else:
            self._sync.sync_delete(term_id)
        self._record("enable" if enabled else "disable", term, actor)
        return term

    # ---- 读 ----
    def list_all(self) -> list[GlossaryTerm]:
        return self._store.scan()

    def search(self, word: Optional[str] = None, current_page: int = 1,
               page_size: int = 20) -> dict:
        items = self._store.scan()
        if word:
            key = normalize(word)
            items = [t for t in items
                     if key in normalize(t.term)
                     or any(key in normalize(s) for s in t.synonyms)]
        items.sort(key=lambda t: t.term)
        return _page(items, current_page, page_size)

    def _record(self, op: str, term: GlossaryTerm, actor: str) -> None:
        if self._obs is not None:
            self._obs.record(operation=f"glossary.{op}", business_key=term.business_key, actor=actor)


class EntityService:
    """硬通道服务。唯一性（业务键 + 别名全局唯一）由 EntityStore 原子保证；
    本期不向运行时匹配器投影（匹配器从 EntityStore 拉取，Req 5.10）。"""

    def __init__(self, store: EntityStore, observer: Optional[AdminObserver] = None) -> None:
        self._store = store
        self._obs = observer

    def create_or_update(self, entry: EntityEntry, actor: str = "system") -> EntityEntry:
        return self.update(entry, actor) if entry.entry_id else self.create(entry, actor)

    def create(self, entry: EntityEntry, actor: str = "system") -> EntityEntry:
        entry.validate()
        entry.created_at = entry.updated_at = _now()
        created = self._store.create(entry)   # 可能抛 DuplicateKeyError / AliasConflictError
        self._record("create", created, actor)
        return created

    def update(self, entry: EntityEntry, actor: str = "system") -> EntityEntry:
        entry.validate()
        if not entry.entry_id:
            raise NotFoundError("更新缺少 entry_id")
        old = self._store.get(entry.entry_id)
        if old is None:
            raise NotFoundError(f"实体不存在: {entry.entry_id}")
        entry.created_at = old.created_at
        entry.updated_at = _now()
        updated = self._store.update(entry)
        self._record("update", updated, actor)
        return updated

    def delete(self, entry_ids: list[str], actor: str = "system") -> int:
        deleted = 0
        for eid in entry_ids:
            existing = self._store.delete(eid)
            if existing is not None:
                self._record("delete", existing, actor)
                deleted += 1
        return deleted

    def set_enabled(self, entry_id: str, enabled: bool, actor: str = "system") -> EntityEntry:
        entry = self._store.get(entry_id)
        if entry is None:
            raise NotFoundError(f"实体不存在: {entry_id}")
        entry.enabled = enabled
        entry.updated_at = _now()
        updated = self._store.update(entry)
        self._record("enable" if enabled else "disable", updated, actor)
        return updated

    def list_all(self) -> list[EntityEntry]:
        return self._store.scan()

    def search(self, keyword: Optional[str] = None, entity_type: Optional[str] = None,
               current_page: int = 1, page_size: int = 20) -> dict:
        items = self._store.scan()
        if entity_type:
            items = [e for e in items if e.entity_type == entity_type]
        if keyword:
            key = normalize(keyword)
            items = [e for e in items
                     if key in normalize(e.canonical)
                     or any(key in normalize(a) for a in e.aliases)]
        items.sort(key=lambda e: (e.entity_type, e.canonical))
        return _page(items, current_page, page_size)

    def _record(self, op: str, entry: EntityEntry, actor: str) -> None:
        if self._obs is not None:
            self._obs.record(operation=f"entity.{op}", business_key=entry.business_key, actor=actor)
