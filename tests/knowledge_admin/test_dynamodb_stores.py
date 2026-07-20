"""DynamoDbTermStore / DynamoDbEntityStore 单测（fake DynamoDB client，忠实模拟条件写）。"""

from __future__ import annotations

import pytest

from knowledge_admin.dynamodb_stores import (
    COND_ALIAS_FREE_OR_OWN,
    COND_NAME_NOT_EXISTS,
    COND_PK_NOT_EXISTS,
    DynamoDbEntityStore,
    DynamoDbTermStore,
)
from knowledge_admin.errors import AliasConflictError, DuplicateKeyError, NotFoundError
from knowledge_admin.models import EntityEntry, GlossaryTerm


class FakeClientError(Exception):
    def __init__(self, response):
        super().__init__("txn canceled")
        self.response = response


class FakeDynamoClient:
    """忠实模拟本模块用到的 transact_write_items 条件语义 + get_item + scan。"""

    def __init__(self, pk_attrs: dict[str, str]) -> None:
        self.tables: dict[str, dict[str, dict]] = {t: {} for t in pk_attrs}
        self.pk_attrs = pk_attrs

    def _key_of(self, table: str, item: dict) -> str:
        return item[self.pk_attrs[table]]["S"]

    def _cond_ok(self, item_spec: dict, is_put: bool) -> bool:
        table = item_spec["TableName"]
        cond = item_spec.get("ConditionExpression")
        if cond is None:
            return True
        store = self.tables[table]
        if cond in (COND_PK_NOT_EXISTS, COND_NAME_NOT_EXISTS):
            key = self._key_of(table, item_spec["Item"])
            return key not in store
        if cond == COND_ALIAS_FREE_OR_OWN:
            key = self._key_of(table, item_spec["Item"])
            owner = item_spec["ExpressionAttributeValues"][":owner"]["S"]
            existing = store.get(key)
            return existing is None or existing["owner_pk"]["S"] == owner
        raise AssertionError(f"未知条件: {cond}")

    def transact_write_items(self, TransactItems):
        reasons = []
        ok = True
        for ti in TransactItems:
            if "Put" in ti:
                passed = self._cond_ok(ti["Put"], is_put=True)
            else:
                passed = True
            reasons.append({"Code": "None"} if passed else {"Code": "ConditionalCheckFailed"})
            ok = ok and passed
        if not ok:
            raise FakeClientError({"Error": {"Code": "TransactionCanceledException"},
                                   "CancellationReasons": reasons})
        for ti in TransactItems:
            if "Put" in ti:
                spec = ti["Put"]
                self.tables[spec["TableName"]][self._key_of(spec["TableName"], spec["Item"])] = spec["Item"]
            else:
                spec = ti["Delete"]
                table = spec["TableName"]
                key = spec["Key"][self.pk_attrs[table]]["S"]
                self.tables[table].pop(key, None)

    def get_item(self, TableName, Key):
        key = Key[self.pk_attrs[TableName]]["S"]
        item = self.tables[TableName].get(key)
        return {"Item": item} if item else {}

    def scan(self, TableName, **kwargs):
        return {"Items": list(self.tables[TableName].values())}


# ---- Term store ----
def _term_client():
    return FakeDynamoClient({"glossary": "pk", "glossary_name": "name_key"})


def _term_store(c):
    return DynamoDbTermStore(c, "glossary", "glossary_name")


def test_term_create_get_and_duplicate() -> None:
    s = _term_store(_term_client())
    t = s.create(GlossaryTerm(term="发电量", synonyms=["发电"]).validate())
    assert t.term_id and s.get(t.term_id).term == "发电量"
    with pytest.raises(DuplicateKeyError):
        s.create(GlossaryTerm(term=" 发电量 ").validate())


def test_term_update_moves_name_guard() -> None:
    s = _term_store(_term_client())
    t = s.create(GlossaryTerm(term="发电量").validate())
    t.term = "月发电量"
    s.update(t)
    assert s.get(t.term_id).term == "月发电量"
    # 旧名释放后可再被新条目占用
    s.create(GlossaryTerm(term="发电量").validate())


def test_term_update_duplicate_name_rejected() -> None:
    s = _term_store(_term_client())
    s.create(GlossaryTerm(term="发电量").validate())
    t2 = s.create(GlossaryTerm(term="装机容量").validate())
    t2.term = "发电量"   # 撞已存在
    with pytest.raises(DuplicateKeyError):
        s.update(t2)


def test_term_update_missing_not_found() -> None:
    s = _term_store(_term_client())
    with pytest.raises(NotFoundError):
        s.update(GlossaryTerm(term="x", term_id="nope").validate())


def test_term_delete_and_scan() -> None:
    s = _term_store(_term_client())
    t = s.create(GlossaryTerm(term="发电量").validate())
    assert len(s.scan()) == 1
    s.delete(t.term_id)
    assert s.get(t.term_id) is None and s.scan() == []


# ---- Entity store ----
def _entity_client():
    return FakeDynamoClient({"entity": "pk", "alias": "alias_key"})


def _entity_store(c):
    return DynamoDbEntityStore(c, "entity", "alias")


def _e(canonical="华能一站", et="power_station", eid="1001", aliases=None):
    return EntityEntry(canonical=canonical, entity_type=et, entity_id=eid,
                       aliases=aliases or []).validate()


def test_entity_create_and_duplicate_business_key() -> None:
    s = _entity_store(_entity_client())
    s.create(_e(aliases=["一号站"]))
    with pytest.raises(DuplicateKeyError):
        s.create(_e(canonical="别的名"))


def test_entity_alias_conflict_across_entities() -> None:
    s = _entity_store(_entity_client())
    s.create(_e(eid="1001", aliases=["一号站"]))
    with pytest.raises(AliasConflictError) as ei:
        s.create(_e(eid="2002", aliases=["一号站"]))
    assert ei.value.detail["alias"] == "一号站"


def test_entity_get_by_entry_id_and_scan_aliases() -> None:
    s = _entity_store(_entity_client())
    e = s.create(_e(aliases=["一号站", "华能站"]))
    got = s.get(e.entry_id)
    assert got is not None and got.business_key == "power_station#1001"
    aliases = {r["alias_original"] for r in s.scan_aliases()}
    assert aliases == {"一号站", "华能站"}


def test_entity_update_alias_diff() -> None:
    s = _entity_store(_entity_client())
    e = s.create(_e(aliases=["一号站", "旧别名"]))
    e.aliases = ["一号站", "新别名"]   # 删"旧别名"、加"新别名"、保留"一号站"
    s.update(e)
    aliases = {r["alias_original"] for r in s.scan_aliases()}
    assert aliases == {"一号站", "新别名"}
    # "旧别名"已释放，可被别的实体占用
    s.create(_e(eid="3003", aliases=["旧别名"]))


def test_entity_delete_frees_aliases() -> None:
    s = _entity_store(_entity_client())
    e = s.create(_e(aliases=["一号站"]))
    s.delete(e.entry_id)
    assert s.get(e.entry_id) is None
    s.create(_e(eid="2002", aliases=["一号站"]))   # 别名已释放
