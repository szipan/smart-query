"""请求/响应契约与统一信封（design.md §4.1/§4.2/§4.6；Requirements 7/9.7）。

QueryResponse 设计为未来流式可作为「最终结果事件」复用（§12.3 扩展性）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from api_gateway.config import MAX_QUESTION_LEN
from api_gateway.errors import BadRequest, StreamingNotAvailable


@dataclass(frozen=True)
class QueryRequest:
    question: str
    session_id: Optional[str] = None
    stream: bool = False
    client_meta: Optional[dict] = None


def parse_query_request(body: Optional[dict]) -> QueryRequest:
    """解析并校验请求体。question 非空且 ≤ 上限；stream=true 本期 → 400。"""
    if not isinstance(body, dict):
        raise BadRequest("请求体必须为 JSON 对象")
    question = body.get("question")
    if not isinstance(question, str) or not question.strip():
        raise BadRequest("question 不能为空")
    if len(question) > MAX_QUESTION_LEN:
        raise BadRequest(f"question 超过长度上限 {MAX_QUESTION_LEN}")
    stream = bool(body.get("stream", False))
    if stream:
        # 本期不支持流式（design §12：v2 只做变体 A）
        raise StreamingNotAvailable("流式在本期不可用")
    return QueryRequest(
        question=question,
        session_id=body.get("session_id"),
        stream=stream,
        client_meta=body.get("client_meta"),
    )


@dataclass(frozen=True)
class QueryResponse:
    """成功响应统一信封。未来流式可作为「最终结果事件」承载同一 JSON。"""

    request_id: str
    status: str          # "ok"
    data: Any
    source: str          # "query" | "legacy"

    def to_dict(self) -> dict:
        return {"request_id": self.request_id, "status": self.status,
                "data": self.data, "source": self.source}


@dataclass(frozen=True)
class ErrorEnvelope:
    code: str
    message: str
    request_id: str
    retry_after: Optional[int] = None

    def to_dict(self) -> dict:
        d = {"code": self.code, "message": self.message, "request_id": self.request_id}
        if self.retry_after is not None:
            d["retry_after"] = self.retry_after
        return {"status": "error", "error": d}


@dataclass(frozen=True)
class GatewayResponse:
    """薄 Lambda 归一化响应（适配器再转 API Gateway proxy 响应）。"""

    status_code: int
    body: dict
    headers: dict = field(default_factory=dict)
