"""创建 Bedrock AgentCore Memory（仅短期 events，最小资源；不建长期策略/角色）。

用法：  python scripts/create_agentcore_memory.py [--name smart-query-memory]
                                                 [--expiry-days 30] [--region us-west-2]
输出：memory id（写入 .env 的 SMARTQUERY_MEMORY_ID）。
幂等：同名已存在则复用其 id。
"""

from __future__ import annotations

import argparse
import os
import time

import boto3
from botocore.exceptions import ClientError


def _find_existing(ctl, name: str):
    token = None
    while True:
        kw = {"maxResults": 100}
        if token:
            kw["nextToken"] = token
        resp = ctl.list_memories(**kw)
        for m in resp.get("memories", []):
            mid = m.get("id") or m.get("memoryId")
            if (m.get("name") == name) or (mid and mid.startswith(name)):
                return mid
        token = resp.get("nextToken")
        if not token:
            return None


def create_memory(name: str, expiry_days: int, region: str) -> str:
    ctl = boto3.client("bedrock-agentcore-control", region_name=region)

    existing = _find_existing(ctl, name)
    if existing:
        print(f"[skip] 已存在同名 memory: {existing}")
        mid = existing
    else:
        resp = ctl.create_memory(name=name, eventExpiryDuration=expiry_days,
                                 description="smart-query 多轮会话短期记忆")
        mid = resp["memory"]["id"]
        print(f"[create] memory {mid} 创建中…")

    # 轮询 ACTIVE
    for _ in range(60):
        m = ctl.get_memory(memoryId=mid)["memory"]
        status = m.get("status")
        if status == "ACTIVE":
            print(f"[ok] memory ACTIVE: {mid}")
            return mid
        if status == "FAILED":
            raise RuntimeError(f"memory 创建失败: {m.get('failureReason')}")
        time.sleep(5)
    raise TimeoutError("等待 memory ACTIVE 超时")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--name", default=os.environ.get("SMARTQUERY_MEMORY_NAME", "smart_query_memory"))
    p.add_argument("--expiry-days", type=int, default=int(os.environ.get("SMARTQUERY_MEMORY_EXPIRY_DAYS", "30")))
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    args = p.parse_args()
    mid = create_memory(args.name, args.expiry_days, args.region)
    print("\nSMARTQUERY_MEMORY_ID=" + mid)
