"""批量导入 / 导出 / 模板（design.md §7）。

- 解析 .xlsx（openpyxl）与 .csv（标准库，UTF-8 兼容 BOM）为条目行。
- 逐行按业务键 upsert（调 Service.create/update），单行失败不中断，产出 Import_Report。
- 生成同列结构的导入模板与导出文件（.xlsx）。
"""

from __future__ import annotations

import csv
import io
from typing import Any, Optional

import openpyxl

from knowledge_admin.errors import AdminError, ValidationError
from knowledge_admin.models import EntityEntry, GlossaryTerm

GLOSSARY_HEADERS = ["标准词", "同义词(逗号分隔)", "定义", "启用(Y/N)"]
ENTITY_HEADERS = ["标准名", "实体类型", "标准id", "别名(逗号分隔)", "启用(Y/N)"]

_TRUE = {"y", "yes", "true", "是", "1", "启用"}


def _to_bool(s: str, default: bool = True) -> bool:
    s = (s or "").strip().lower()
    return default if s == "" else s in _TRUE


def _split_list(s: str) -> list[str]:
    if not s:
        return []
    parts = s.replace("，", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


def _parse(data: bytes, filename: str, ncols: int) -> list[tuple[int, list[str]]]:
    name = (filename or "").lower()
    if name.endswith(".csv"):
        text = data.decode("utf-8-sig")
        rows = [list(r) for r in csv.reader(io.StringIO(text))]
    elif name.endswith((".xlsx", ".xlsm")):
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = [["" if c is None else c for c in r] for r in ws.iter_rows(values_only=True)]
        wb.close()
    else:
        raise ValidationError("仅支持 .xlsx 或 .csv 文件")

    if not rows:
        raise ValidationError("文件为空")
    header_nonempty = [str(h).strip() for h in rows[0] if str(h).strip()]
    if len(header_nonempty) < ncols:
        raise ValidationError(f"列结构与模板不匹配：至少需要 {ncols} 列")

    out: list[tuple[int, list[str]]] = []
    for i, r in enumerate(rows[1:], start=2):   # 行号从 2 起（表头为第 1 行）
        if not any(str(c).strip() for c in r):
            continue
        vals = [str(r[j]).strip() if j < len(r) and r[j] is not None else "" for j in range(ncols)]
        out.append((i, vals))
    return out


def _new_report() -> dict[str, Any]:
    return {"success_count": 0, "updated_count": 0, "failed_count": 0,
            "duplicate_count": 0, "failures": []}


def import_glossary(data: bytes, filename: str, service, actor: str = "system") -> dict:
    rows = _parse(data, filename, len(GLOSSARY_HEADERS))
    existing = {t.business_key: t for t in service.list_all()}
    report = _new_report()
    seen: set[str] = set()
    for rownum, vals in rows:
        raw = dict(zip(GLOSSARY_HEADERS, vals))
        try:
            term = GlossaryTerm(term=vals[0], synonyms=_split_list(vals[1]),
                                definition=vals[2], enabled=_to_bool(vals[3])).validate()
            key = term.business_key
            if key in seen:
                report["duplicate_count"] += 1
            seen.add(key)
            if key in existing:
                term.term_id = existing[key].term_id
                service.update(term, actor)
                report["updated_count"] += 1
            else:
                created = service.create(term, actor)
                existing[key] = created
                report["success_count"] += 1
        except Exception as e:   # noqa: BLE001 —— 逐行容错
            report["failed_count"] += 1
            report["failures"].append({"row": rownum, "data": raw, "error": _msg(e)})
    return report


def import_entities(data: bytes, filename: str, service, actor: str = "system") -> dict:
    rows = _parse(data, filename, len(ENTITY_HEADERS))
    existing = {e.business_key: e for e in service.list_all()}
    report = _new_report()
    seen: set[str] = set()
    for rownum, vals in rows:
        raw = dict(zip(ENTITY_HEADERS, vals))
        try:
            entry = EntityEntry(canonical=vals[0], entity_type=vals[1], entity_id=vals[2],
                                aliases=_split_list(vals[3]), enabled=_to_bool(vals[4])).validate()
            key = entry.business_key
            if key in seen:
                report["duplicate_count"] += 1
            seen.add(key)
            if key in existing:
                entry.entry_id = existing[key].entry_id
                service.update(entry, actor)
                report["updated_count"] += 1
            else:
                created = service.create(entry, actor)
                existing[key] = created
                report["success_count"] += 1
        except Exception as e:   # noqa: BLE001
            report["failed_count"] += 1
            report["failures"].append({"row": rownum, "data": raw, "error": _msg(e)})
    return report


def _msg(e: Exception) -> str:
    return e.message if isinstance(e, AdminError) else f"{type(e).__name__}: {e}"


# ---- 模板 / 导出 ----
def _xlsx_bytes(headers: list[str], rows: list[list[str]]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def glossary_template() -> bytes:
    return _xlsx_bytes(GLOSSARY_HEADERS, [
        ["发电量", "发电,发电量kWh", "指标字段 p83022，单位 kWh", "Y"],
        ["装机容量", "容量", "power_station.install_capacity，单位 kWp", "Y"],
    ])


def entity_template() -> bytes:
    return _xlsx_bytes(ENTITY_HEADERS, [
        ["华能一站电站", "power_station", "1001", "华能一站,一号站", "Y"],
        ["满发小时", "metric", "p83025", "等效利用小时", "Y"],
    ])


def export_glossary(terms: list[GlossaryTerm]) -> bytes:
    rows = [[t.term, ",".join(t.synonyms), t.definition, "Y" if t.enabled else "N"] for t in terms]
    return _xlsx_bytes(GLOSSARY_HEADERS, rows)


def export_entities(entries: list[EntityEntry]) -> bytes:
    rows = [[e.canonical, e.entity_type, e.entity_id, ",".join(e.aliases), "Y" if e.enabled else "N"]
            for e in entries]
    return _xlsx_bytes(ENTITY_HEADERS, rows)
