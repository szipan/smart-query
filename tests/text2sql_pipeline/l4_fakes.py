"""可复用的 mock 依赖（供 conftest fixture 与各测试直接 import）。"""

from __future__ import annotations

from typing import Any


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeVectorStore:
    def __init__(self, schema=None, examples=None, semantic=None):
        self._data = {
            "schema": schema if schema is not None else [
                {"text": "CREATE TABLE fact_power_station_kpi_month (ps_id INT, p83022 DECIMAL)"}
            ],
            "examples": examples if examples is not None else [
                {"question": "本月发电量", "sql": "SELECT ps_id, p83022 FROM fact_power_station_kpi_month"}
            ],
            "semantic_layer": semantic if semantic is not None else [],
        }

    def similarity_search(self, vector, top_k: int, namespace: str) -> list[dict[str, Any]]:
        return list(self._data.get(namespace, []))[:top_k]


class FakeLlm:
    def __init__(self, sql: str = "SELECT ps_id, p83022 FROM fact_power_station_kpi_month WHERE date_id = 20260501"):
        self._sql = sql
        self.calls: list[dict] = []

    def complete(self, *, model_id: str, system: str, prompt: str,
                 max_tokens: int = 2048, temperature: float = 0.0) -> str:
        self.calls.append({"model_id": model_id, "system": system, "prompt": prompt})
        return f"```sql\n{self._sql}\n```"


class FakeDatasource:
    dialect = "starrocks"

    def dry_run(self, sql: str) -> None:  # 本期不使用
        raise NotImplementedError("本期不执行 SQL")

    def get_schema_snippet(self, table_names: list[str]) -> list[str]:
        return []
