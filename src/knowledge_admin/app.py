"""FastAPI 应用装配（design.md §10）。

build_admin_app 支持依赖注入（测试注入 fake service）；未注入则从 AwsSettings/env 构造
真实实现（DynamoDB 存储 + Bedrock embedder + OpenSearch 向量库）。
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from knowledge_admin.api import build_entity_router, build_glossary_router
from knowledge_admin.auth import PlaceholderAuthGuard
from knowledge_admin.errors import AdminError
from knowledge_admin.service import EntityService, GlossaryService


def _error_body(exc: AdminError) -> dict:
    err = {"code": exc.code, "message": exc.message}
    if exc.detail is not None:
        err["detail"] = exc.detail
    return {"status": "error", "error": err}


def build_admin_app(*, glossary_service: Optional[GlossaryService] = None,
                    entity_service: Optional[EntityService] = None,
                    auth_guard=None, settings=None) -> FastAPI:
    app = FastAPI(title="knowledge-admin", version="0.1.0")
    auth_guard = auth_guard or PlaceholderAuthGuard()

    if glossary_service is None or entity_service is None:
        real_g, real_e = _build_real_services(settings)
        glossary_service = glossary_service or real_g
        entity_service = entity_service or real_e

    @app.exception_handler(AdminError)
    async def _handle_admin_error(request, exc: AdminError):  # noqa: ANN001
        return JSONResponse(status_code=exc.http_status, content=_error_body(exc))

    app.include_router(build_glossary_router(glossary_service, auth_guard))
    app.include_router(build_entity_router(entity_service, auth_guard))
    return app


def _build_real_services(settings):
    """从 AwsSettings/env 构造真实存储与服务（延迟导入 boto3/adapters）。"""
    import boto3

    from adapters.bedrock import BedrockEmbedder
    from adapters.opensearch import OpenSearchVectorStore, make_opensearch_client
    from knowledge_admin.dynamodb_stores import DynamoDbEntityStore, DynamoDbTermStore
    from knowledge_admin.observer import LoggingAdminObserver
    from knowledge_admin.sync import GlossaryIndexSync

    region = os.environ.get("AWS_REGION", "us-west-2")
    g_table = os.environ.get("SMARTQUERY_GLOSSARY_TABLE", "knowledge_glossary")
    g_name_table = os.environ.get("SMARTQUERY_GLOSSARY_NAME_TABLE", "knowledge_glossary_name")
    e_table = os.environ.get("SMARTQUERY_ENTITY_TABLE", "knowledge_entity")
    a_table = os.environ.get("SMARTQUERY_ALIAS_TABLE", "knowledge_alias_index")
    endpoint = os.environ.get("SMARTQUERY_OPENSEARCH_ENDPOINT", "")
    index = os.environ.get("SMARTQUERY_OPENSEARCH_INDEX", "smartquery-vectors")
    embed_model = os.environ.get("SMARTQUERY_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")
    embed_dim = int(os.environ.get("SMARTQUERY_EMBED_DIM", "1024"))

    ddb = boto3.client("dynamodb", region_name=region)
    term_store = DynamoDbTermStore(ddb, g_table, g_name_table)
    entity_store = DynamoDbEntityStore(ddb, e_table, a_table)
    embedder = BedrockEmbedder(region=region, model_id=embed_model, dimensions=embed_dim)
    os_client = make_opensearch_client(endpoint=endpoint, region=region) if endpoint else None
    vector_store = OpenSearchVectorStore(client=os_client, index=index) if os_client else None
    if vector_store is None:
        raise RuntimeError("缺少 SMARTQUERY_OPENSEARCH_ENDPOINT，无法构造软通道向量同步")
    sync = GlossaryIndexSync(embedder, vector_store)
    obs = LoggingAdminObserver()
    return GlossaryService(term_store, sync, obs), EntityService(entity_store, obs)
