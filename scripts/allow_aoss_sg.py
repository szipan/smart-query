"""为 AOSS VPC 端点的安全组放行入站 443（默认按 VPC CIDR 收敛，绝不 0.0.0.0/0）。

用法：  python scripts/allow_aoss_sg.py --sg sg-xxxx --cidr 172.31.0.0/16 [--region us-west-2]
幂等：规则已存在则跳过。可用 --revoke 撤销同一规则。
"""

from __future__ import annotations

import argparse
import os

import boto3
from botocore.exceptions import ClientError

DESC = "smart-query AOSS access (in-VPC only)"


def _perm(cidr: str) -> list:
    return [{
        "IpProtocol": "tcp", "FromPort": 443, "ToPort": 443,
        "IpRanges": [{"CidrIp": cidr, "Description": DESC}],
    }]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sg", required=True)
    p.add_argument("--cidr", default="172.31.0.0/16")
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    p.add_argument("--revoke", action="store_true")
    args = p.parse_args()

    if args.cidr.strip() in ("0.0.0.0/0", "::/0"):
        print("ERROR: 拒绝开放到全网 (0.0.0.0/0)。请指定收敛的 CIDR。")
        return 2

    ec2 = boto3.client("ec2", region_name=args.region)
    try:
        if args.revoke:
            ec2.revoke_security_group_ingress(GroupId=args.sg, IpPermissions=_perm(args.cidr))
            print(f"[ok] 已撤销 {args.sg} 入站 443 <- {args.cidr}")
        else:
            ec2.authorize_security_group_ingress(GroupId=args.sg, IpPermissions=_perm(args.cidr))
            print(f"[ok] 已放行 {args.sg} 入站 443 <- {args.cidr}")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "InvalidPermission.Duplicate":
            print(f"[skip] 规则已存在：{args.sg} 443 <- {args.cidr}")
        elif code == "InvalidPermission.NotFound":
            print(f"[skip] 规则不存在，无需撤销：{args.sg} 443 <- {args.cidr}")
        else:
            raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
