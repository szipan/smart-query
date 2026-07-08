"""DynamoDbCounterStore 单测：用内存版 DynamoDB mock 验证原子条件自增语义。

同时用属性测试验证「不变量：counter 永不超过 limit」，与内存实现语义一致。
"""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from adapters.dynamodb import DynamoDbCounterStore


class ConditionalCheckFailedException(Exception):
    """模拟 botocore 生成的同名异常。"""


class FakeDynamoClient:
    """内存模拟 DynamoDB 的条件自增 + get_item（单线程，逐调用原子）。"""

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}
        self.update_calls: list[dict] = []

    def update_item(self, **kwargs):
        self.update_calls.append(kwargs)
        pk = kwargs["Key"]["pk"]["S"]
        limit = int(kwargs["ExpressionAttributeValues"][":limit"]["N"])
        ttl = kwargs["ExpressionAttributeValues"][":ttl"]["N"]
        cur = int(self.items.get(pk, {}).get("count", {}).get("N", "0"))
        # ConditionExpression: attribute_not_exists(#c) OR #c < :limit
        exists = pk in self.items and "count" in self.items[pk]
        if exists and not (cur < limit):
            raise ConditionalCheckFailedException("conditional request failed")
        new_count = cur + 1
        self.items[pk] = {"count": {"N": str(new_count)}, "ttl": {"N": ttl}}
        return {"Attributes": {"count": {"N": str(new_count)}}}

    def get_item(self, **kwargs):
        pk = kwargs["Key"]["pk"]["S"]
        item = self.items.get(pk)
        return {"Item": item} if item else {}


def _store(client=None) -> DynamoDbCounterStore:
    return DynamoDbCounterStore(table_name="t", client=client or FakeDynamoClient())


def test_increments_until_limit_then_blocks() -> None:
    store = _store()
    assert store.incr_if_below("k", 2, 111) == (True, 1)
    assert store.incr_if_below("k", 2, 111) == (True, 2)
    # 达到上限：拒绝，返回当前计数
    assert store.incr_if_below("k", 2, 111) == (False, 2)


def test_update_expression_and_ttl_written() -> None:
    client = FakeDynamoClient()
    _store(client).incr_if_below("USER#u#2026-07-08", 5, 1893456000)
    call = client.update_calls[0]
    assert call["TableName"] == "t"
    assert call["ExpressionAttributeNames"]["#c"] == "count"
    assert call["ExpressionAttributeValues"][":ttl"]["N"] == "1893456000"
    assert "if_not_exists" in call["UpdateExpression"]
    assert client.items["USER#u#2026-07-08"]["ttl"]["N"] == "1893456000"


def test_limit_zero_blocks_immediately() -> None:
    # 首次写入 count=1，条件 attribute_not_exists 成立 -> 允许；第二次 1<0 为假 -> 拒绝
    store = _store()
    assert store.incr_if_below("k", 0, 1)[0] is True
    assert store.incr_if_below("k", 0, 1) == (False, 1)


def test_conditional_failure_via_error_code_shape() -> None:
    class ClientErrLike(Exception):
        def __init__(self):
            super().__init__("x")
            self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}

    class Client(FakeDynamoClient):
        def update_item(self, **kwargs):
            raise ClientErrLike()

    # 无条目时读计数返回 0
    assert _store(Client()).incr_if_below("k", 3, 1) == (False, 0)


def test_non_conditional_error_reraised() -> None:
    class Boom(Exception):
        pass

    class Client(FakeDynamoClient):
        def update_item(self, **kwargs):
            raise Boom("network")

    with pytest.raises(Boom):
        _store(Client()).incr_if_below("k", 3, 1)


@given(limit=st.integers(min_value=1, max_value=20), attempts=st.integers(min_value=0, max_value=60))
def test_property_never_exceeds_limit(limit: int, attempts: int) -> None:
    store = _store()
    allowed_count = 0
    for _ in range(attempts):
        allowed, _n = store.incr_if_below("k", limit, 1)
        if allowed:
            allowed_count += 1
    assert allowed_count == min(attempts, limit)
