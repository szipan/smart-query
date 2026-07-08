"""单用户业务限流共享库（design.md §3.3/§6；Requirements 4）。

RateLimiter 独立于入口形态（传输无关），网关路径与未来流式路径复用同一实现。
计数存储抽象为 CounterStore：真实实现用 DynamoDB 原子条件自增；测试用内存实现。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from api_gateway.config import DEFAULT_USER_LIMIT


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int = 0
    retry_after: int = 0


class CounterStore(Protocol):
    """原子计数存储。incr_if_below：当且仅当当前计数 < limit 时原子自增。

    返回 (allowed, new_count)。真实实现 = DynamoDB UpdateItem + ConditionExpression。
    """

    def incr_if_below(self, pk: str, limit: int, ttl_epoch: int) -> tuple[bool, int]: ...


class InMemoryCounterStore:
    """内存原子计数（测试用；用锁模拟 DynamoDB 条件自增的原子性）。"""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def incr_if_below(self, pk: str, limit: int, ttl_epoch: int) -> tuple[bool, int]:
        with self._lock:
            cur = self._counts.get(pk, 0)
            if cur >= limit:
                return False, cur
            self._counts[pk] = cur + 1
            return True, cur + 1


def _current_window() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _seconds_to_next_window() -> int:
    now = datetime.now(timezone.utc)
    end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    return max(1, int((end - now).total_seconds()))


class RateLimiter:
    def __init__(self, store: CounterStore, default_limit: int = DEFAULT_USER_LIMIT) -> None:
        self._store = store
        self._default_limit = default_limit

    def _resolve_limit(self, user_id: str) -> int:
        # 未来接用户分级配额服务；本期用默认
        return self._default_limit

    def check_and_increment(self, user_id: str, request_id: str) -> RateLimitDecision:
        window = _current_window()
        pk = f"USER#{user_id}#{window}"
        limit = self._resolve_limit(user_id)
        ttl = 0  # 内存实现忽略；DynamoDB 实现设窗口末 epoch
        allowed, count = self._store.incr_if_below(pk, limit, ttl)
        if not allowed:
            return RateLimitDecision(allowed=False, remaining=0,
                                     retry_after=_seconds_to_next_window())
        return RateLimitDecision(allowed=True, remaining=max(0, limit - count))
