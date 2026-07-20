"""DynamoDB 权威存储（design.md §4）。

- DynamoDbTermStore：软通道。term 表（PK=term_id）+ 名占用表（PK=name_key）保证 term 唯一。
- DynamoDbEntityStore：硬通道。entity 表（PK=business_key）+ alias 表（PK=alias_key）
  用 TransactWriteItems 条件写，原子保证「业务键唯一」+「别名跨实体全局唯一」。

条件表达式为固定常量（供测试用 fake client 精确识别）。TransactionCanceledException
按取消原因映射为 DuplicateKeyError / AliasConflictError。
entry_id 维度的 get/delete 用 scan（§4.5 已认可的小规模取舍）。
"""

from __future__ import annotations

from typing import Any, Optional

from knowledge_admin.errors import AliasConflictError, DuplicateKeyError
from knowledge_admin.models import EntityEntry, GlossaryTerm, normalize

# —— 固定条件表达式（fake client 精确匹配用） ——
COND_PK_NOT_EXISTS = "attribute_not_exists(pk)"
COND_NAME_NOT_EXISTS = "attribute_not_exists(name_key)"
COND_ALIAS_FREE_OR_OWN = "attribute_not_exists(alias_key) OR owner_pk = :owner"


# —— 属性 (de)serialize ——
def _s(v: str) -> dict:
    return {"S": str(v)}


def _list_s(items: list[str]) -> dict:
    return {"L": [{"S": str(i)} for i in items]}


def _from_item(item: dict) -> dict:
    """DynamoDB 低层 item -> 普通 dict（仅处理本模块用到的 S/BOOL/L(S) 类型）。"""
    out: dict[str, Any] = {}
    for k, v in item.items():
        if "S" in v:
            out[k] = v["S"]
        elif "BOOL" in v:
            out[k] = v["BOOL"]
        elif "L" in v:
            out[k] = [x.get("S") for x in v["L"]]
    return out


def _term_to_item(t: GlossaryTerm) -> dict:
    return {
        "pk": _s(t.term_id), "term": _s(t.term), "name_key": _s(t.business_key),
        "synonyms": _list_s(t.synonyms), "definition": _s(t.definition),
        "enabled": {"BOOL": bool(t.enabled)},
        "created_at": _s(t.created_at or ""), "updated_at": _s(t.updated_at or ""),
    }


def _term_from_item(item: dict) -> GlossaryTerm:
    d = _from_item(item)
    return GlossaryTerm(term=d["term"], synonyms=list(d.get("synonyms") or []),
                        definition=d.get("definition", ""), enabled=bool(d.get("enabled", True)),
                        term_id=d["pk"], created_at=d.get("created_at") or None,
                        updated_at=d.get("updated_at") or None)


def _entity_to_item(e: EntityEntry) -> dict:
    return {
        "pk": _s(e.business_key), "entry_id": _s(e.entry_id),
        "canonical": _s(e.canonical), "entity_type": _s(e.entity_type),
        "entity_id": _s(e.entity_id), "aliases": _list_s(e.aliases),
        "enabled": {"BOOL": bool(e.enabled)},
        "created_at": _s(e.created_at or ""), "updated_at": _s(e.updated_at or ""),
    }


def _entity_from_item(item: dict) -> EntityEntry:
    d = _from_item(item)
    return EntityEntry(canonical=d["canonical"], entity_type=d["entity_type"],
                       entity_id=d["entity_id"], aliases=list(d.get("aliases") or []),
                       enabled=bool(d.get("enabled", True)), entry_id=d.get("entry_id"),
                       created_at=d.get("created_at") or None, updated_at=d.get("updated_at") or None)


def _is_txn_canceled(exc: Exception) -> Optional[list]:
    """若为 TransactionCanceledException，返回 CancellationReasons，否则 None。"""
    resp = getattr(exc, "response", None)
    if not isinstance(resp, dict):
        return None
    if resp.get("Error", {}).get("Code") == "TransactionCanceledException":
        return resp.get("CancellationReasons") or []
    return None


def _failed(reason: Optional[dict]) -> bool:
    return bool(reason) and reason.get("Code") not in (None, "None")


class DynamoDbTermStore:
    def __init__(self, client: Any, table: str, name_table: str) -> None:
        self._c = client
        self._t = table
        self._n = name_table

    def create(self, term: GlossaryTerm) -> GlossaryTerm:
        import uuid
        term.term_id = term.term_id or uuid.uuid4().hex
        items = [
            {"Put": {"TableName": self._t, "Item": _term_to_item(term),
                     "ConditionExpression": COND_PK_NOT_EXISTS}},
            {"Put": {"TableName": self._n,
                     "Item": {"name_key": _s(term.business_key), "owner": _s(term.term_id)},
                     "ConditionExpression": COND_NAME_NOT_EXISTS}},
        ]
        try:
            self._c.transact_write_items(TransactItems=items)
        except Exception as e:
            if _is_txn_canceled(e) is not None:
                raise DuplicateKeyError(f"term 已存在: {term.term}") from e
            raise
        return term

    def update(self, term: GlossaryTerm) -> GlossaryTerm:
        old = self.get(term.term_id)
        if old is None:
            from knowledge_admin.errors import NotFoundError
            raise NotFoundError(f"term 不存在: {term.term_id}")
        items: list[dict] = [{"Put": {"TableName": self._t, "Item": _term_to_item(term)}}]
        if old.business_key != term.business_key:
            items.append({"Delete": {"TableName": self._n,
                                     "Key": {"name_key": _s(old.business_key)}}})
            items.append({"Put": {"TableName": self._n,
                                  "Item": {"name_key": _s(term.business_key), "owner": _s(term.term_id)},
                                  "ConditionExpression": COND_NAME_NOT_EXISTS}})
        try:
            self._c.transact_write_items(TransactItems=items)
        except Exception as e:
            if _is_txn_canceled(e) is not None:
                raise DuplicateKeyError(f"term 已存在: {term.term}") from e
            raise
        return term

    def delete(self, term_id: str) -> Optional[GlossaryTerm]:
        old = self.get(term_id)
        if old is None:
            return None
        self._c.transact_write_items(TransactItems=[
            {"Delete": {"TableName": self._t, "Key": {"pk": _s(term_id)}}},
            {"Delete": {"TableName": self._n, "Key": {"name_key": _s(old.business_key)}}},
        ])
        return old

    def get(self, term_id: str) -> Optional[GlossaryTerm]:
        resp = self._c.get_item(TableName=self._t, Key={"pk": _s(term_id)})
        item = resp.get("Item")
        return _term_from_item(item) if item else None

    def scan(self) -> list[GlossaryTerm]:
        return [_term_from_item(it) for it in _scan_all(self._c, self._t)]


