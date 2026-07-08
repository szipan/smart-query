"""可观测（design.md §11；Requirements 12）。

每编排步骤打结构化日志（含 request_id + 脱敏 user_id）；发出 429 率/下游延迟/路由分布指标。
日志脱敏：不落 raw_claims 明文 / 敏感细节。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


def mask_user_id(user_id: str) -> str:
    """脱敏 user_id（保留前后各 1~2 位）。"""
    if not user_id:
        return ""
    if len(user_id) <= 3:
        return user_id[0] + "*"
    return f"{user_id[:2]}***{user_id[-1]}"


@runtime_checkable
class GatewayObserver(Protocol):
    def on_step(self, step: str, request_id: str, attrs: dict) -> None: ...


class InMemoryGatewayObserver:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict]] = []

    def on_step(self, step: str, request_id: str, attrs: dict) -> None:
        self.records.append((step, request_id, attrs))


class NullGatewayObserver:
    def on_step(self, step: str, request_id: str, attrs: dict) -> None:
        return None
