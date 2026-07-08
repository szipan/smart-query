"""AWS Bedrock 适配器：LLM 客户端（Converse）与文本向量化（Titan Embed v2）。

实现的 Protocol 接口：
- ``LlmClient``（L3 ``query_engine.deps`` 与 L4 ``text2sql_pipeline.deps`` 共用；
  统一签名兼容两者：L3 不传 ``model_id``，L4 传 ``model_id``）。
- ``Embedder``（L3/L4 共用）。

设计要点（参考 SQLBot「批量 embed + 阈值召回」思路，clean-room 重写；SQLBot 用本地
HF 模型 + pgvector，这里改用 Bedrock 托管模型 + 注入式 boto3 client）：
- boto3 client 依赖注入，便于单测 mock，不在单测中连真实服务。
- 已实测可用的模型：LLM ``us.anthropic.claude-haiku-4-5-20251001-v1:0``（Converse），
  Embed ``amazon.titan-embed-text-v2:0``（维度 1024）。
"""

from __future__ import annotations

import json
from typing import Any, Optional

# 已实测可用的默认模型（us 前缀为跨区推理配置文件）
DEFAULT_LLM_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"
DEFAULT_EMBED_DIMENSIONS = 1024


def _make_runtime_client(region: Optional[str]):
    """惰性创建 bedrock-runtime client（避免在无 boto3 环境下 import 即失败）。"""
    import boto3  # 延迟导入

    return boto3.client("bedrock-runtime", region_name=region)


class BedrockLlmClient:
    """基于 Bedrock Converse API 的 LLM 客户端。

    统一 ``complete`` 签名同时满足 L3 与 L4 的 ``LlmClient`` Protocol：
    - L3 调用不含 ``model_id`` -> 使用 ``default_model_id``。
    - L4 调用含 ``model_id`` -> 使用传入值。
    """

    def __init__(
        self,
        *,
        client: Any = None,
        region: Optional[str] = None,
        default_model_id: str = DEFAULT_LLM_MODEL_ID,
    ) -> None:
        self._client = client if client is not None else _make_runtime_client(region)
        self._default_model_id = default_model_id

    def complete(
        self,
        *,
        system: str = "",
        prompt: str,
        model_id: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> str:
        mid = model_id or self._default_model_id
        kwargs: dict[str, Any] = {
            "modelId": mid,
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
        }
        if system:
            kwargs["system"] = [{"text": system}]
        resp = self._client.converse(**kwargs)
        return _extract_converse_text(resp)


def _extract_converse_text(resp: dict[str, Any]) -> str:
    """从 Converse 响应中拼接所有 text 分块。"""
    try:
        blocks = resp["output"]["message"]["content"]
    except (KeyError, TypeError) as exc:  # 结构异常直接暴露，便于定位
        raise ValueError(f"Bedrock Converse 响应结构异常: {resp!r}") from exc
    parts = [b["text"] for b in blocks if isinstance(b, dict) and "text" in b]
    return "".join(parts)


class BedrockEmbedder:
    """基于 Titan Embed Text v2 的向量化实现。

    Titan v2 每次 invoke 处理单条 ``inputText``，因此对 ``texts`` 逐条调用并按序返回。
    """

    def __init__(
        self,
        *,
        client: Any = None,
        region: Optional[str] = None,
        model_id: str = DEFAULT_EMBED_MODEL_ID,
        dimensions: int = DEFAULT_EMBED_DIMENSIONS,
        normalize: bool = True,
    ) -> None:
        self._client = client if client is not None else _make_runtime_client(region)
        self._model_id = model_id
        self._dimensions = dimensions
        self._normalize = normalize

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            body = json.dumps(
                {
                    "inputText": text,
                    "dimensions": self._dimensions,
                    "normalize": self._normalize,
                }
            )
            resp = self._client.invoke_model(modelId=self._model_id, body=body)
            payload = _read_invoke_body(resp)
            emb = payload.get("embedding")
            if not isinstance(emb, list):
                raise ValueError(f"Titan Embed 响应缺少 embedding: {payload!r}")
            out.append([float(x) for x in emb])
        return out


def _read_invoke_body(resp: dict[str, Any]) -> dict[str, Any]:
    """读取 invoke_model 响应体（StreamingBody 或已解析 dict 皆可）。"""
    body = resp["body"]
    if hasattr(body, "read"):
        body = body.read()
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8")
    if isinstance(body, str):
        return json.loads(body)
    return body  # 已是 dict（测试可直接给 dict）
