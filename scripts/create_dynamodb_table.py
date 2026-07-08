"""创建限流计数表 api_gateway_rate_limit（PAY_PER_REQUEST + TTL）。

幂等：表已存在则跳过创建，仅确保 TTL 已开启。
用法：  python scripts/create_dynamodb_table.py [--table NAME] [--region REGION]
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import boto3
from botocore.exceptions import ClientError


def ensure_table(table_name: str, region: str, pk_attr: str = "pk", ttl_attr: str = "ttl") -> None:
    ddb = boto3.client("dynamodb", region_name=region)

    try:
        ddb.create_table(
            TableName=table_name,
            AttributeDefinitions=[{"AttributeName": pk_attr, "AttributeType": "S"}],
            KeySchema=[{"AttributeName": pk_attr, "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        print(f"[create] {table_name}: 创建中…")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceInUseException":
            print(f"[skip] {table_name}: 已存在")
        else:
            raise

    # 等待 ACTIVE
    waiter = ddb.get_waiter("table_exists")
    waiter.wait(TableName=table_name)
    for _ in range(30):
        desc = ddb.describe_table(TableName=table_name)["Table"]
        if desc["TableStatus"] == "ACTIVE":
            break
        time.sleep(2)
    print(f"[ok] {table_name}: ACTIVE")

    # 开启 TTL（幂等）
    try:
        ttl_desc = ddb.describe_time_to_live(TableName=table_name)
        status = ttl_desc["TimeToLiveDescription"]["TimeToLiveStatus"]
        if status in ("ENABLED", "ENABLING"):
            print(f"[skip] TTL 已 {status}（属性 {ttl_attr}）")
            return
        ddb.update_time_to_live(
            TableName=table_name,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": ttl_attr},
        )
        print(f"[ok] TTL 已开启（属性 {ttl_attr}）")
    except ClientError as e:
        print(f"[warn] TTL 设置失败: {e}", file=sys.stderr)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--table", default=os.environ.get("SMARTQUERY_DDB_TABLE", "api_gateway_rate_limit"))
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    args = p.parse_args()
    ensure_table(args.table, args.region)
