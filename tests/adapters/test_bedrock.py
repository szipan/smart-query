"""Bedrock 适配器单测（mock 注入，不连真实服务）。

覆盖：
- LLM 正常路径 / system 提示注入 / model_id 覆盖（L4）与缺省（L3）/ 响应结构异常。
- Embedder 正常路径 / 逐条调用 / 请求体字段 / StreamingBody 与 dict 两种响应体 / 异常。
"""

from __future__ import annotations

import io
import json

import pytest

from adapters.bedrock import (
    DEFAULT_EMBED_MODEL_ID,
    DEFAULT_LLM_MODEL_ID,
    BedrockEmbedder,
    BedrockLlmClient,
)


class FakeConverseClient:
    """记录调用参数并返回预置文本的 Converse mock。"""

    def __init__(self, text: str = "好") -> None:
        self._text = text
        self.calls: list[dict] = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {"output": {"message": {"content": [{"text": self._text}]}}}


class FakeInvokeClient:
    """按调用序返回预置向量的 invoke_model mock；body 用 StreamingBody 模拟。"""

    def __init__(self, embeddings: list[list[float]], as_stream: bool = True) -> None:
        self._embeddings = embeddings
        self._as_stream = as_stream
        self.calls: list[dict] = []

    def invoke_model(self, modelId: str, body: str):
        self.calls.append({"modelId": modelId, "body": json.loads(body)})
        emb = self._embeddings[len(self.calls) - 1]
        payload = json.dumps({"embedding": emb}).encode("utf-8")
        if self._as_stream:
            return {"body": io.BytesIO(payload)}
        return {"body": {"embedding": emb}}


# ---------------- LLM ----------------
def test_llm_default_model_and_text() -> None:
    fake = FakeConverseClient(text="发电量为 100kWh")
    llm = BedrockLlmClient(client=fake)
    out = llm.complete(system="你是助手", prompt="今日发电量？")
    assert out == "发电量为 100kWh"
    call = fake.calls[0]
    assert call["modelId"] == DEFAULT_LLM_MODEL_ID
    assert call["system"] == [{"text": "你是助手"}]
    assert call["messages"][0]["content"][0]["text"] == "今日发电量？"
    assert call["inferenceConfig"] == {"maxTokens": 2048, "temperature": 0.0}


def test_llm_omits_system_when_empty() -> None:
    fake = FakeConverseClient()
    BedrockLlmClient(client=fake).complete(prompt="hi")
    assert "system" not in fake.calls[0]


def test_llm_model_id_override_for_layer4() -> None:
    fake = FakeConverseClient()
    llm = BedrockLlmClient(client=fake)
    llm.complete(prompt="hi", model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
                 max_tokens=512, temperature=0.3)
    call = fake.calls[0]
    assert call["modelId"] == "us.anthropic.claude-sonnet-4-20250514-v1:0"
    assert call["inferenceConfig"] == {"maxTokens": 512, "temperature": 0.3}


def test_llm_concatenates_multiple_text_blocks() -> None:
    class MultiBlock(FakeConverseClient):
        def converse(self, **kwargs):
            return {"output": {"message": {"content": [{"text": "a"}, {"text": "b"}]}}}

    assert BedrockLlmClient(client=MultiBlock()).complete(prompt="x") == "ab"


def test_llm_raises_on_malformed_response() -> None:
    class Bad:
        def converse(self, **kwargs):
            return {"unexpected": True}

    with pytest.raises(ValueError):
        BedrockLlmClient(client=Bad()).complete(prompt="x")


# ---------------- Embedder ----------------
def test_embed_returns_vectors_in_order() -> None:
    fake = FakeInvokeClient([[0.1, 0.2], [0.3, 0.4]])
    emb = BedrockEmbedder(client=fake, dimensions=2)
    out = emb.embed(["电站A", "电站B"])
    assert out == [[0.1, 0.2], [0.3, 0.4]]
    assert len(fake.calls) == 2
    assert fake.calls[0]["modelId"] == DEFAULT_EMBED_MODEL_ID
    assert fake.calls[0]["body"] == {"inputText": "电站A", "dimensions": 2, "normalize": True}


def test_embed_reads_dict_body() -> None:
    fake = FakeInvokeClient([[1.0]], as_stream=False)
    out = BedrockEmbedder(client=fake, dimensions=1).embed(["x"])
    assert out == [[1.0]]


def test_embed_empty_input() -> None:
    fake = FakeInvokeClient([])
    assert BedrockEmbedder(client=fake).embed([]) == []


def test_embed_dimensions_property() -> None:
    assert BedrockEmbedder(client=FakeInvokeClient([]), dimensions=512).dimensions == 512


def test_embed_raises_on_missing_embedding() -> None:
    class Bad:
        def invoke_model(self, modelId, body):
            return {"body": json.dumps({"nope": 1}).encode("utf-8")}

    with pytest.raises(ValueError):
        BedrockEmbedder(client=Bad()).embed(["x"])
