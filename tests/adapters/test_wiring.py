"""wiring 集成测试：用 mock 的 AWS client 串起 L2→L3→L4→L5 全链路（不连真实服务）。

验证：真实适配器 + 真实四层核心组装后，一条自然语言问题 -> 网关 200 ->
L4 生成 SQL -> L5 注入隔离子查询 -> safe_sql 返回。
"""

from __future__ import annotations

import json

from adapters.settings import AwsSettings
from api_gateway.handler import GatewayRequest
from wiring import (
    InMemoryMemory,
    build_counter_store,
    build_embedder,
    build_engine,
    build_gateway,
    build_llm,
    build_vector_store,
)


# ---- mock AWS clients ----
class FakeConverse:
    def __init__(self, sql: str) -> None:
        self._sql = sql

    def converse(self, **kwargs):
        return {"output": {"message": {"content": [{"text": f"```sql\n{self._sql}\n```"}]}}}


class FakeEmbed:
    def invoke_model(self, modelId, body):
        return {"body": json.dumps({"embedding": [0.1] * 8}).encode("utf-8")}


class FakeOs:
    """按 namespace 返回：schema 给出建表片段；其余（examples/cache/glossary）空 -> 走 pipeline。"""

    def __init__(self) -> None:
        self.indexed = []

    def search(self, index, body):
        ns = body["query"]["knn"]["vector"]["filter"]["term"]["namespace"]
        if ns == "schema":
            return {"hits": {"hits": [{"_id": "s1", "_score": 1.0, "_source": {
                "text": "CREATE TABLE fact_power_station_kpi_month (ps_id INT, p83022 DECIMAL, date_id INT)"}}]}}
        return {"hits": {"hits": []}}

    def index(self, index, body):
        self.indexed.append(body)
        return {"_id": "x"}

    def delete_by_query(self, index, body):
        return {"deleted": 0}


class FakeDynamo:
    def __init__(self) -> None:
        self.items = {}

    def update_item(self, **kwargs):
        pk = kwargs["Key"]["pk"]["S"]
        limit = int(kwargs["ExpressionAttributeValues"][":limit"]["N"])
        cur = int(self.items.get(pk, 0))
        if pk in self.items and not (cur < limit):
            raise _CondFail()
        self.items[pk] = cur + 1
        return {"Attributes": {"count": {"N": str(cur + 1)}}}

    def get_item(self, **kwargs):
        pk = kwargs["Key"]["pk"]["S"]
        if pk in self.items:
            return {"Item": {"count": {"N": str(self.items[pk])}}}
        return {}


class _CondFail(Exception):
    def __init__(self):
        super().__init__("conditional")
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}


def _settings() -> AwsSettings:
    return AwsSettings(
        region="us-west-2", llm_model_id="m", embed_model_id="e", embed_dimensions=8,
        opensearch_endpoint="x", opensearch_index="i", ddb_table="t", user_rate_limit=100,
        memory_id="",
    )


def _gateway_with_sql(sql: str):
    s = _settings()
    embedder = build_embedder(s, client=FakeEmbed())
    vs = build_vector_store(s, client=FakeOs())
    llm = build_llm(s, client=FakeConverse(sql))
    engine = build_engine(s, embedder=embedder, vector_store=vs, llm=llm, memory=InMemoryMemory(),
                          entity_dictionary=None)
    cs = build_counter_store(s, client=FakeDynamo())
    return build_gateway(s, engine=engine, counter_store=cs)


def test_full_chain_produces_isolated_safe_sql() -> None:
    gw = _gateway_with_sql(
        "SELECT ps_id, p83022 FROM fact_power_station_kpi_month WHERE date_id = 20260501")
    resp = gw.handle(GatewayRequest(
        body={"question": "本月各电站发电量"},
        authorizer_claims={"sub": "1001"}, request_id="req-1"))

    assert resp.status_code == 200
    assert resp.body["status"] == "ok"
    assert resp.body["source"] == "query"
    safe_sql = resp.body["data"]["safe_sql"]
    assert "sys_user_ps_share" in safe_sql          # L5 已注入隔离子查询
    assert "ps_id IN (" in safe_sql
    assert resp.body["data"]["sql_source"] == "pipeline"


def test_full_chain_rejects_write_sql() -> None:
    gw = _gateway_with_sql("DROP TABLE fact_power_station_kpi_month")
    resp = gw.handle(GatewayRequest(
        body={"question": "删表"}, authorizer_claims={"sub": "1001"}, request_id="req-2"))
    # L5 拒绝 -> L3 非 ok -> L2 DownstreamError(502) 信封（不泄露内部细节）
    assert resp.status_code == 502
    assert resp.body["error"]["code"] == "DOWNSTREAM_ERROR"


def test_rate_limit_shared_store_blocks_after_limit() -> None:
    s = AwsSettings(region="us-west-2", llm_model_id="m", embed_model_id="e", embed_dimensions=8,
                    opensearch_endpoint="x", opensearch_index="i", ddb_table="t", user_rate_limit=1,
                    memory_id="")
    embedder = build_embedder(s, client=FakeEmbed())
    vs = build_vector_store(s, client=FakeOs())
    llm = build_llm(s, client=FakeConverse("SELECT ps_id FROM fact_power_station_kpi_month"))
    engine = build_engine(s, embedder=embedder, vector_store=vs, llm=llm, memory=InMemoryMemory(),
                          entity_dictionary=None)
    gw = build_gateway(s, engine=engine, counter_store=build_counter_store(s, client=FakeDynamo()))

    r1 = gw.handle(GatewayRequest(body={"question": "q"}, authorizer_claims={"sub": "u"}, request_id="a"))
    r2 = gw.handle(GatewayRequest(body={"question": "q"}, authorizer_claims={"sub": "u"}, request_id="b"))
    assert r1.status_code == 200
    assert r2.status_code == 429
