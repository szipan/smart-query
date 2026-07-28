"""真实依赖的环境驱动配置（单一切换点）。

模型、区域、端点、表名等全部经环境变量注入，便于「换模型/换区域」而不改代码：
- 换 LLM 模型：设置 ``SMARTQUERY_LLM_MODEL_ID`` 即可（wiring 会把它注入到每个请求的
  ``model_id``，覆盖 L4 默认；也可在单次请求里再覆盖）。
- 换向量维度/模型：``SMARTQUERY_EMBED_MODEL_ID`` / ``SMARTQUERY_EMBED_DIM``。
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str) -> str:
    val = os.environ.get(name)
    return val if val not in (None, "") else default


@dataclass(frozen=True)
class AwsSettings:
    region: str
    llm_model_id: str
    embed_model_id: str
    embed_dimensions: int
    opensearch_endpoint: str
    opensearch_index: str
    ddb_table: str
    user_rate_limit: int
    memory_id: str
    # 槽位抽取专用模型（独立于主 LLM；应选支持 temperature=0 的模型以保证抽取确定性）
    slot_extraction_model_id: str = "us.anthropic.claude-sonnet-4-6"
    # —— knowledge-admin 知识表 ——
    glossary_table: str = "knowledge_glossary"
    glossary_name_table: str = "knowledge_glossary_name"
    entity_table: str = "knowledge_entity"
    alias_table: str = "knowledge_alias_index"

    @classmethod
    def from_env(cls) -> "AwsSettings":
        return cls(
            region=_env("AWS_REGION", "us-west-2"),
            llm_model_id=_env("SMARTQUERY_LLM_MODEL_ID",
                              "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
            embed_model_id=_env("SMARTQUERY_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0"),
            embed_dimensions=int(_env("SMARTQUERY_EMBED_DIM", "1024")),
            opensearch_endpoint=_env("SMARTQUERY_OPENSEARCH_ENDPOINT", ""),
            opensearch_index=_env("SMARTQUERY_OPENSEARCH_INDEX", "smartquery-vectors"),
            ddb_table=_env("SMARTQUERY_DDB_TABLE", "api_gateway_rate_limit"),
            user_rate_limit=int(_env("SMARTQUERY_USER_RATE_LIMIT", "200")),
            memory_id=_env("SMARTQUERY_MEMORY_ID", ""),
            slot_extraction_model_id=_env("SMARTQUERY_SLOT_MODEL_ID",
                                          "us.anthropic.claude-sonnet-4-6"),
            glossary_table=_env("SMARTQUERY_GLOSSARY_TABLE", "knowledge_glossary"),
            glossary_name_table=_env("SMARTQUERY_GLOSSARY_NAME_TABLE", "knowledge_glossary_name"),
            entity_table=_env("SMARTQUERY_ENTITY_TABLE", "knowledge_entity"),
            alias_table=_env("SMARTQUERY_ALIAS_TABLE", "knowledge_alias_index"),
        )
