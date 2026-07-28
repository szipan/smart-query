"""规范化改写节点单测 + 属性测试（Requirements 16；Property 11/13）。

覆盖：UnitNormalizer 规则归一；CanonicalRewriter 三段 A/B/C 与逐级回退；
阶段A/C 确定性（Property 11）；任一阶段失败永不抛断、canonical 恒非空（Property 13）。
外部依赖（LLM / TermReplacer）全部 mock。
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from query_engine.models import CallerIdentity, QueryContext, QueryRequest
from query_engine.nodes.canonical_rewriter import (
    CanonicalRewriter,
    UnitNormalizer,
    _rule_comparison,
    _rule_direction,
    _rule_threshold_watts,
    _rule_top_n,
)


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------
class _DictReplacer:
    """确定性替换替身：按 mapping 精确子串替换（不做归一化，便于断言）。"""

    def __init__(self, mapping: dict[str, str]):
        self._m = mapping

    def replace(self, text: str) -> str:
        for k, v in self._m.items():
            text = text.replace(k, v)
        return text


class _RaisingReplacer:
    def replace(self, text: str) -> str:
        raise RuntimeError("replacer down")


class _Llm:
    def __init__(self, out: str | None = None, exc: Exception | None = None):
        self._out = out
        self._exc = exc
        self.calls: list[dict] = []

    def complete(self, *, system: str, prompt: str, model_id=None,
                 max_tokens: int = 1024, temperature: float = 0.0) -> str:
        self.calls.append({"system": system, "prompt": prompt, "model_id": model_id})
        if self._exc is not None:
            raise self._exc
        return self._out if self._out is not None else ""


def _ctx(q: str, standalone: str | None = None) -> QueryContext:
    req = QueryRequest(request_id="r", identity=CallerIdentity(user_id="u"), raw_question=q)
    c = QueryContext(request=req)
    c.standalone_question = standalone if standalone is not None else q
    return c


# ---------------------------------------------------------------------------
# UnitNormalizer（Requirement 16.4）
# ---------------------------------------------------------------------------
class TestUnitNormalizer:
    def setup_method(self):
        self.u = UnitNormalizer()

    def test_arabic_mw(self):
        assert self.u.normalize("装机容量大于1MW的") == "装机容量大于1000000W的"

    def test_arabic_mw_lowercase(self):
        assert self.u.normalize("电站总容量>1mw") == "电站总容量>1000000W"

    def test_chinese_hundred_wan_wa(self):
        assert self.u.normalize("一百万瓦") == "1000000W"

    def test_arabic_hundred_wan_wa(self):
        assert self.u.normalize("大于100万瓦") == "大于1000000W"

    def test_arabic_hundred_wan_w(self):
        assert self.u.normalize("超过100万w") == "超过1000000W"

    def test_plain_watt_chinese_unit(self):
        assert self.u.normalize("1000000瓦") == "1000000W"

    def test_plain_watt(self):
        assert self.u.normalize("大于1000000w的电站") == "大于1000000W的电站"

    def test_kw_kept_distinct_from_w(self):
        # 1000000kW = 1e9 W，必须与 1000000W 保持不同（防误命中）
        assert self.u.normalize("装机容量>1000000kw") == "装机容量>1000000000W"

    def test_no_unit_unchanged(self):
        assert self.u.normalize("装机>1000000") == "装机>1000000"

    def test_empty(self):
        assert self.u.normalize("") == ""

    def test_idempotent(self):
        once = self.u.normalize("大于1MW且小于5MW")
        assert self.u.normalize(once) == once


# ---------------------------------------------------------------------------
# CanonicalRewriter 三段与回退（Requirement 16.2/16.5/16.6/16.7/16.8）
# ---------------------------------------------------------------------------
class TestCanonicalRewriter:
    def test_stage_a_only_folds_synonyms_and_units(self):
        # 关闭 LLM，只验证确定性阶段：同义词替换 + 单位归一
        rw = CanonicalRewriter(_Llm(), _DictReplacer({"家用": "户用", "装机功率": "装机容量"}),
                               llm_enabled=False)
        ctx = _ctx("产品线家用，装机功率大于1MW")
        rw.run(ctx)
        assert ctx.canonical_question == "产品线户用，装机容量大于1000000W"

    def test_llm_output_used_when_ok(self):
        rw = CanonicalRewriter(
            _Llm(out='{"canonical":"标准问题","slots":{"comparison":"GT","threshold":"1000000W","metric":"装机容量"}}'),
            None, llm_enabled=True, max_len=200)
        ctx = _ctx("随便一个问题")
        rw.run(ctx)
        assert ctx.canonical_question == "标准问题"
        assert ctx.semantic_slots is not None
        assert ctx.semantic_slots.comparison == "GT"
        assert ctx.semantic_slots.threshold_watts == 1000000

    def test_llm_failure_falls_back_to_stage_a(self):
        rw = CanonicalRewriter(_Llm(exc=RuntimeError("llm down")),
                               _DictReplacer({"家用": "户用"}), llm_enabled=True)
        ctx = _ctx("家用产品线")
        rw.run(ctx)
        assert ctx.canonical_question == "户用产品线"
        assert any("LLM 失败" in w for w in ctx.warnings)

    def test_llm_overlong_falls_back_to_stage_a(self):
        rw = CanonicalRewriter(_Llm(out='{"canonical":"' + "x" * 500 + '","slots":{}}'),
                               _DictReplacer({"家用": "户用"}), llm_enabled=True, max_len=200)
        ctx = _ctx("家用产品线")
        rw.run(ctx)
        assert ctx.canonical_question == "户用产品线"
        assert any("超长" in w for w in ctx.warnings)

    def test_llm_empty_falls_back_to_stage_a(self):
        rw = CanonicalRewriter(_Llm(out='{"canonical":"","slots":{}}'),
                               _DictReplacer({"家用": "户用"}), llm_enabled=True)
        ctx = _ctx("家用产品线")
        rw.run(ctx)
        assert ctx.canonical_question == "户用产品线"
        assert any("为空" in w for w in ctx.warnings)

    def test_bad_json_falls_back_and_slots_none(self):
        rw = CanonicalRewriter(_Llm(out="这不是JSON"), _DictReplacer({"家用": "户用"}), llm_enabled=True)
        ctx = _ctx("家用产品线")
        rw.run(ctx)
        assert ctx.canonical_question == "户用产品线"   # 回退阶段A
        assert ctx.semantic_slots is None                # fail-safe
        assert any("非 JSON" in w for w in ctx.warnings)

    def test_stage_c_locks_standard_term_after_llm(self):
        # LLM 的 canonical 含同义词，阶段C 兜底再替换回标准词
        rw = CanonicalRewriter(
            _Llm(out='{"canonical":"家用产品线的机型","slots":{"comparison":"GT"}}'),
            _DictReplacer({"家用": "户用"}), llm_enabled=True)
        ctx = _ctx("户用产品线的机型")
        rw.run(ctx)
        assert "家用" not in ctx.canonical_question
        assert "户用" in ctx.canonical_question

    def test_degenerate_slots_become_none(self):
        rw = CanonicalRewriter(_Llm(out='{"canonical":"问题","slots":{}}'), None, llm_enabled=True)
        ctx = _ctx("问题")
        rw.run(ctx)
        assert ctx.semantic_slots is None   # 空槽位视为提取失败（防空对空误命中）

    def test_group_by_and_agg_defaulted_when_unspecified(self):
        # 未显式提及聚合 → group_by 取 output_entity、agg_func 取 SUM（消除抖动）
        rw = CanonicalRewriter(
            _Llm(out='{"canonical":"q","slots":{"comparison":"GT","metric":"装机容量","output_entity":"电站"}}'),
            None, llm_enabled=True)
        ctx = _ctx("x")
        rw.run(ctx)
        assert ctx.semantic_slots.group_by == "电站"
        assert ctx.semantic_slots.agg_func == "SUM"

    def test_explicit_agg_and_group_by_not_overridden(self):
        rw = CanonicalRewriter(
            _Llm(out='{"canonical":"q","slots":{"comparison":"GT","metric":"装机容量",'
                     '"output_entity":"电站","agg_func":"AVG","group_by":"单台"}}'),
            None, llm_enabled=True)
        ctx = _ctx("x")
        rw.run(ctx)
        assert ctx.semantic_slots.agg_func == "AVG"     # 显式平均不被默认覆盖
        assert ctx.semantic_slots.group_by == "单台"    # 显式单台不被默认覆盖

    def test_rules_override_llm_for_deterministic_fields(self):
        # LLM 给了错误的 comparison/sort_direction/threshold，规则从问题文本确定性纠正
        q = "户用电站装机容量小于1000000W的，按装机容量升序，前10个"
        llm = _Llm(out='{"canonical":"x","slots":{"comparison":"GT","sort_direction":"DESC",'
                       '"threshold":"9999W","metric":"装机容量","output_entity":"电站"}}')
        rw = CanonicalRewriter(llm, None, llm_enabled=True)
        ctx = _ctx(q)
        rw.run(ctx)
        s = ctx.semantic_slots
        assert s.comparison == "LT"          # 规则从"小于"覆盖 LLM 的 GT
        assert s.sort_direction == "ASC"     # 规则从"升序"覆盖 DESC
        assert s.threshold_watts == 1000000  # 规则从文本 1000000W 覆盖 9999
        assert s.top_n == 10                 # 规则从"前10"

    def test_model_id_passed_to_llm(self):
        llm = _Llm(out='{"canonical":"x","slots":{"comparison":"GT","metric":"装机容量"}}')
        rw = CanonicalRewriter(llm, None, llm_enabled=True, model_id="extract-model")
        rw.run(_ctx("装机容量大于1000000W"))
        assert llm.calls[-1]["model_id"] == "extract-model"


class TestRuleExtractors:
    def test_comparison(self):
        assert _rule_comparison("电站装机容量大于等于1000000W") == "GE"
        assert _rule_comparison("电站装机容量大于1000000W") == "GT"
        assert _rule_comparison("装机容量不足1000000W") == "LT"
        assert _rule_comparison("装机容量小于1000000W") == "LT"
        assert _rule_comparison("装机容量不超过1000000W") == "LE"
        assert _rule_comparison("装机容量等于1000000W") == "EQ"
        assert _rule_comparison("没有比较词的句子") is None

    def test_direction(self):
        assert _rule_direction("按装机容量倒序排列") == "DESC"
        assert _rule_direction("按装机容量从大到小") == "DESC"
        assert _rule_direction("按装机容量升序") == "ASC"
        assert _rule_direction("无排序词") is None

    def test_threshold(self):
        assert _rule_threshold_watts("大于1MW") == 1000000
        assert _rule_threshold_watts("大于一百万瓦") == 1000000
        assert _rule_threshold_watts("大于1000000kw") == 1000000000
        assert _rule_threshold_watts("装机大于1000000（无单位）") is None

    def test_top_n(self):
        assert _rule_top_n("装机容量最大的前10个电站") == 10
        assert _rule_top_n("top 5") == 5
        assert _rule_top_n("没有TopN") is None

    def test_replacer_none_still_unit_normalizes(self):
        rw = CanonicalRewriter(_Llm(), None, llm_enabled=False)
        ctx = _ctx("大于1MW")
        rw.run(ctx)
        assert ctx.canonical_question == "大于1000000W"

    def test_worst_fallback_when_replacer_raises_and_llm_disabled(self):
        # 替换抛错被吞掉，退回未替换文本再单位归一；canonical 恒非空（Property 13）
        rw = CanonicalRewriter(_Llm(), _RaisingReplacer(), llm_enabled=False)
        ctx = _ctx("大于1MW的户用电站")
        rw.run(ctx)
        assert ctx.canonical_question == "大于1000000W的户用电站"

    def test_uses_raw_question_when_no_standalone(self):
        rw = CanonicalRewriter(_Llm(), None, llm_enabled=False)
        req = QueryRequest(request_id="r", identity=CallerIdentity(user_id="u"), raw_question="原始问题")
        ctx = QueryContext(request=req)  # standalone_question 为 None
        rw.run(ctx)
        assert ctx.canonical_question == "原始问题"


# ---------------------------------------------------------------------------
# Property 11：确定性阶段可复现（llm 关闭 → 全节点确定性）
# ---------------------------------------------------------------------------
@settings(max_examples=60)
@given(q=st.text(min_size=1, max_size=40))
def test_property11_deterministic_when_llm_disabled(q: str) -> None:
    rw = CanonicalRewriter(_Llm(), _DictReplacer({"家用": "户用", "降序": "倒序"}), llm_enabled=False)
    c1, c2 = _ctx(q), _ctx(q)
    rw.run(c1)
    rw.run(c2)
    assert c1.canonical_question == c2.canonical_question


# ---------------------------------------------------------------------------
# Property 13：任一阶段失败永不抛断，canonical 恒非空
# ---------------------------------------------------------------------------
@settings(max_examples=60)
@given(q=st.text(min_size=1, max_size=40))
def test_property13_never_breaks_and_non_empty(q: str) -> None:
    rw = CanonicalRewriter(_Llm(exc=RuntimeError("boom")), _RaisingReplacer(), llm_enabled=True)
    ctx = _ctx(q)
    rw.run(ctx)                      # 不得抛出
    assert ctx.canonical_question    # 非空（最差回退 standalone / raw）
