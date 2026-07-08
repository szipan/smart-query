"""身份构造共享库（传输无关）（design.md §3.5/§5.3/§12.3；Requirements 3/6）。

build_identity 不绑定 API Gateway event 结构，供网关路径与未来流式入口共用。
本层只承载身份，绝不从 raw_claims 解析 org_ids/ps_ids（数据范围属 Layer 5）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from api_gateway.errors import Unauthorized


@dataclass(frozen=True)
class IdentityContext:
    user_id: str
    tenant_id: Optional[str] = None
    scopes: tuple[str, ...] = ()
    raw_claims: dict = field(default_factory=dict)
    request_id: str = ""


def build_identity(claims: Optional[dict], request_id: str) -> IdentityContext:
    """从已校验 claims 构造可信身份。user_id 必须非空，否则 Unauthorized。

    不解析 org_ids/ps_ids —— 那是 Layer 5 的职责（本层只透传 raw_claims）。
    """
    claims = claims or {}
    user_id = claims.get("sub") or claims.get("user_id")
    if not user_id:
        raise Unauthorized("身份缺少 user_id")
    scopes_raw = claims.get("scope") or claims.get("scopes") or ""
    if isinstance(scopes_raw, str):
        scopes = tuple(s for s in scopes_raw.split() if s)
    else:
        scopes = tuple(scopes_raw)
    return IdentityContext(
        user_id=str(user_id),
        tenant_id=claims.get("tenant_id"),
        scopes=scopes,
        raw_claims=dict(claims),
        request_id=request_id,
    )


class TokenVerifier(Protocol):
    """阳光云 OAuth token 校验（传输无关，供 Authorizer 与未来流式入口共用）。

    校验成功返回 claims；失败抛 Unauthorized。真实实现校验 JWT 签名/过期/发行方。
    """

    def verify(self, token: str) -> dict: ...
