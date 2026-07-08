"""出站客户端契约（design.md §3.6/§3.7；Requirements 6/7）。

EngineClient 调 EKS query-engine；LegacyClient 委托 legacy-router。均透传身份。
L2 只判断+转发+计数，不实现下游语义。真实实现走 HTTPS；测试用 fake / 进程内适配。
"""

from __future__ import annotations

from typing import Any, Protocol

from api_gateway.envelope import QueryRequest
from api_gateway.identity import IdentityContext


class EngineClient(Protocol):
    def invoke(self, req: QueryRequest, identity: IdentityContext) -> Any:
        """调 query-engine，透传 identity + request_id；返回下游数据（供整形）。

        下游超时/5xx/连接失败 → 抛 DownstreamError。
        """
        ...


class LegacyClient(Protocol):
    def invoke(self, req: QueryRequest, identity: IdentityContext, rule_id: str) -> Any:
        """命中 Legacy 规则时委托 legacy-router 执行，透传身份。"""
        ...
