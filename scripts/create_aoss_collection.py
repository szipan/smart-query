"""创建 OpenSearch Serverless 向量集合（fewest resources + 最小授权范围）。

安全设计（呼应「不开放 0.0.0.0/0、控制范围、不留空认证」）：
- AOSS 无安全组；访问一律 IAM/SigV4，无匿名访问、无密码留空。
- 加密策略：使用 AWS 托管密钥（不额外建 KMS 资源）。
- 网络策略：AllowFromPublic=true —— 集合端点可从本机访问（本地演示所需），
  但**数据访问策略仅授予单一 IAM 主体**（当前调用者），非任意人可读写。
  如需彻底不暴露公网，改用 VPC-only（需建 VPC 接口端点，且演示须在 VPC 内运行）。
- 数据访问策略：Principal 限定为当前 STS 调用者，权限限定为 ingest/检索所需集合与索引操作。

用法：  python scripts/create_aoss_collection.py [--name smart-query] [--region us-west-2]
输出：集合的 collectionEndpoint（写入 .env 的 SMARTQUERY_OPENSEARCH_ENDPOINT）。
"""

from __future__ import annotations

import argparse
import json
import os
import time

import boto3
from botocore.exceptions import ClientError

COLLECTION_PERMS = [
    "aoss:CreateCollectionItems",
    "aoss:DescribeCollectionItems",
    "aoss:UpdateCollectionItems",
]
INDEX_PERMS = [
    "aoss:CreateIndex",
    "aoss:DescribeIndex",
    "aoss:ReadDocument",
    "aoss:WriteDocument",
    "aoss:UpdateIndex",
    "aoss:DeleteIndex",
]


def _principal_arn(region: str) -> str:
    arn = boto3.client("sts", region_name=region).get_caller_identity()["Arn"]
    # assumed-role 会话 arn -> 角色 arn（数据访问策略需角色/用户 arn）
    if ":assumed-role/" in arn:
        parts = arn.split(":")
        acct = parts[4]
        role = arn.split("assumed-role/")[1].split("/")[0]
        return f"arn:aws:iam::{acct}:role/{role}"
    return arn


def _create_policy(aoss, *, name, ptype, policy, kind):
    """kind: 'security' | 'access'。已存在则跳过。"""
    try:
        if kind == "security":
            aoss.create_security_policy(name=name, type=ptype, policy=json.dumps(policy))
        else:
            aoss.create_access_policy(name=name, type=ptype, policy=json.dumps(policy))
        print(f"[create] {kind} policy '{name}' ({ptype})")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("ConflictException",):
            print(f"[skip] {kind} policy '{name}' 已存在")
        else:
            raise


def _network_rule(coll_res: str, vpce: str | None) -> dict:
    rule = {"Rules": [
        {"ResourceType": "collection", "Resource": [coll_res]},
        {"ResourceType": "dashboard", "Resource": [coll_res]},
    ]}
    if vpce:
        rule["SourceVPCEs"] = [vpce]  # VPC-only：仅经该 VPC 端点访问，不暴露公网
    else:
        rule["AllowFromPublic"] = True
    return rule


def create_collection(name: str, region: str, vpce: str | None = None) -> str:
    aoss = boto3.client("opensearchserverless", region_name=region)
    principal = _principal_arn(region)
    print(f"[info] 数据访问主体限定为: {principal}")

    coll_res = f"collection/{name}"

    # 1) 加密策略（AWS 托管密钥）
    _create_policy(
        aoss, name=f"{name}-enc"[:32], ptype="encryption", kind="security",
        policy={"Rules": [{"ResourceType": "collection", "Resource": [coll_res]}],
                "AWSOwnedKey": True},
    )
    # 2) 网络策略：优先 VPC-only（不暴露公网，控制范围）；未提供 vpce 才退回公网
    _create_policy(
        aoss, name=f"{name}-net"[:32], ptype="network", kind="security",
        policy=[_network_rule(coll_res, vpce)],
    )
    # 3) 数据访问策略（仅当前主体，最小必要权限）
    _create_policy(
        aoss, name=f"{name}-data"[:32], ptype="data", kind="access",
        policy=[{"Rules": [
            {"ResourceType": "collection", "Resource": [coll_res], "Permission": COLLECTION_PERMS},
            {"ResourceType": "index", "Resource": [f"index/{name}/*"], "Permission": INDEX_PERMS},
        ], "Principal": [principal]}],
    )

    # 4) 创建集合
    try:
        aoss.create_collection(name=name, type="VECTORSEARCH",
                               description="smart-query 向量库（schema/examples/cache/glossary）")
        print(f"[create] collection '{name}' (VECTORSEARCH) 创建中…")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConflictException":
            print(f"[skip] collection '{name}' 已存在")
        else:
            raise

    # 5) 轮询直到 ACTIVE
    endpoint = ""
    for _ in range(60):
        got = aoss.batch_get_collection(names=[name])
        details = got.get("collectionDetails", [])
        if details:
            d = details[0]
            status = d.get("status")
            if status == "ACTIVE":
                endpoint = d.get("collectionEndpoint", "")
                print(f"[ok] collection ACTIVE: {endpoint}")
                break
            if status == "FAILED":
                raise RuntimeError(f"collection 创建失败: {d}")
        time.sleep(10)
    else:
        raise TimeoutError("等待集合 ACTIVE 超时")
    return endpoint


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--name", default=os.environ.get("SMARTQUERY_AOSS_NAME", "smart-query"))
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    p.add_argument("--vpce", default=os.environ.get("SMARTQUERY_AOSS_VPCE"),
                   help="AOSS VPC 端点 id（提供则 VPC-only，不暴露公网）")
    args = p.parse_args()
    ep = create_collection(args.name, args.region, args.vpce)
    print("\nSMARTQUERY_OPENSEARCH_ENDPOINT=" + ep)
