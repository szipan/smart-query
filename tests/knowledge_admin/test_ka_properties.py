"""属性测试（hypothesis）：P1 业务键唯一、P2 别名全局唯一、P5 导入逐行独立且计数一致。"""

from __future__ import annotations

import io

import openpyxl
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from knowledge_admin import importer
from knowledge_admin.errors import AdminError
from knowledge_admin.models import EntityEntry, GlossaryTerm, normalize
from knowledge_admin.service import EntityService, GlossaryService
from knowledge_admin.sync import GlossaryIndexSync

from fakes import FakeEmbedder, FakeEntityStore, FakeTermStore, FakeVectorStore

_words = st.text(alphabet="abcdefghij 一二三四", min_size=1, max_size=6)


def _glossary_svc():
    return GlossaryService(FakeTermStore(), GlossaryIndexSync(FakeEmbedder(), FakeVectorStore()))


def _entity_svc():
    return EntityService(FakeEntityStore())


# ---- P1：软通道 term 业务键唯一 ----
@settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
@given(terms=st.lists(_words, min_size=1, max_size=20))
def test_p1_glossary_business_key_unique(terms) -> None:
    svc = _glossary_svc()
    for t in terms:
        try:
            svc.create(GlossaryTerm(term=t))
        except AdminError:
            pass
    keys = [x.business_key for x in svc.list_all()]
    assert len(keys) == len(set(keys))   # 无重复业务键


# ---- P1 + P2：硬通道业务键唯一 + 别名全局唯一 ----
@settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
@given(rows=st.lists(
    st.tuples(st.sampled_from(["ps", "metric", "org"]), _words,
              st.lists(_words, min_size=0, max_size=3)),
    min_size=1, max_size=20))
def test_p1_p2_entity_uniqueness(rows) -> None:
    svc = _entity_svc()
    for et, eid, aliases in rows:
        try:
            svc.create(EntityEntry(canonical="c" + eid, entity_type=et, entity_id=eid, aliases=aliases))
        except AdminError:
            pass
    entries = svc.list_all()
    bkeys = [e.business_key for e in entries]
    assert len(bkeys) == len(set(bkeys))   # P1
    all_alias_keys: list[str] = []
    for e in entries:
        all_alias_keys.extend(e.alias_keys())
    assert len(all_alias_keys) == len(set(all_alias_keys))   # P2 别名全局唯一


# ---- P5：导入逐行独立且计数一致 ----
def _xlsx(rows) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(importer.GLOSSARY_HEADERS)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@settings(max_examples=40, suppress_health_check=[HealthCheck.too_slow])
@given(terms=st.lists(st.text(alphabet="abc 一二", min_size=0, max_size=4), min_size=1, max_size=15))
def test_p5_import_counts_consistent(terms) -> None:
    svc = _glossary_svc()
    rows = [[t, "", "", "Y"] for t in terms]
    report = importer.import_glossary(_xlsx(rows), "t.xlsx", svc)
    # 成功创建数 == 最终存储条目数（更新数不新增；失败/文件内重复不新增新键）
    assert report["success_count"] == len(svc.list_all())
    # 计数自洽：每个被解析的数据行恰好落入 success/updated/failed 之一。
    # 每行第 4 列固定 "Y" 故所有行都非空、都会被解析（空 term 行 -> failed）。
    parsed_rows = len(rows)
    assert report["success_count"] + report["updated_count"] + report["failed_count"] == parsed_rows
