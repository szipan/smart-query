"""薄 Lambda 编排（design.md §2.2/§9.1；Requirements 3/4/5/6/7/8/11/12）。

固定顺序：解析+构造身份 → 单用户限流（早于路由与下游）→ 路由决策 → 请求整形+身份透传
→ 调下游 → 整形回包。薄层零智能：不生成 SQL、不查库、不调 LLM、不解析范围。

本期只做变体 A（stream=true → 400）。响应整形层的**消费点**建模为可分块抽象
（consume 本期只产出单块），编排逻辑不假设"一次性返回"，为未来流式免返工（§12.3）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from api_gateway.clients import EngineClient, LegacyClient
from api_gateway.envelope import ErrorEnvelope, GatewayResponse, QueryResponse, parse_query_request
from api_gateway.errors import Layer2Error, RateLimitExceeded
from api_gateway.identity import build_identity
from api_gateway.observer import GatewayObserver, NullGatewayObserver, mask_user_id
from api_gateway.ratelimit import RateLimiter
from api_gateway.router import RequestRouter, RouteTarget


@dataclass(frozen=True)
class GatewayRequest:
    """归一化入站请求（传输无关；API Gateway proxy event 由适配器映射为此）。

    authorizer_claims：选项 A 下由 Lambda Authorizer 校验并注入的 claims。
    """

    body: Optional[dict]
    headers: dict = field(default_factory=dict)
    authorizer_claims: Optional[dict] = None
    request_id: Optional[str] = None


@dataclass
class GatewayDependencies:
    rate_limiter: RateLimiter
    router: RequestRouter
    engine_client: EngineClient
    legacy_client: Optional[LegacyClient] = None
    observer: Optional[GatewayObserver] = None


def _consume(downstream_resp: Any) -> Iterator[Any]:
    """响应整形的消费点：把下游响应建模为可分块序列。

    本期只产出单块（一次性返回）；未来流式可改为逐块 yield，整形层逐块 emit，编排不变。
    """
    yield downstream_resp


def _reshape_response(downstream_resp: Any) -> Any:
    """整形层：消费下游响应，产出面向客户端的 data。本期恰好一块。"""
    last = None
    for chunk in _consume(downstream_resp):
        last = chunk
    return last


class ThinLambda:
    """编排胶水。只判断 + 转发 + 计数。"""

    def __init__(self, deps: GatewayDependencies) -> None:
        self._deps = deps
        self._obs = deps.observer or NullGatewayObserver()

    def handle(self, req: GatewayRequest) -> GatewayResponse:
        request_id = self._request_id(req)
        try:
            # 步骤1：解析请求 + 构造可信身份（认证已在入口 Authorizer 完成 —— 选项 A）
            query = parse_query_request(req.body)  # stream=true → StreamingNotAvailable(400)
            identity = build_identity(req.authorizer_claims, request_id)
            self._log("identity", request_id, {"user": mask_user_id(identity.user_id)})

            # 步骤2：单用户业务限流（严格早于路由与下游 —— Property 7）
            decision = self._deps.rate_limiter.check_and_increment(identity.user_id, request_id)
            self._log("rate_limit", request_id, {"allowed": decision.allowed})
            if not decision.allowed:
                raise RateLimitExceeded(decision.retry_after)

            # 步骤3：Legacy/Query 路由决策（决策在此，执行在下游）
            route = self._deps.router.route(query.question)
            self._log("route", request_id, {"target": route.target.value, "rule_id": route.rule_id})

            # 步骤4+5：请求整形 + 身份透传 + 调下游
            if route.target == RouteTarget.LEGACY:
                if self._deps.legacy_client is None:
                    raise Layer2Error("legacy 路由未配置")
                raw = self._deps.legacy_client.invoke(query, identity, route.rule_id or "")
                source = "legacy"
            else:
                raw = self._deps.engine_client.invoke(query, identity)
                source = "query"

            # 步骤6：整形回包（可分块消费点，本期单块）
            data = _reshape_response(raw)
            resp = QueryResponse(request_id=request_id, status="ok", data=data, source=source)
            self._log("done", request_id, {"source": source, "status_code": 200})
            return GatewayResponse(status_code=200, body=resp.to_dict())

        except RateLimitExceeded as e:
            return self._error(request_id, e, retry_after=e.retry_after,
                               headers={"Retry-After": str(e.retry_after)})
        except Layer2Error as e:
            return self._error(request_id, e)
        except Exception as e:  # fail-closed 兜底，不泄露内部细节
            self._log("unhandled", request_id, {"err_type": type(e).__name__})
            env = ErrorEnvelope(code="DOWNSTREAM_ERROR", message="内部错误", request_id=request_id)
            return GatewayResponse(status_code=500, body=env.to_dict())

    def _request_id(self, req: GatewayRequest) -> str:
        return req.request_id or (req.headers or {}).get("X-Request-Id") or str(uuid.uuid4())

    def _error(self, request_id: str, e: Layer2Error, retry_after: Optional[int] = None,
               headers: Optional[dict] = None) -> GatewayResponse:
        # 面向客户端的 message 不泄露内部实现细节
        env = ErrorEnvelope(code=e.code, message=_safe_message(e.code), request_id=request_id,
                            retry_after=retry_after)
        self._log("error", request_id, {"code": e.code, "status_code": e.http_status})
        return GatewayResponse(status_code=e.http_status, body=env.to_dict(), headers=headers or {})

    def _log(self, step: str, request_id: str, attrs: dict) -> None:
        self._obs.on_step(step, request_id, attrs)


_SAFE_MESSAGES = {
    "BAD_REQUEST": "请求无效",
    "UNAUTHORIZED": "未认证或认证失败",
    "RATE_LIMITED": "请求过于频繁，请稍后重试",
    "DOWNSTREAM_ERROR": "服务暂时不可用",
}


def _safe_message(code: str) -> str:
    return _SAFE_MESSAGES.get(code, "请求处理失败")
