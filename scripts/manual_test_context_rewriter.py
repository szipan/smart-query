"""手动实测指代消解（ContextRewriter），用真实 Bedrock LLM + 真实/进程内 Memory。

用法：
  1) 预设多轮对话自动跑：
     python scripts/manual_test_context_rewriter.py

  2) 交互式手动输入（一轮一轮自己打字）：
     python scripts/manual_test_context_rewriter.py --interactive

环境变量：
  AWS_REGION                     默认 us-west-2
  SMARTQUERY_LLM_MODEL_ID        默认 us.anthropic.claude-haiku-4-5-20251001-v1:0
  SMARTQUERY_MEMORY_ID           若设置，用真实 AgentCore Memory；否则用进程内 InMemoryMemory
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from adapters.bedrock import BedrockLlmClient
from adapters.settings import AwsSettings
from query_engine.models import CallerIdentity, QueryContext, QueryRequest
from query_engine.nodes.context_rewriter import ContextRewriter
from wiring import InMemoryMemory, build_memory

DEFAULT_TURNS = [
    "本月各电站发电量",
    "那上个月呢",
    "它的装机容量是多少",
    "跟去年同期比怎么样",
]


def run_turn(rewriter: ContextRewriter, memory, user_id: str, session_id: str, raw_question: str) -> str:
    ctx = QueryContext(request=QueryRequest(
        request_id="manual", identity=CallerIdentity(user_id=user_id),
        raw_question=raw_question, session_id=session_id,
    ))
    rewriter.run(ctx)
    print(f"\n原始追问   : {raw_question}")
    print(f"改写后问题 : {ctx.standalone_question}")
    if ctx.memory_degraded:
        print("  [警告] Memory 读取失败，已降级")
    if ctx.warnings:
        for w in ctx.warnings:
            print(f"  [warning] {w}")
    # 写回本轮：answer_summary 留空（本脚本只测改写，不执行 SQL/生成答案）
    memory.write_turn(user_id, session_id, raw_question, "")
    return ctx.standalone_question


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--interactive", action="store_true", help="交互式手动输入每轮问题")
    p.add_argument("--user", default="manual-user")
    p.add_argument("--session", default="manual-session")
    args = p.parse_args()

    settings = AwsSettings.from_env()
    print(f"[config] region={settings.region} llm={settings.llm_model_id}")

    llm = BedrockLlmClient(region=settings.region, default_model_id=settings.llm_model_id)
    memory = build_memory(settings)
    print(f"[memory] 使用 {'AgentCoreMemory' if not isinstance(memory, InMemoryMemory) else 'InMemoryMemory(进程内，非持久)'}")

    rewriter = ContextRewriter(memory, llm)

    if args.interactive:
        print("\n交互模式：逐行输入问题，Ctrl+C / 空行退出。\n")
        while True:
            try:
                q = input("你问: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not q:
                break
            run_turn(rewriter, memory, args.user, args.session, q)
    else:
        print(f"\n自动跑 {len(DEFAULT_TURNS)} 轮预设对话（session={args.session}）：")
        for q in DEFAULT_TURNS:
            run_turn(rewriter, memory, args.user, args.session, q)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
