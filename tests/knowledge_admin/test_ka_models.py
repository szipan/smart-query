"""knowledge-admin 模型与错误单测。"""

from __future__ import annotations

import pytest

from knowledge_admin.errors import (
    AdminError,
    AliasConflictError,
    DuplicateKeyError,
    IndexSyncError,
    NotFoundError,
    ValidationError,
)
from knowledge_admin.models import EntityEntry, GlossaryTerm, normalize


# ---- normalize ----
def test_normalize_nfkc_and_case_and_space() -> None:
    # 全角字母数字 -> 半角；大写 -> 小写；折叠空白
    assert normalize("　ＡＢＣ　１２３　") == "abc 123"
    assert normalize("华能  一站\t电站") == "华能 一站 电站"
    assert normalize(None) == ""


# ---- GlossaryTerm ----
def test_glossary_validate_ok_and_dedup_synonyms() -> None:
    t = GlossaryTerm(term="  发电量 ", synonyms=[" 发电 ", "发电", "", "generation"],
                     definition="  指标 p83022 ").validate()
    assert t.term == "发电量"
    assert t.synonyms == ["发电", "generation"]  # 归一化去重、丢空串
    assert t.definition == "指标 p83022"
    assert t.business_key == "发电量"
    assert "发电量" in t.index_text() and "generation" in t.index_text()


def test_glossary_empty_term_rejected() -> None:
    with pytest.raises(ValidationError):
        GlossaryTerm(term="   ").validate()


# ---- EntityEntry ----
def test_entity_validate_ok() -> None:
    e = EntityEntry(canonical="华能一站电站", entity_type=" power_station ",
                    entity_id=" 1001 ", aliases=[" 华能一站 ", "一号站", "华能一站"]).validate()
    assert e.entity_type == "power_station"
    assert e.entity_id == "1001"
    assert e.aliases == ["华能一站", "一号站"]  # 去重
    assert e.business_key == "power_station#1001"
    assert e.alias_keys() == ["华能一站", "一号站"]


def test_entity_alias_equal_canonical_dropped() -> None:
    e = EntityEntry(canonical="发电量", entity_type="metric", entity_id="p83022",
                    aliases=["发电量", "发电"]).validate()
    assert e.aliases == ["发电"]  # 与 canonical 同名的别名被剔除


@pytest.mark.parametrize("bad", [
    dict(canonical="", entity_type="t", entity_id="1"),
    dict(canonical="c", entity_type="", entity_id="1"),
    dict(canonical="c", entity_type="t", entity_id=""),
])
def test_entity_required_fields(bad) -> None:
    with pytest.raises(ValidationError):
        EntityEntry(**bad).validate()


# ---- errors ----
def test_error_http_status_mapping() -> None:
    assert ValidationError("x").http_status == 400
    assert DuplicateKeyError("x").http_status == 409
    assert AliasConflictError("x").http_status == 409
    assert NotFoundError("x").http_status == 404
    assert IndexSyncError("x").http_status == 502
    assert isinstance(ValidationError("x"), AdminError)


def test_error_carries_detail() -> None:
    e = AliasConflictError("冲突", detail={"alias": "一号站", "owner": "power_station#1001"})
    assert e.detail["alias"] == "一号站"
    assert e.message == "冲突"
