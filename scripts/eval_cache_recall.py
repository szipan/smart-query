r"""缓存召回评测（向量-only vs 向量+槽位门禁）——手动运行，连真实 Bedrock + AOSS。

方法：
  1. 独立命名空间 ``semantic_cache_eval`` 只写入原问题 Q0 的 canonical 向量 + 槽位指纹（不污染生产缓存）。
  2. 对 positives/negatives 每条：跑 canonical_rewrite 得到 canonical + SemanticSlots，向量检索取 Top-1 与 Q0 的相似度；
     - vector-only 命中 = 相似度≥阈值；
     - gated 命中 = 相似度≥阈值 且 槽位与 Q0 全等（Slot_Gate）。
  3. 汇总：正例召回、反例误命中，在阈值 0.95 与放宽 0.90 两档下，对比 vector-only 与 gated。

前置：AWS 凭证；``AWS_REGION`` / ``SMARTQUERY_OPENSEARCH_ENDPOINT``；术语库已导入（确定性折叠）。
本脚本不入 pytest，也不写生产缓存命名空间。
"""

from __future__ import annotations

import csv
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from adapters.settings import AwsSettings  # noqa: E402
from query_engine.models import CallerIdentity, QueryContext, QueryRequest, slots_equal, SemanticSlots  # noqa: E402
from query_engine.nodes.canonical_rewriter import CanonicalRewriter  # noqa: E402
from wiring import build_embedder, build_llm, build_term_replacer, build_vector_store  # noqa: E402

EVAL_NAMESPACE = os.environ.get("SMARTQUERY_EVAL_NAMESPACE", "semantic_cache_eval")
EVAL_DIR = Path(__file__).resolve().parents[1] / "eval" / "cache_recall"
THRESHOLDS = (0.95, 0.90, 0.85)


def _read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _rewrite(rewriter, q: str):
    req = QueryRequest(request_id="eval", identity=CallerIdentity(user_id="eval"), raw_question=q)
    ctx = QueryContext(request=req)
    ctx.standalone_question = q
    rewriter.run(ctx)
    return ctx.canonical_question, ctx.semantic_slots


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    settings = AwsSettings.from_env()
    if not settings.opensearch_endpoint:
        print("缺少 SMARTQUERY_OPENSEARCH_ENDPOINT")
        return 2

    embedder = build_embedder(settings)
    vs = build_vector_store(settings)
    rewriter = CanonicalRewriter(build_llm(settings), build_term_replacer(settings),
                                 model_id=settings.slot_extraction_model_id or None)

    positives = _read_csv(EVAL_DIR / "positives.csv")
    negatives = _read_csv(EVAL_DIR / "negatives.csv")

    q0 = next(r for r in positives if r.get("类型") == "基准原问题")["变体问法"]
    q0_canon, q0_slots = _rewrite(rewriter, q0)
    print(f"[Q0] canonical: {q0_canon}")
    print(f"[Q0] slots: {q0_slots.to_json() if q0_slots else None}")

    q0_vec = embedder.embed([q0_canon])[0]
    vs.upsert(EVAL_NAMESPACE, {
        "vector": q0_vec,
        "question": q0_canon,
        "pre_isolation_sql": "SELECT ps_id, ps_name, install_capacity FROM ... /*eval Q0*/",
        "slots_json": q0_slots.to_json() if q0_slots else None,
        "entry_id": str(uuid.uuid4()),
    })
    # AOSS 最终一致：轮询直到 Q0 可被检索到（避免早期查询因未建索引而 sim=0）
    for _ in range(30):
        hits = vs.similarity_search(q0_vec, 1, EVAL_NAMESPACE)
        if hits and float(hits[0].get("similarity", hits[0].get("score", 0.0))) > 0.99:
            break
        time.sleep(2)
    else:
        print("[warn] Q0 在 AOSS 未在预期时间内可检索，结果可能偏低")

    def _top1(text):
        vec = embedder.embed([text])[0]
        hits = vs.similarity_search(vec, 1, EVAL_NAMESPACE)
        if not hits:
            return 0.0, None
        top = hits[0]
        sim = float(top.get("similarity", top.get("score", 0.0)))
        return sim, SemanticSlots.from_json(top.get("slots_json"))

    def _eval(rows, label):
        stats = {t: {"vec": 0, "gated": 0} for t in THRESHOLDS}
        n = 0
        print(f"\n==== {label} ====")
        print("序号 | sim | slots匹配 | " + " | ".join(f"vec@{t}/gated@{t}" for t in THRESHOLDS))
        for r in rows:
            if r.get("类型") == "基准原问题":
                continue
            variant = (r.get("变体问法") or r.get("反例问法") or "").strip()
            if not variant:
                continue
            marked = r.get("预期命中", "").strip()
            canon, slots = _rewrite(rewriter, variant)
            sim, cand = _top1(canon)
            match = slots is not None and cand is not None and slots_equal(slots, cand)
            cells = []
            for t in THRESHOLDS:
                vec_hit = sim >= t
                gated_hit = vec_hit and match
                cells.append(f"{vec_hit}/{gated_hit}")
                if marked in ("是", "否"):
                    if vec_hit:
                        stats[t]["vec"] += 1
                    if gated_hit:
                        stats[t]["gated"] += 1
            if marked in ("是", "否"):
                n += 1
            idx = r.get("序号", "?")
            flag = "" if marked in ("是", "否") else " [待确认]"
            print(f"{idx:>3} | {sim:.4f} | {match} | " + " | ".join(cells) + flag)
        return n, stats

    npos, ps = _eval(positives, "正例（期望命中）")
    nneg, ns = _eval(negatives, "反例（期望不命中）")

    print("\n==== 汇总（仅明确标注行）====")
    for t in THRESHOLDS:
        print(f"阈值 {t}:")
        print(f"  正例召回  vector-only={ps[t]['vec']}/{npos}  gated={ps[t]['gated']}/{npos}")
        print(f"  反例误命中 vector-only={ns[t]['vec']}/{nneg}  gated={ns[t]['gated']}/{nneg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
