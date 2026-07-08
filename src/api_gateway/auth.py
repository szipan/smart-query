"""认证（选项 A · 网关终结）（design.md §5.1/§5.2；Requirements 2）。

Authorizer 复用 TokenVerifier（传输无关共享校验），供 API Gateway Lambda Authorizer 使用；
未来流式入口（Function URL）可复用同一 Authorizer，保证"认证只做一次、两路径同源"。
"""

from __future__ import annotations

from api_gateway.errors import Unauthorized
from api_gateway.identity import TokenVerifier


class Authorizer:
    def __init__(self, verifier: TokenVerifier) -> None:
        self._verifier = verifier

    def authorize(self, authorization_header: str | None) -> dict:
        """校验 bearer token，返回已校验 claims；缺失/非法 → Unauthorized。"""
        if not authorization_header:
            raise Unauthorized("缺少 Authorization 头")
        parts = authorization_header.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
            raise Unauthorized("Authorization 头格式非法")
        return self._verifier.verify(parts[1].strip())  # 非法 token 抛 Unauthorized
