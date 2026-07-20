"""API 测试（FastAPI TestClient + 注入 fake service）。"""

from __future__ import annotations

import io

import openpyxl
import pytest
from fastapi.testclient import TestClient

from knowledge_admin import importer
from knowledge_admin.app import build_admin_app
from knowledge_admin.auth import PlaceholderAuthGuard
from knowledge_admin.service import EntityService, GlossaryService
from knowledge_admin.sync import GlossaryIndexSync

from fakes import FakeEmbedder, FakeEntityStore, FakeTermStore, FakeVectorStore


def _client(auth_guard=None):
    g = GlossaryService(FakeTermStore(), GlossaryIndexSync(FakeEmbedder(), FakeVectorStore()))
    e = EntityService(FakeEntityStore())
    app = build_admin_app(glossary_service=g, entity_service=e, auth_guard=auth_guard)
    return TestClient(app), g, e


def test_glossary_create_search_enable_delete() -> None:
    c, _, _ = _client()
    r = c.put("/admin/glossary", json={"term": "发电量", "synonyms": ["发电"], "definition": "p83022"})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    term_id = r.json()["data"]["term_id"]

    r = c.get("/admin/glossary", params={"word": "发电"})
    assert r.json()["data"]["total_count"] == 1

    r = c.post(f"/admin/glossary/{term_id}/enable/false")
    assert r.json()["data"]["enabled"] is False

    r = c.request("DELETE", "/admin/glossary", json={"ids": [term_id]})
    assert r.json()["data"]["deleted"] == 1


def test_glossary_duplicate_returns_409() -> None:
    c, _, _ = _client()
    c.put("/admin/glossary", json={"term": "发电量"})
    r = c.put("/admin/glossary", json={"term": " 发电量 "})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "DUPLICATE_KEY"


def test_glossary_validation_error_400() -> None:
    c, _, _ = _client()
    r = c.put("/admin/glossary", json={"term": "   "})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_entity_alias_conflict_409() -> None:
    c, _, _ = _client()
    c.put("/admin/entities", json={"canonical": "华能一站", "entity_type": "power_station",
                                    "entity_id": "1001", "aliases": ["一号站"]})
    r = c.put("/admin/entities", json={"canonical": "大唐二站", "entity_type": "power_station",
                                       "entity_id": "2002", "aliases": ["一号站"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALIAS_CONFLICT"
    assert r.json()["error"]["detail"]["alias"] == "一号站"


def test_import_endpoint_returns_report() -> None:
    c, _, _ = _client()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(importer.GLOSSARY_HEADERS)
    ws.append(["发电量", "发电", "p83022", "Y"])
    buf = io.BytesIO()
    wb.save(buf)
    r = c.post("/admin/glossary/import",
               files={"file": ("terms.xlsx", buf.getvalue(),
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200 and r.json()["data"]["success_count"] == 1


def test_template_and_export_download() -> None:
    c, _, _ = _client()
    r = c.get("/admin/glossary/template")
    assert r.status_code == 200 and r.content[:2] == b"PK"   # xlsx = zip


# ---- 鉴权 ----
def test_auth_rejects_non_admin_write() -> None:
    c, _, _ = _client(auth_guard=PlaceholderAuthGuard(admin_token="s3cret"))
    # 无 token 写操作 -> 401，且不改数据
    r = c.put("/admin/glossary", json={"term": "发电量"})
    assert r.status_code == 401
    # 正确 token 放行
    r2 = c.put("/admin/glossary", json={"term": "发电量"},
               headers={"Authorization": "Bearer s3cret"})
    assert r2.status_code == 200


def test_read_allowed_without_auth_in_token_mode() -> None:
    # 读操作不加鉴权依赖
    c, _, _ = _client(auth_guard=PlaceholderAuthGuard(admin_token="s3cret"))
    r = c.get("/admin/glossary")
    assert r.status_code == 200
