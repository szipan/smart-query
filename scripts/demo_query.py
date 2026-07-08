"""端到端演示：一条自然语言问题走完 L2→L3→L4→L5，打印隔离后的 safe_sql。

用真实 Bedrock（生成/向量化）+ OpenSearch（检索）+ DynamoDB（限流）。
用法：  python scripts/demo_query.py ["自定义问题"]
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from adapters.settings import AwsSettings
from api_gateway.handler import GatewayRequest
from wiring import build_gateway

DEFAULT_QUESTIONS = [
    "本月各电站发电量",
    "查询所有电站的装机容量",
]


def run_once(gw, question: str, user_id: str, request_id: str) -> None:
    print("\n" + "=" * 70)
    print(f"Q: {question}  (user={user_id}, request_id={request_id})")
    resp = gw.handle(GatewayRequest(
        body={"question": question},
        authorizer_claims={"sub": user_id},
        request_id=request_id,
    ))
    print(f"HTTP {resp.status_code}")
    print(json.dumps(resp.body, ensure_ascii=False, indent=2))


def main() -> int:
    s = AwsSettings.from_env()
    if not s.opensearch_endpoint:
        print("ERROR: 未设置 SMARTQUERY_OPENSEARCH_ENDPOINT", file=sys.stderr)
        return 2

    print(f"[config] region={s.region} llm={s.llm_model_id}")
    print(f"[config] embed={s.embed_model_id}({s.embed_dimensions}) index={s.opensearch_index} ddb={s.ddb_table}")

    gw = build_gateway(s)

    questions = [" ".join(sys.argv[1:])] if len(sys.argv) > 1 else DEFAULT_QUESTIONS
    for i, q in enumerate(questions):
        run_once(gw, q, user_id="1001", request_id=f"demo-{i}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
