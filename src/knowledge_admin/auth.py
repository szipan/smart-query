"""管理员鉴权（占位实现，design.md §8）。

本期占位：默认放行（actor 从 Authorization 头解析或用占位值）；也可配置一个 admin token
做最简校验，便于本地演示。接口 require_admin 稳定，未来替换为阳光云 OAuth 的 admin
角色/scope 校验时不改路由与 Service。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from knowledge_admin.errors import Forbidden, Unauthorized


@dataclass(frozen=True)
class AdminPrincipal:
    actor_id: str


def _parse_bearer(auth_header: Optional[str]) -> Optional[str]:
    if not auth_header:
        return None
    parts = auth_header.split()
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
        return parts[1].strip()
    return None


class PlaceholderAuthGuard:
    """占位鉴权。

    - admin_token 为 None（默认）：放行；actor 取自 Bearer token，缺省为 'placeholder-admin'。
    - admin_token 非 None：要求请求携带 `Bearer <admin_token>`，否则拒绝（演示用最简校验）。
    """

    def __init__(self, admin_token: Optional[str] = None) -> None:
        self._token = admin_token

    def require_admin(self, auth_header: Optional[str]) -> AdminPrincipal:
        token = _parse_bearer(auth_header)
        if self._token is None:
            return AdminPrincipal(actor_id=token or "placeholder-admin")
        if token is None:
            raise Unauthorized("缺少或非法的 Authorization 头")
        if token != self._token:
            raise Forbidden("非管理员")
        return AdminPrincipal(actor_id="admin")
