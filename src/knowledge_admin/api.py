"""FastAPI 路由（design.md §5）。软通道 /admin/glossary、硬通道 /admin/entities。

写操作先过 AuthGuard；统一响应信封 {status:"ok", data:...}；错误由 app 层异常处理器
转为 {status:"error", error:{code,message,detail?}}。
"""

from __future__ import annotations

import io
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, Path, Query, UploadFile
from fastapi.responses import StreamingResponse

from knowledge_admin import importer
from knowledge_admin.models import EntityEntry, GlossaryTerm

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _ok(data) -> dict:
    return {"status": "ok", "data": data}


def _page_to_dict(page: dict) -> dict:
    return {**page, "data": [asdict(x) for x in page["data"]]}


def _xlsx_response(data: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(io.BytesIO(data), media_type=_XLSX_MEDIA,
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _auth_dep(auth_guard):
    def dep(authorization: Optional[str] = Header(default=None)):
        return auth_guard.require_admin(authorization)
    return dep


def build_glossary_router(service, auth_guard) -> APIRouter:
    r = APIRouter(prefix="/admin/glossary", tags=["glossary"])
    admin = Depends(_auth_dep(auth_guard))

    @r.get("")
    def search(word: Optional[str] = Query(None), current_page: int = Query(1),
               page_size: int = Query(20)):
        return _ok(_page_to_dict(service.search(word, current_page, page_size)))

    @r.put("")
    def create_or_update(payload: dict = Body(...), principal=admin):
        term = GlossaryTerm(term=payload.get("term", ""),
                            synonyms=list(payload.get("synonyms", []) or []),
                            definition=payload.get("definition", "") or "",
                            enabled=bool(payload.get("enabled", True)),
                            term_id=payload.get("term_id"))
        return _ok(asdict(service.create_or_update(term, principal.actor_id)))

    @r.delete("")
    def delete(payload: dict = Body(...), principal=admin):
        ids = payload.get("ids") if isinstance(payload, dict) else payload
        return _ok({"deleted": service.delete(list(ids or []), principal.actor_id)})

    @r.post("/{term_id}/enable/{enabled}")
    def enable(term_id: str = Path(...), enabled: bool = Path(...), principal=admin):
        return _ok(asdict(service.set_enabled(term_id, enabled, principal.actor_id)))

    @r.post("/import")
    async def do_import(file: UploadFile, principal=admin):
        data = await file.read()
        return _ok(importer.import_glossary(data, file.filename or "", service, principal.actor_id))

    @r.get("/export")
    def export(word: Optional[str] = Query(None)):
        items = service.search(word, 1, 10_000)["data"]
        return _xlsx_response(importer.export_glossary(items), "glossary.xlsx")

    @r.get("/template")
    def template():
        return _xlsx_response(importer.glossary_template(), "glossary_template.xlsx")

    return r


def build_entity_router(service, auth_guard) -> APIRouter:
    r = APIRouter(prefix="/admin/entities", tags=["entities"])
    admin = Depends(_auth_dep(auth_guard))

    @r.get("")
    def search(keyword: Optional[str] = Query(None), entity_type: Optional[str] = Query(None),
               current_page: int = Query(1), page_size: int = Query(20)):
        return _ok(_page_to_dict(service.search(keyword, entity_type, current_page, page_size)))

    @r.put("")
    def create_or_update(payload: dict = Body(...), principal=admin):
        entry = EntityEntry(canonical=payload.get("canonical", ""),
                            entity_type=payload.get("entity_type", ""),
                            entity_id=payload.get("entity_id", ""),
                            aliases=list(payload.get("aliases", []) or []),
                            enabled=bool(payload.get("enabled", True)),
                            entry_id=payload.get("entry_id"))
        return _ok(asdict(service.create_or_update(entry, principal.actor_id)))

    @r.delete("")
    def delete(payload: dict = Body(...), principal=admin):
        ids = payload.get("ids") if isinstance(payload, dict) else payload
        return _ok({"deleted": service.delete(list(ids or []), principal.actor_id)})

    @r.post("/{entry_id}/enable/{enabled}")
    def enable(entry_id: str = Path(...), enabled: bool = Path(...), principal=admin):
        return _ok(asdict(service.set_enabled(entry_id, enabled, principal.actor_id)))

    @r.post("/import")
    async def do_import(file: UploadFile, principal=admin):
        data = await file.read()
        return _ok(importer.import_entities(data, file.filename or "", service, principal.actor_id))

    @r.get("/export")
    def export(keyword: Optional[str] = Query(None), entity_type: Optional[str] = Query(None)):
        items = service.search(keyword, entity_type, 1, 10_000)["data"]
        return _xlsx_response(importer.export_entities(items), "entities.xlsx")

    @r.get("/template")
    def template():
        return _xlsx_response(importer.entity_template(), "entities_template.xlsx")

    return r
