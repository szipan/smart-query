"""Layer 2 测试用 mock 依赖。"""

from __future__ import annotations

from typing import Any

from api_gateway.errors import DownstreamError, Unauthorized


class FakeVerifier:
    """token=='good' → 有效 claims；否则 Unauthorized。"""

    def __init__(self, claims: dict | None = None, valid_token: str = "good"):
        self._claims = claims or {"sub": "1001", "scope": "read"}
        self._valid = valid_token

    def verify(self, token: str) -> dict:
        if token != self._valid:
            raise Unauthorized("token 非法")
        return dict(self._claims)


class FakeEngineClient:
    def __init__(self, data: Any = None, raise_downstream: bool = False):
        self._data = data if data is not None else {"answer": "safe_sql here", "source": "engine"}
        self._raise = raise_downstream
        self.calls: list[tuple] = []

    def invoke(self, req, identity):
        self.calls.append((req, identity))
        if self._raise:
            raise DownstreamError("engine 5xx", http_status=502)
        return self._data


class FakeLegacyClient:
    def __init__(self, data: Any = None):
        self._data = data if data is not None else {"legacy": True}
        self.calls: list[tuple] = []

    def invoke(self, req, identity, rule_id):
        self.calls.append((req, identity, rule_id))
        return self._data
