"""Task 4/5/6 单测：上下文改写、术语归一化、语义缓存。Requirements 2/3/4/5/6/7。"""

from query_engine.config import CACHE_NAMESPACE
from query_engine.models import CallerIdentity, QueryContext, QueryRequest
from query_engine.nodes.context_rewriter import ContextRewriter
from query_engine.nodes.semantic_cache import SemanticCache
from query_engine.nodes.term_normalizer import HardEntityResolver, SoftGlossaryResolver, TermNormalizer

from qe_fakes import (
    FakeEmbedder,
    FakeEntityDictionary,
    FakeLlm,
    FakeMemory,
    FakeVectorStore,
)


def _ctx(q="本月各电站发电量", session="s1"):
    return QueryContext(request=QueryRequest(
        request_id="r", identity=CallerIdentity(user_id="1001"), raw_question=q, session_id=session))


# ---- ContextRewriter ----
def test_rewrite_empty_history_uses_raw() -> None:
    ctx = _ctx()
    ContextRewriter(FakeMemory(turns=[]), FakeLlm()).run(ctx)
    assert ctx.standalone_question == "本月各电站发电量"


def test_rewrite_with_history_calls_llm() -> None:
    ctx = _ctx(q="那上个月呢")
    llm = FakeLlm(out="上个月各电站发电量")
    ContextRewriter(FakeMemory(turns=[{"question": "本月发电量", "answer": "..."}]), llm).run(ctx)
    assert ctx.standalone_question == "上个月各电站发电量"
    assert llm.calls


def test_rewrite_memory_fail_degrades() -> None:
    ctx = _ctx()
    ContextRewriter(FakeMemory(fail_read=True), FakeLlm()).run(ctx)
    assert ctx.standalone_question == "本月各电站发电量"
    assert ctx.memory_degraded is True
    assert ctx.warnings


def test_rewrite_accepts_answer_summary_key() -> None:
    # 历史 turn 用 answer_summary（AgentCore/InMemory 的字段），应被正确拼进历史
    llm = FakeLlm(out="上个月各电站发电量")
    turns = [{"question": "本月发电量", "answer_summary": "SELECT ..."}]
    ctx = _ctx(q="那上个月呢")
    ContextRewriter(FakeMemory(turns=turns), llm).run(ctx)
    assert "SELECT ..." in llm.calls[0]["prompt"]  # answer_summary 进了 prompt
    assert ctx.standalone_question == "上个月各电站发电量"


def test_rewrite_strips_wrapping_quotes() -> None:
    llm = FakeLlm(out='"上个月各电站发电量"')
    ctx = _ctx(q="那上个月呢")
    ContextRewriter(FakeMemory(turns=[{"question": "本月发电量", "answer": "x"}]), llm).run(ctx)
    assert ctx.standalone_question == "上个月各电站发电量"


def test_rewrite_oversized_falls_back_to_raw() -> None:
    llm = FakeLlm(out="x" * 5000)  # 超长 → 回退原问题
    ctx = _ctx(q="那上个月呢")
    ContextRewriter(FakeMemory(turns=[{"question": "q", "answer": "a"}]), llm).run(ctx)
    assert ctx.standalone_question == "那上个月呢"
    assert any("超长" in w for w in ctx.warnings)


class _RaisingLlm:
    def complete(self, *, system, prompt, max_tokens=1024, temperature=0.0):
        raise RuntimeError("bedrock down")


def test_rewrite_llm_failure_degrades_to_raw() -> None:
    ctx = _ctx(q="那上个月呢")
    ContextRewriter(FakeMemory(turns=[{"question": "q", "answer": "a"}]), _RaisingLlm()).run(ctx)
    assert ctx.standalone_question == "那上个月呢"
    assert any("改写 LLM 调用失败" in w for w in ctx.warnings)


def test_rewrite_no_memory_uses_raw() -> None:
    # memory=None（未接 Memory 能力）→ 直接用原问题，不应尝试读历史或调 LLM
    llm = FakeLlm(out="不应该被用到的改写")
    ctx = _ctx(q="那上个月呢")
    ContextRewriter(None, llm).run(ctx)
    assert ctx.standalone_question == "那上个月呢"
    assert not llm.calls


def test_rewrite_history_without_llm_uses_raw() -> None:
    # 有历史但 llm=None（未接 LLM 能力）→ 无法改写，用原问题
    ctx = _ctx(q="那上个月呢")
    ContextRewriter(FakeMemory(turns=[{"question": "本月发电量", "answer": "..."}]), None).run(ctx)
    assert ctx.standalone_question == "那上个月呢"


# ---- TermNormalizer ----
def test_hard_resolver_deterministic() -> None:
    d = FakeEntityDictionary(hits=[{"surface": "1号电站", "canonical": "PS-1", "entity_type": "power_station", "entity_id": "1001"}])
    r = HardEntityResolver(d)
    a = r.resolve("1号电站发电量")
    b = r.resolve("1号电站发电量")
    assert a == b
    assert a[0].entity_id == "1001"


def test_hard_resolver_no_hit_returns_empty() -> None:
    assert HardEntityResolver(FakeEntityDictionary(hits=[])).resolve("x") == []


def test_soft_resolver_returns_terms() -> None:
    vs = FakeVectorStore(data={"glossary": [{"term": "自发自用率", "definition": "...", "synonyms": ["自用率"]}]})
    terms = SoftGlossaryResolver(FakeEmbedder(), vs).resolve("自发自用率多少")
    assert terms and terms[0].term == "自发自用率"


def test_soft_resolver_fail_no_interrupt() -> None:
    class BadVS(FakeVectorStore):
        def similarity_search(self, *a, **k):
            raise RuntimeError("vs down")

    assert SoftGlossaryResolver(FakeEmbedder(), BadVS()).resolve("q") == []


def test_term_normalizer_writes_both() -> None:
    d = FakeEntityDictionary(hits=[{"surface": "s", "canonical": "c", "entity_type": "t"}])
    vs = FakeVectorStore(data={"glossary": [{"term": "x", "definition": "d"}]})
    tn = TermNormalizer(HardEntityResolver(d), SoftGlossaryResolver(FakeEmbedder(), vs))
    ctx = _ctx()
    ctx.standalone_question = "q"
    tn.run(ctx)
    assert ctx.resolved_entities and ctx.resolved_terms


# ---- SemanticCache ----
def test_cache_hit_above_threshold() -> None:
    vs = FakeVectorStore(data={CACHE_NAMESPACE: [{"similarity": 0.97, "pre_isolation_sql": "SELECT 1", "entry_id": "e1"}]})
    res = SemanticCache(FakeEmbedder(), vs).lookup("q")
    assert res.hit and res.pre_isolation_sql == "SELECT 1"


def test_cache_miss_below_threshold() -> None:
    vs = FakeVectorStore(data={CACHE_NAMESPACE: [{"similarity": 0.5, "pre_isolation_sql": "SELECT 1"}]})
    assert SemanticCache(FakeEmbedder(), vs).lookup("q").hit is False


def test_cache_miss_no_hits() -> None:
    assert SemanticCache(FakeEmbedder(), FakeVectorStore()).lookup("q").hit is False


def test_cache_store_only_pre_isolation_sql() -> None:
    vs = FakeVectorStore()
    SemanticCache(FakeEmbedder(), vs).store("q", "SELECT ps_id FROM t")
    assert len(vs.upserts) == 1
    ns, entry = vs.upserts[0]
    assert ns == CACHE_NAMESPACE
    assert entry["pre_isolation_sql"] == "SELECT ps_id FROM t"
    assert "safe_sql" not in entry  # 绝不存 safe_sql
