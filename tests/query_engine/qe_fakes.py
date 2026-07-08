"""Layer 3 测试用 mock 依赖。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


class FakeMemory:
    def __init__(self, turns: Optional[list[dict]] = None, fail_read: bool = False,
                 fail_write: bool = False):
        self._turns = turns or []
        self._fail_read = fail_read
        self._fail_write = fail_write
        self.written: list[tuple] = []

    def read_recent_turns(self, actor_id, session_id, limit):
        if self._fail_read:
            raise RuntimeError("memory down")
        return self._turns[:limit]

    def write_turn(self, actor_id, session_id, question, answer_summary):
        if self._fail_write:
            raise RuntimeError("write fail")
        self.written.append((actor_id, session_id, question, answer_summary))


class FakeLlm:
    def __init__(self, out: str = "独立问题：本月各电站发电量"):
        self._out = out
        self.calls: list[dict] = []

    def complete(self, *, system: str, prompt: str, max_tokens: int = 1024, temperature: float = 0.0) -> str:
        self.calls.append({"system": system, "prompt": prompt})
        return self._out


class FakeEmbedder:
    def embed(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeVectorStore:
    def __init__(self, data: Optional[dict] = None):
        self.data = data or {}
        self.upserts: list[tuple] = []
        self.deletes: list[tuple] = []

    def similarity_search(self, vector, top_k, namespace):
        return list(self.data.get(namespace, []))[:top_k]

    def upsert(self, namespace, entry):
        self.upserts.append((namespace, entry))

    def delete(self, namespace, entry_id):
        self.deletes.append((namespace, entry_id))


class FakeEntityDictionary:
    def __init__(self, hits: Optional[list[dict]] = None):
        self._hits = hits or []

    def match(self, question):
        return list(self._hits)


@dataclass
class FakeL4Result:
    status: str
    unvalidated_sql: Optional[str]


class FakeLayer4Client:
    def __init__(self, status: str = "ok",
                 sql: str = "SELECT ps_id FROM fact_power_station_kpi_month"):
        self._status = status
        self._sql = sql
        self.last_request = None

    def generate(self, request):
        self.last_request = request
        return FakeL4Result(self._status, self._sql if self._status == "ok" else None)


@dataclass
class FakeGuardResult:
    status: str
    safe_sql: Optional[str]


class FakeGuard:
    def __init__(self, status: str = "allowed", safe_sql: str = "SELECT ... /*isolated*/"):
        self._status = status
        self._safe = safe_sql
        self.calls: list[tuple] = []

    def guard(self, sql, request_id):
        self.calls.append((sql, request_id))
        return FakeGuardResult(self._status, self._safe if self._status == "allowed" else None)


class FakeGuardFactory:
    def __init__(self, guard: Optional[FakeGuard] = None):
        self._g = guard or FakeGuard()
        self.identities: list[Any] = []

    def for_identity(self, identity):
        self.identities.append(identity)
        return self._g
