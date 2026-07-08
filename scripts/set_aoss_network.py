"""把已存在集合的网络策略切换为 VPC-only（经指定 AOSS VPC 端点访问，不暴露公网）。

背景：企业网络将 AOSS 集合端点解析到 VPC 内 PrivateLink 私网 IP，公网策略不可达。
将网络策略绑定到现有 AOSS VPC 端点即可从 VPC/VPN 内访问，且更符合「不暴露公网」的安全要求。

用法：  python scripts/set_aoss_network.py --vpce vpce-xxxx [--name smart-query] [--region us-west-2]
"""

from __future__ import annotations

import argparse
import json
import os

import boto3


def set_vpc_only(name: str, region: str, vpce: str) -> None:
    aoss = boto3.client("opensearchserverless", region_name=region)
    policy_name = f"{name}-net"[:32]
    coll_res = f"collection/{name}"

    cur = aoss.get_security_policy(name=policy_name, type="network")
    version = cur["securityPolicyDetail"]["policyVersion"]

    new_policy = [{
        "Rules": [
            {"ResourceType": "collection", "Resource": [coll_res]},
            {"ResourceType": "dashboard", "Resource": [coll_res]},
        ],
        "SourceVPCEs": [vpce],  # VPC-only：不设 AllowFromPublic
    }]

    aoss.update_security_policy(
        name=policy_name, type="network", policyVersion=version,
        policy=json.dumps(new_policy),
    )
    print(f"[ok] 网络策略 '{policy_name}' 已切换为 VPC-only（SourceVPCEs={vpce}）")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--vpce", required=True)
    p.add_argument("--name", default=os.environ.get("SMARTQUERY_AOSS_NAME", "smart-query"))
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    args = p.parse_args()
    set_vpc_only(args.name, args.region, args.vpce)
