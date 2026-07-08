"""DynamoDB 适配器：多实例安全的原子限流计数（实现 L2 ``CounterStore`` Protocol）。

契约：``incr_if_below(pk, limit, ttl_epoch) -> (allowed, new_count)``——当且仅当当前
计数 < limit 时原子自增；否则不自增。用 DynamoDB ``UpdateItem`` +
``ConditionExpression`` 实现单次原子操作，天然支持多 Lambda/多实例并发。

表结构（见 ``scripts/create_dynamodb_table.py``）：
- 分区键（默认属性名 ``pk``，字符串）= ``USER#{user}#{window}``。
- 计数属性（默认 ``count``，数值）。
- TTL 属性（默认 ``ttl``，数值，Unix 秒）——窗口结束后由 DynamoDB 自动清理。

``count`` 是 DynamoDB 保留字，故一律用 ExpressionAttributeNames 占位（``#c`` / ``#t``）。
"""

from __future__ import annotations

from typing import Any, Optional


class DynamoDbCounterStore:
    def __init__(
        self,
        *,
        table_name: str,
        client: Any = None,
        region: Optional[str] = None,
        pk_attr: str = "pk",
        count_attr: str = "count",
        ttl_attr: str = "ttl",
    ) -> None:
        self._table = table_name
        self._client = client if client is not None else _make_client(region)
        self._pk = pk_attr
        self._count = count_attr
        self._ttl = ttl_attr

    def incr_if_below(self, pk: str, limit: int, ttl_epoch: int) -> tuple[bool, int]:
        names = {"#c": self._count, "#t": self._ttl}
        values = {
            ":one": {"N": "1"},
            ":zero": {"N": "0"},
            ":limit": {"N": str(limit)},
            ":ttl": {"N": str(ttl_epoch)},
        }
        try:
            resp = self._client.update_item(
                TableName=self._table,
                Key={self._pk: {"S": pk}},
                UpdateExpression="SET #c = if_not_exists(#c, :zero) + :one, #t = :ttl",
                ConditionExpression="attribute_not_exists(#c) OR #c < :limit",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ReturnValues="UPDATED_NEW",
            )
            new_count = int(resp["Attributes"][self._count]["N"])
            return True, new_count
        except Exception as exc:  # noqa: BLE001 —— 仅对条件失败做特判，其余重抛
            if _is_conditional_failure(exc):
                current = self._read_count(pk)
                return False, current
            raise

    def _read_count(self, pk: str) -> int:
        resp = self._client.get_item(
            TableName=self._table,
            Key={self._pk: {"S": pk}},
            ProjectionExpression="#c",
            ExpressionAttributeNames={"#c": self._count},
            ConsistentRead=True,
        )
        item = resp.get("Item")
        if not item or self._count not in item:
            return 0
        return int(item[self._count]["N"])


def _is_conditional_failure(exc: Exception) -> bool:
    """判断异常是否为 ConditionalCheckFailedException（不依赖 botocore 类型导入）。"""
    name = type(exc).__name__
    if name == "ConditionalCheckFailedException":
        return True
    code = getattr(exc, "response", {}) or {}
    return code.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _make_client(region: Optional[str]):
    import boto3  # 延迟导入

    return boto3.client("dynamodb", region_name=region)
