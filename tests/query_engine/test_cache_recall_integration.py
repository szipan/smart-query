"""缓存召回集成测试（Requirements 5/6/7/16/17；Property 12/14/16）。

验证向量召回 + 槽位门禁的端到端行为：
- 向量层面全部高度相似（canonical 折叠到同一叙述，嵌入几乎相同）；
- 槽位一致的正例（同义词/排序词变体）→ 命中同一缓存条目，且命中仍过 L5；
- 仅比较运算不同的反例（大于→小于）→ 槽位门禁判未命中（即便向量相似）；
- 缓存键与传 L4 的问题都为 canonical_question（Property 12）。

用输入敏感的 FakeLlm 产出 {canonical, slots}（canonical 固定→向量相同；slots 随问题变化）。
"""

from __future__ import annotations

import hashlib
import json
import math

from adapters.term_replacer import AhoCorasickTermReplacer
from query_engine.config import CACHE_NAMESPACE
from query_engine.deps import EngineDependencies
from query_engine.models import AnswerStatus, CallerIdentity, QueryRequest, QuerySource
from query_engine.orchestrator import QueryOrchestrator

from qe_fakes import FakeGuardFactory, FakeLayer4Client, FakeMemory

Q0 = ("导出产品线为户用的机型，电站汇总的装机容量大于1000000w的"
      "电站id、电站名称、装机容量，按照装机容量倒序")
V_SYNONYM = ("导出产品线为家用的机型，电站汇总的装机功率大于1000000w的"
             "电站id、电站名称、装机功率，按照装机功率倒序")   # 家用→户用, 装机功率→装机容量
V_ORDER = ("导出产品线为户用的机型，电站汇总的装机容量大于1000000w的"
           "电站id、电站名称、装机容量，按照装机容量降序")      # 降序→倒序
NEG_LESS = ("导出产品线为户用的机型，电站汇总的装机容量小于1000000w的"
            "电站id、电站名称、装机容量，按照装机容量倒序")      # 大于→小于（槽位不同）

_GLOSSARY = [
    {"synonym": "家用", "term": "户用"},
    {"synonym": "装机功率", "term": "装机容量"},
    {"synonym": "降序", "term": "倒序"},
]

_FIXED_CANON = "查询户用电站装机容量超过阈值的电站"   # 固定 → 各问题向量相同


class _SlotLlm:
    """输入敏感的假 LLM：canonical 固定，slots 随问题中的比较词/排序词变化。"""

    def complete(self, *, system: str, prompt: str, model_id=None,
                 max_tokens: int = 1024, temperature: float = 0.0) -> str:
        q = prompt  # prompt 内含 stage_a（确定性替换后的问题）
        comparison = "LT" if ("小于" in q or "不足" in q) else "GT"
        direction = "ASC" if "升序" in q else "DESC"
        slots = {"comparison": comparison, "threshold": "1000000W", "metric": "装机容量",
                 "group_by": "电站", "agg_func": "SUM", "sort_direction": direction,
                 "sort_field": "装机容量", "output_entity": "电站", "product_line": ["户用"]}
        return json.dumps({"canonical": _FIXED_CANON, "slots": slots}, ensure_ascii=False)


class _TextEmbedder:
    def embed(self, texts):
        return [self._vec(t) for t in texts]

    @staticmethod
    def _vec(text: str):
        h = hashlib.sha256((text or "").encode("utf-8")).digest()
        return [b / 255.0 for b in h]


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class _CosineStore:
    def __init__(self):
        self._items = []
        self.upserts = []

    def similarity_search(self, vector, top_k, namespace):
        res = [{**e, "similarity": _cos(vector, e["vector"])}
               for ns, e in self._items if ns == namespace]
        res.sort(key=lambda r: -r["similarity"])
        return res[:top_k]

    def upsert(self, namespace, entry):
        self._items.append((namespace, entry))
        self.upserts.append((namespace, entry))

    def delete(self, namespace, entry_id):  # pragma: no cover
        pass


def _replacer():
    r = AhoCorasickTermReplacer(lambda: list(_GLOSSARY), ttl_seconds=10_000)
    r.refresh()
    return r


def _deps():
    return EngineDependencies(
        memory=FakeMemory(), llm=_SlotLlm(), embedder=_TextEmbedder(), vector_store=_CosineStore(),
        entity_dictionary=None, term_replacer=_replacer(),
        layer4=FakeLayer4Client(sql="SELECT ps_id FROM stations /*pre-iso*/"),
        guard_factory=FakeGuardFactory(), datasource=None,
    )


def _req(q):
    return QueryRequest(request_id="r", identity=CallerIdentity(user_id="1001"), raw_question=q)


def _store_q0(orch):
    ans = orch.run(_req(Q0))
    assert ans.sql_source is QuerySource.PIPELINE   # 首次未命中，入缓存
    return ans


def test_synonym_variant_hits_when_slots_match():
    orch = QueryOrchestrator(_deps())
    _store_q0(orch)
    ans = orch.run(_req(V_SYNONYM))
    assert ans.sql_source is QuerySource.CACHE_HIT   # 同义词变体：canonical+slots 一致 → 命中
    assert "layer5_guard" in [t.node for t in ans.request_traces]   # 命中仍过 L5
    assert "layer4_pipeline" not in [t.node for t in ans.request_traces]


def test_order_word_variant_hits():
    orch = QueryOrchestrator(_deps())
    _store_q0(orch)
    assert orch.run(_req(V_ORDER)).sql_source is QuerySource.CACHE_HIT


def test_comparison_negative_rejected_by_slot_gate():
    orch = QueryOrchestrator(_deps())
    _store_q0(orch)
    # 向量与 Q0 几乎相同，但比较运算不同（小于）→ 槽位门禁判未命中（Property 14）
    assert orch.run(_req(NEG_LESS)).sql_source is QuerySource.PIPELINE


def test_property12_cache_key_and_l4_question_are_canonical():
    deps = _deps()
    orch = QueryOrchestrator(deps)
    orch.run(_req(V_SYNONYM))   # 未命中：既写缓存又传 L4
    stored = [e["question"] for _ns, e in deps.vector_store._items]
    assert len(stored) == 1
    canonical = stored[0]
    assert deps.layer4.last_request.standalone_question == canonical   # 传 L4 == 缓存键
    assert canonical == _FIXED_CANON