class DynamoDbEntityStore:
    def __init__(self, client: Any, table: str, alias_table: str) -> None:
        self._c = client
        self._t = table
        self._a = alias_table

    def _alias_put(self, alias: str, owner_pk: str, e: EntityEntry) -> dict:
        return {"Put": {"TableName": self._a, "Item": {
            "alias_key": _s(normalize(alias)), "alias_original": _s(alias), "owner_pk": _s(owner_pk),
            "canonical": _s(e.canonical), "entity_type": _s(e.entity_type), "entity_id": _s(e.entity_id)},
            "ConditionExpression": COND_ALIAS_FREE_OR_OWN,
            "ExpressionAttributeValues": {":owner": _s(owner_pk)}}}

    def _map_txn_error(self, exc: Exception, alias_start_idx: int, aliases: list[str]) -> None:
        reasons = _is_txn_canceled(exc)
        if reasons is None:
            raise exc
        if reasons and _failed(reasons[0]):   # index 0 = entity put
            raise DuplicateKeyError("实体已存在（业务键重复）") from exc
        for i, alias in enumerate(aliases):
            idx = alias_start_idx + i
            if idx < len(reasons) and _failed(reasons[idx]):
                raise AliasConflictError(f"别名 '{alias}' 已属于其他实体",
                                         detail={"alias": alias}) from exc
        raise DuplicateKeyError("写入被取消") from exc

    def create(self, entry: EntityEntry) -> EntityEntry:
        import uuid
        entry.entry_id = entry.entry_id or uuid.uuid4().hex
        pk = entry.business_key
        items = [{"Put": {"TableName": self._t, "Item": _entity_to_item(entry),
                          "ConditionExpression": COND_PK_NOT_EXISTS}}]
        items += [self._alias_put(a, pk, entry) for a in entry.aliases]
        try:
            self._c.transact_write_items(TransactItems=items)
        except Exception as e:
            self._map_txn_error(e, alias_start_idx=1, aliases=entry.aliases)
        return entry

    def update(self, entry: EntityEntry) -> EntityEntry:
        pk = entry.business_key
        old = self._get_by_pk(pk)
        old_aliases = old.aliases if old else []
        new_keys = {normalize(a): a for a in entry.aliases}
        old_keys = {normalize(a) for a in old_aliases}
        removed = [k for k in old_keys if k not in new_keys]
        items: list[dict] = [{"Put": {"TableName": self._t, "Item": _entity_to_item(entry)}}]
        for k in removed:
            items.append({"Delete": {"TableName": self._a, "Key": {"alias_key": _s(k)}}})
        added_aliases = list(new_keys.values())
        items += [self._alias_put(a, pk, entry) for a in added_aliases]
        alias_start = 1 + len(removed)
        try:
            self._c.transact_write_items(TransactItems=items)
        except Exception as e:
            self._map_txn_error(e, alias_start_idx=alias_start, aliases=added_aliases)
        return entry

    def delete(self, entry_id: str) -> Optional[EntityEntry]:
        e = self.get(entry_id)
        if e is None:
            return None
        items = [{"Delete": {"TableName": self._t, "Key": {"pk": _s(e.business_key)}}}]
        items += [{"Delete": {"TableName": self._a, "Key": {"alias_key": _s(normalize(a))}}}
                  for a in e.aliases]
        self._c.transact_write_items(TransactItems=items)
        return e

    def get(self, entry_id: str) -> Optional[EntityEntry]:
        for it in _scan_all(self._c, self._t):
            if it.get("entry_id", {}).get("S") == entry_id:
                return _entity_from_item(it)
        return None

    def _get_by_pk(self, pk: str) -> Optional[EntityEntry]:
        resp = self._c.get_item(TableName=self._t, Key={"pk": _s(pk)})
        item = resp.get("Item")
        return _entity_from_item(item) if item else None

    def scan(self) -> list[EntityEntry]:
        return [_entity_from_item(it) for it in _scan_all(self._c, self._t)]

    def scan_aliases(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for e in self.scan():
            if not e.enabled:
                continue
            for a in e.aliases:
                rows.append({"alias_normalized": normalize(a), "alias_original": a,
                             "canonical": e.canonical, "entity_type": e.entity_type,
                             "entity_id": e.entity_id})
        return rows


def _scan_all(client: Any, table: str) -> list[dict]:
    items: list[dict] = []
    kwargs: dict[str, Any] = {"TableName": table}
    while True:
        resp = client.scan(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return items
