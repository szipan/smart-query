"""创建 knowledge-admin 所需 DynamoDB 表（PAY_PER_REQUEST）。

- knowledge_glossary       PK=pk(term_id)
- knowledge_glossary_name  PK=name_key（软通道 term 唯一性名占用）
- knowledge_entity         PK=pk(business_key = entity_type#entity_id)
- knowledge_alias_index    PK=alias_key（硬通道别名全局唯一）

幂等：已存在则跳过。
用法：  python scripts/create_knowledge_tables.py [--region us-west-2]
"""

from __future__ import annotations

import argparse
import os

import boto3
from botocore.exceptions import ClientError

TABLES = [
    ("SMARTQUERY_GLOSSARY_TABLE", "knowledge_glossary", "pk"),
    ("SMARTQUERY_GLOSSARY_NAME_TABLE", "knowledge_glossary_name", "name_key"),
    ("SMARTQUERY_ENTITY_TABLE", "knowledge_entity", "pk"),
    ("SMARTQUERY_ALIAS_TABLE", "knowledge_alias_index", "alias_key"),
]


def ensure_table(ddb, name: str, pk: str) -> None:
    try:
        ddb.create_table(
            TableName=name,
            AttributeDefinitions=[{"AttributeName": pk, "AttributeType": "S"}],
            KeySchema=[{"AttributeName": pk, "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        print(f"[create] {name} (PK={pk}) 创建中…")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceInUseException":
            print(f"[skip] {name}: 已存在")
            return
        raise
    ddb.get_waiter("table_exists").wait(TableName=name)
    print(f"[ok] {name}: ACTIVE")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    args = p.parse_args()
    ddb = boto3.client("dynamodb", region_name=args.region)
    for env_key, default, pk in TABLES:
        ensure_table(ddb, os.environ.get(env_key, default), pk)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
