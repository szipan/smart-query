"""删除 smart-query 项目创建的全部 AWS 测试资源（节省成本）。

删除顺序（依赖关系由后往前）：
1. OpenSearch Serverless 数据访问 / 网络 / 加密策略
2. OpenSearch Serverless 集合
3. DynamoDB 表（限流表 + 4 张知识表）
4. Bedrock AgentCore Memory

幂等：资源不存在则跳过。
安全：执行前列出将删除的资源并要求确认（除非 --yes）。

恢复：所有资源均可通过 scripts/ 下的创建脚本重建（见 scripts/README_recreate.md）。

用法：  python scripts/teardown_all.py [--region us-west-2] [--yes]
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import boto3
from botocore.exceptions import ClientError


def _env(name: str, default: str) -> str:
    val = os.environ.get(name)
    return val if val not in (None, "") else default


# ─── 配置（与 .env.example 对齐）───────────────────────────────────────────────

def get_config(region: str) -> dict:
    return {
        "region": region,
        "aoss_name": _env("SMARTQUERY_AOSS_NAME", "smart-query"),
        "ddb_tables": [
            _env("SMARTQUERY_DDB_TABLE", "api_gateway_rate_limit"),
            _env("SMARTQUERY_GLOSSARY_TABLE", "knowledge_glossary"),
            _env("SMARTQUERY_GLOSSARY_NAME_TABLE", "knowledge_glossary_name"),
            _env("SMARTQUERY_ENTITY_TABLE", "knowledge_entity"),
            _env("SMARTQUERY_ALIAS_TABLE", "knowledge_alias_index"),
        ],
        "memory_id": _env("SMARTQUERY_MEMORY_ID", ""),
    }


# ─── 删除函数 ───────────────────────────────────────────────────────────────────

def delete_aoss_policies(aoss, name: str) -> None:
    """删除 AOSS 关联的安全策略和数据访问策略。"""
    # 数据访问策略
    policy_name = f"{name}-data"[:32]
    try:
        aoss.delete_access_policy(name=policy_name, type="data")
        print(f"  [deleted] 数据访问策略: {policy_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"  [skip] 数据访问策略 {policy_name} 不存在")
        else:
            print(f"  [warn] 删除数据访问策略失败: {e}")

    # 网络策略
    policy_name = f"{name}-net"[:32]
    try:
        aoss.delete_security_policy(name=policy_name, type="network")
        print(f"  [deleted] 网络策略: {policy_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"  [skip] 网络策略 {policy_name} 不存在")
        else:
            print(f"  [warn] 删除网络策略失败: {e}")

    # 加密策略（必须在集合删除后才能删，先记录，后面处理）
    # AOSS 要求：集合存在时加密策略不可删；集合删除后才可以。
    # 这里先跳过，在集合删除后再删。


def delete_aoss_collection(aoss, name: str) -> None:
    """删除 AOSS 集合（会先等集合变 DELETING/不存在）。"""
    try:
        # 查集合 id
        resp = aoss.batch_get_collection(names=[name])
        details = resp.get("collectionDetails", [])
        if not details:
            print(f"  [skip] AOSS 集合 '{name}' 不存在")
            return
        coll_id = details[0]["id"]
        aoss.delete_collection(id=coll_id)
        print(f"  [deleting] AOSS 集合 '{name}' (id={coll_id})...")

        # 等删除完成
        for _ in range(60):
            got = aoss.batch_get_collection(ids=[coll_id])
            remaining = got.get("collectionDetails", [])
            if not remaining:
                print(f"  [deleted] AOSS 集合 '{name}' 已删除")
                return
            status = remaining[0].get("status", "")
            if status == "DELETING":
                time.sleep(10)
                continue
            # 其它异常状态
            time.sleep(10)
        print(f"  [warn] 等待集合删除超时，请手动确认")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"  [skip] AOSS 集合 '{name}' 不存在")
        else:
            raise


def delete_aoss_encryption_policy(aoss, name: str) -> None:
    """删除加密策略（须在集合删除后调用）。"""
    policy_name = f"{name}-enc"[:32]
    try:
        aoss.delete_security_policy(name=policy_name, type="encryption")
        print(f"  [deleted] 加密策略: {policy_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"  [skip] 加密策略 {policy_name} 不存在")
        else:
            print(f"  [warn] 删除加密策略失败: {e}")


def delete_dynamodb_tables(region: str, tables: list[str]) -> None:
    """删除 DynamoDB 表。"""
    ddb = boto3.client("dynamodb", region_name=region)
    for table in tables:
        try:
            ddb.delete_table(TableName=table)
            print(f"  [deleting] DynamoDB 表: {table}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                print(f"  [skip] DynamoDB 表 {table} 不存在")
            else:
                print(f"  [error] 删除 {table} 失败: {e}")

    # 等待所有表删除完成
    for table in tables:
        try:
            waiter = ddb.get_waiter("table_not_exists")
            waiter.wait(TableName=table, WaiterConfig={"Delay": 5, "MaxAttempts": 30})
            print(f"  [deleted] DynamoDB 表: {table}")
        except Exception:
            pass  # 可能本来就不存在


def delete_agentcore_memory(region: str, memory_id: str) -> None:
    """删除 Bedrock AgentCore Memory。"""
    if not memory_id:
        print("  [skip] 未配置 SMARTQUERY_MEMORY_ID，跳过")
        return
    try:
        ctl = boto3.client("bedrock-agentcore-control", region_name=region)
        ctl.delete_memory(memoryId=memory_id)
        print(f"  [deleted] AgentCore Memory: {memory_id}")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("ResourceNotFoundException", "ValidationException"):
            print(f"  [skip] AgentCore Memory {memory_id} 不存在或已删除")
        else:
            print(f"  [error] 删除 Memory 失败: {e}")


# ─── 主流程 ──────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description="删除 smart-query 全部 AWS 测试资源")
    p.add_argument("--region", default=_env("AWS_REGION", "us-west-2"))
    p.add_argument("--yes", "-y", action="store_true", help="跳过确认直接删除")
    args = p.parse_args()

    cfg = get_config(args.region)

    # 打印将删除的资源
    print("=" * 60)
    print("即将删除以下 AWS 资源（区域: {})：".format(cfg["region"]))
    print("=" * 60)
    print(f"\n[OpenSearch Serverless]")
    print(f"  集合: {cfg['aoss_name']}")
    print(f"  策略: {cfg['aoss_name']}-enc, {cfg['aoss_name']}-net, {cfg['aoss_name']}-data")
    print(f"\n[DynamoDB 表]")
    for t in cfg["ddb_tables"]:
        print(f"  {t}")
    print(f"\n[Bedrock AgentCore Memory]")
    print(f"  {cfg['memory_id'] or '(未配置)'}")
    print("\n" + "=" * 60)

    if not args.yes:
        confirm = input("\n确认删除以上全部资源？(yes/no): ").strip().lower()
        if confirm != "yes":
            print("已取消。")
            return 0

    print("\n开始删除...\n")

    # 1. 删除 AOSS 策略（数据访问 + 网络；加密需集合删后）
    print("[1/4] 删除 AOSS 策略（数据访问 + 网络）...")
    aoss = boto3.client("opensearchserverless", region_name=cfg["region"])
    delete_aoss_policies(aoss, cfg["aoss_name"])

    # 2. 删除 AOSS 集合
    print("\n[2/4] 删除 AOSS 集合...")
    delete_aoss_collection(aoss, cfg["aoss_name"])

    # 删除加密策略（集合删除后）
    print("\n[2.5/4] 删除 AOSS 加密策略...")
    delete_aoss_encryption_policy(aoss, cfg["aoss_name"])

    # 3. 删除 DynamoDB 表
    print("\n[3/4] 删除 DynamoDB 表...")
    delete_dynamodb_tables(cfg["region"], cfg["ddb_tables"])

    # 4. 删除 AgentCore Memory
    print("\n[4/4] 删除 AgentCore Memory...")
    delete_agentcore_memory(cfg["region"], cfg["memory_id"])

    print("\n" + "=" * 60)
    print("全部资源已删除（或确认不存在）。")
    print("恢复指南见：scripts/README_recreate.md")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
