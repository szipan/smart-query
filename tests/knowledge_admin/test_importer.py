"""批量导入/导出/模板单测（xlsx via openpyxl + csv）。"""

from __future__ import annotations

import io

import openpyxl
import pytest

from knowledge_admin import importer
from knowledge_admin.errors import ValidationError
from knowledge_admin.service import EntityService, GlossaryService
from knowledge_admin.sync import GlossaryIndexSync

from fakes import FakeEmbedder, FakeEntityStore, FakeTermStore, FakeVectorStore


def _glossary_svc():
    return GlossaryService(FakeTermStore(), GlossaryIndexSync(FakeEmbedder(), FakeVectorStore()))


def _entity_svc():
    return EntityService(FakeEntityStore())


def _xlsx(headers, rows) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _csv(headers, rows) -> bytes:
    lines = [",".join(headers)] + [",".join(r) for r in rows]
    return ("\n".join(lines)).encode("utf-8-sig")


# ---- glossary import ----
def test_import_glossary_xlsx_create_and_update() -> None:
    svc = _glossary_svc()
    data = _xlsx(importer.GLOSSARY_HEADERS, [
        ["发电量", "发电,发电量kWh", "p83022", "Y"],
        ["装机容量", "容量", "kWp", "N"],
    ])
    r = importer.import_glossary(data, "terms.xlsx", svc)
    assert r["success_count"] == 2 and r["failed_count"] == 0
    # 再导一次：同业务键 -> 更新
    r2 = importer.import_glossary(data, "terms.xlsx", svc)
    assert r2["updated_count"] == 2 and r2["success_count"] == 0
    assert svc.search()["total_count"] == 2


def test_import_glossary_csv_and_bom() -> None:
    svc = _glossary_svc()
    data = _csv(importer.GLOSSARY_HEADERS, [["发电量", "发电", "p83022", "Y"]])
    r = importer.import_glossary(data, "terms.csv", svc)
    assert r["success_count"] == 1


def test_import_glossary_row_failure_isolated() -> None:
    svc = _glossary_svc()
    data = _xlsx(importer.GLOSSARY_HEADERS, [
        ["发电量", "", "", "Y"],
        ["", "空标准词", "会失败", "Y"],   # term 为空 -> 校验失败
        ["装机容量", "", "", "Y"],
    ])
    r = importer.import_glossary(data, "t.xlsx", svc)
    assert r["success_count"] == 2 and r["failed_count"] == 1
    assert r["failures"][0]["row"] == 3   # 第 3 行（表头为 1）
    assert svc.search()["total_count"] == 2   # 失败行不影响其余


def test_import_within_file_duplicate_counted() -> None:
    svc = _glossary_svc()
    data = _xlsx(importer.GLOSSARY_HEADERS, [
        ["发电量", "", "v1", "Y"],
        [" 发电量 ", "", "v2", "Y"],   # 同业务键（归一化）
    ])
    r = importer.import_glossary(data, "t.xlsx", svc)
    assert r["duplicate_count"] == 1


def test_import_bad_format_rejected() -> None:
    with pytest.raises(ValidationError):
        importer.import_glossary(b"whatever", "terms.txt", _glossary_svc())


def test_import_bad_columns_rejected() -> None:
    data = _xlsx(["只有一列"], [["x"]])
    with pytest.raises(ValidationError):
        importer.import_glossary(data, "t.xlsx", _glossary_svc())


# ---- entity import ----
def test_import_entities_and_alias_conflict_row_isolated() -> None:
    svc = _entity_svc()
    data = _xlsx(importer.ENTITY_HEADERS, [
        ["华能一站", "power_station", "1001", "一号站", "Y"],
        ["大唐二站", "power_station", "2002", "一号站", "Y"],   # 别名冲突 -> 失败
        ["三站", "power_station", "3003", "三号站", "Y"],
    ])
    r = importer.import_entities(data, "e.xlsx", svc)
    assert r["success_count"] == 2 and r["failed_count"] == 1
    assert "别名" in r["failures"][0]["error"]


# ---- 模板 / 导出 ----
def test_templates_have_headers() -> None:
    for data, headers in [(importer.glossary_template(), importer.GLOSSARY_HEADERS),
                          (importer.entity_template(), importer.ENTITY_HEADERS)]:
        wb = openpyxl.load_workbook(io.BytesIO(data))
        ws = wb.active
        assert [c.value for c in ws[1]] == headers


def test_export_roundtrip_glossary() -> None:
    svc = _glossary_svc()
    importer.import_glossary(_xlsx(importer.GLOSSARY_HEADERS, [["发电量", "发电", "p83022", "Y"]]),
                             "t.xlsx", svc)
    data = importer.export_glossary(svc.list_all())
    # 导出可回填再导入到新服务
    svc2 = _glossary_svc()
    r = importer.import_glossary(data, "export.xlsx", svc2)
    assert r["success_count"] == 1
    assert svc2.list_all()[0].synonyms == ["发电"]
