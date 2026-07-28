"""规范化改写节点（design.md §5.3 / §8.5；Requirements 16）。

把措辞各异但意图相同的问题折叠为统一 ``canonical_question``，作为语义缓存键并替代原问题传给 L4，
从而把"户用/家用/residential""1MW/一百万瓦/1000000w""倒序/降序/从大到小"等变体收拢到同一标准叙述。

三段算法（任一步失败逐级回退，最差回退 ``standalone_question``）：
- 阶段A（确定性，无 LLM）：synonyms→标准词 精确替换（TermReplacer） + 单位/数字规则归一（UnitNormalizer）。
- 阶段B（LLM 结构规范化）：temperature=0，提示词严格约束语义槽位；超长/失败回退阶段A。
- 阶段C（确定性兜底）：对阶段B 产出再跑一次确定性替换，锁定标准词。

参考 SQLBot 术语归一/规范化思路，clean-room 重写。
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any, Optional

from query_engine.config import CANONICAL_LLM_ENABLED, CANONICAL_REWRITE_MAX_LEN
from query_engine.deps import LlmClient, TermReplacer
from query_engine.models import QueryContext, SemanticSlots

# ---------------------------------------------------------------------------
# 单位 / 数字规则归一（纯函数，确定性，无外部依赖）
# ---------------------------------------------------------------------------

_CN_NUM = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_SMALL_UNIT = {"十": 10, "百": 100, "千": 1000}
_CN_BIG_UNIT = {"万": 10000, "亿": 100000000}

# 功率单位 → 瓦(W) 换算因子；chinese 词优先于英文缩写，[wW] 放最后避免抢占
_UNIT_FACTOR = {
    "兆瓦": 1_000_000, "千瓦": 1_000, "瓦特": 1, "瓦": 1,
    "gw": 1_000_000_000, "mw": 1_000_000, "kw": 1_000, "w": 1,
}
_UNIT_ALT = r"兆瓦|千瓦|瓦特|瓦|[gmkGMK][wW]|[wW]"
_QTY_ALT = r"[0-9]+(?:\.[0-9]+)?[万亿]?|[零〇一二两三四五六七八九十百千万亿]+"
_TOKEN_RE = re.compile(rf"(?P<qty>{_QTY_ALT})\s*(?P<unit>{_UNIT_ALT})")


def _cn_to_int(s: str) -> Optional[int]:
    """中文数字转整数，支持 十/百/千/万/亿；无法解析返回 None。"""
    if not s:
        return None
    total = 0        # 已结算（含大单位段）
    section = 0      # 当前 万/亿 段内累计
    number = 0       # 当前小节数字
    for ch in s:
        if ch in _CN_NUM:
            number = _CN_NUM[ch]
        elif ch in _CN_SMALL_UNIT:
            section += (number or 1) * _CN_SMALL_UNIT[ch]
            number = 0
        elif ch in _CN_BIG_UNIT:
            section += number
            total += section * _CN_BIG_UNIT[ch]
            section = 0
            number = 0
        else:
            return None
    return total + section + number


def _qty_value(qty: str) -> Optional[float]:
    m = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([万亿]?)", qty)
    if m:
        v = float(m.group(1))
        if m.group(2) == "万":
            v *= 10_000
        elif m.group(2) == "亿":
            v *= 100_000_000
        return v
    n = _cn_to_int(qty)
    return float(n) if n is not None else None


def _token_to_watts(qty: str, unit: str) -> Optional[int]:
    q = _qty_value(qty)
    f = _UNIT_FACTOR.get(unit.lower())
    if q is None or f is None:
        return None
    return int(round(q * f))


class UnitNormalizer:
    """把等价的单位/数字写法归一为统一标准写法（如 1MW / 一百万瓦 / 1000000瓦 → 1000000W）。"""

    def normalize(self, text: str) -> str:
        if not text:
            return text

        def _repl(m: "re.Match[str]") -> str:
            watts = _token_to_watts(m.group("qty"), m.group("unit"))
            return f"{watts}W" if watts is not None else m.group(0)

        return _TOKEN_RE.sub(_repl, text)


# ---------------------------------------------------------------------------
# 规范化改写节点
# ---------------------------------------------------------------------------

_CANON_LLM_MAX_TOKENS = 512

_CANON_SYSTEM = (
    "你是问题规范化与结构抽取助手。对用户问题：\n"
    "1) 产出标准、简洁的自然语言问句 canonical，只做叙述规范化，"
    "严禁改变过滤条件、比较运算、阈值、单位、聚合维度、聚合函数、排序方向、排序字段、输出字段等语义；\n"
    "2) 抽取结构化语义槽位 slots。\n"
    "只输出一个 JSON 对象，不要解释、不要代码围栏。字段：\n"
    '{"canonical":"...","slots":{'
    '"comparison":"GT|GE|LT|LE|EQ 或 null(分别对应 大于/大于等于/小于/小于等于/等于)",'
    '"threshold":"阈值含单位，如 1000000W；无则 null",'
    '"metric":"指标词(如 装机容量/发电量)或 null",'
    '"group_by":"聚合维度：电站|机型|单台|null",'
    '"agg_func":"SUM|AVG|MAX|MIN|COUNT|NONE(汇总/合计/累计=SUM，平均=AVG)",'
    '"sort_direction":"DESC|ASC|null(降序/倒序/从大到小=DESC，升序=ASC)",'
    '"sort_field":"排序字段或 null",'
    '"output_entity":"输出主体：电站|机型|null",'
    '"product_line":["产品线取值，如 户用/工商业"],'
    '"top_n":整数或 null,'
    '"extra_filters":["附加过滤签名，如 build_year=2025"]}}'
)


def _canon_prompt(text: str) -> str:
    return f"问题：\n{text}\n\n输出 JSON："


def _clean(text: str) -> str:
    """清洗文本：去空白、去成对引号 / 代码围栏。"""
    t = (text or "").strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in ("\"", "'", "`"):
        t = t[1:-1].strip()
    return t


def _parse_json_obj(raw: str) -> Optional[dict]:
    """从 LLM 输出中鲁棒地解析首个 JSON 对象；失败返回 None。"""
    if not raw:
        return None
    t = raw.strip()
    if t.startswith("```"):
        t = t.strip("`")
    i, j = t.find("{"), t.rfind("}")
    if i == -1 or j == -1 or j < i:
        return None
    try:
        obj = json.loads(t[i:j + 1])
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


# ---- 槽位归一映射（确定性）----
_CMP_MAP = {
    "GT": "GT", ">": "GT", "大于": "GT", "超过": "GT", "超": "GT", "高于": "GT", "多于": "GT",
    "GE": "GE", ">=": "GE", "≥": "GE", "大于等于": "GE", "不低于": "GE", "至少": "GE", "不少于": "GE",
    "LT": "LT", "<": "LT", "小于": "LT", "不足": "LT", "低于": "LT", "少于": "LT",
    "LE": "LE", "<=": "LE", "≤": "LE", "小于等于": "LE", "不超过": "LE", "不高于": "LE", "至多": "LE",
    "EQ": "EQ", "=": "EQ", "==": "EQ", "等于": "EQ", "为": "EQ",
}
_DIR_MAP = {
    "DESC": "DESC", "降序": "DESC", "倒序": "DESC", "从大到小": "DESC", "从高到低": "DESC", "由大到小": "DESC",
    "ASC": "ASC", "升序": "ASC", "从小到大": "ASC", "由小到大": "ASC", "从低到高": "ASC",
}
_AGG_MAP = {
    "SUM": "SUM", "求和": "SUM", "汇总": "SUM", "合计": "SUM", "总计": "SUM", "累计": "SUM", "总和": "SUM", "总": "SUM",
    "AVG": "AVG", "平均": "AVG", "均值": "AVG",
    "MAX": "MAX", "最大": "MAX", "MIN": "MIN", "最小": "MIN",
    "COUNT": "COUNT", "计数": "COUNT", "个数": "COUNT",
    "NONE": "NONE", "无": "NONE",
}


# ---- 规则型确定性抽取（基于确定性 stage_a 文本，完全不依赖 LLM / 温度）----
# 顺序敏感：复合词（大于等于/不超过）必须先于基础词（大于/超过）匹配
_CMP_RULES = [
    ("GE", ("大于等于", "不小于", "不低于", "至少", "不少于", ">=", "≥")),
    ("LE", ("小于等于", "不大于", "不超过", "不高于", "至多", "<=", "≤")),
    ("GT", ("大于", "超过", "高于", "多于", ">")),
    ("LT", ("小于", "不足", "低于", "少于", "不到", "<")),
    ("EQ", ("等于", "恰好", "正好")),
]
_DIR_RULES = [
    ("DESC", ("倒序", "降序", "从大到小", "从高到低", "由大到小", "由高到低", "递减", "大到小")),
    ("ASC", ("升序", "从小到大", "从低到高", "由小到大", "由低到高", "递增")),
]


def _rule_comparison(text: str) -> Optional[str]:
    for op, kws in _CMP_RULES:
        if any(k in text for k in kws):
            return op
    return None


def _rule_direction(text: str) -> Optional[str]:
    for d, kws in _DIR_RULES:
        if any(k in text for k in kws):
            return d
    return None


def _rule_threshold_watts(text: str) -> Optional[int]:
    norm = UnitNormalizer().normalize(text)
    m = re.search(r"(\d+)\s*[wW]", norm)
    return int(m.group(1)) if m else None


def _rule_top_n(text: str) -> Optional[int]:
    m = re.search(r"(?:前|top|Top|TOP)\s*(\d+)", text)
    return int(m.group(1)) if m else None


def _map_enum(val: Any, table: dict[str, str]) -> Optional[str]:
    if val is None:
        return None
    k = str(val).strip()
    if not k or k.lower() in ("null", "none", "na", "n/a"):
        return None
    return table.get(k) or table.get(k.upper()) or table.get(k.lower()) or k


def _threshold_to_watts(val: Any) -> Optional[int]:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in ("null", "none"):
        return None
    norm = UnitNormalizer().normalize(s)
    m = re.search(r"(\d+)\s*[wW]", norm)
    if m:
        return int(m.group(1))
    m2 = re.search(r"\d+", norm)
    return int(m2.group(0)) if m2 else None


def _to_int(val: Any) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except Exception:
        m = re.search(r"\d+", str(val))
        return int(m.group(0)) if m else None


def _is_degenerate(s: SemanticSlots) -> bool:
    """全部关键槽位皆空 → 视为提取失败（fail-safe：避免空对空误命中）。"""
    return (s.comparison is None and s.threshold_watts is None and s.metric is None
            and s.group_by is None and s.agg_func is None and s.sort_direction is None
            and s.sort_field is None and s.output_entity is None and not s.product_line
            and s.top_n is None and not s.extra_filters)


class CanonicalRewriter:
    def __init__(self, llm: Optional[LlmClient], term_replacer: Optional[TermReplacer], *,
                 llm_enabled: bool = CANONICAL_LLM_ENABLED,
                 max_len: int = CANONICAL_REWRITE_MAX_LEN,
                 llm_max_tokens: int = _CANON_LLM_MAX_TOKENS,
                 model_id: Optional[str] = None) -> None:
        self._llm = llm
        self._replacer = term_replacer
        self._units = UnitNormalizer()
        self._llm_enabled = llm_enabled
        self._max_len = max_len
        self._llm_max_tokens = llm_max_tokens
        self._model_id = model_id   # 槽位抽取专用模型（独立于主生成模型）

    def run(self, ctx: QueryContext) -> QueryContext:
        base = ctx.standalone_question or ctx.request.raw_question
        # 阶段A：确定性替换 + 单位归一
        stage_a = self._deterministic(base)
        canonical = stage_a
        slots: Optional[SemanticSlots] = None
        # 阶段B：LLM 结构化产出 {canonical, slots}（可开关；失败/坏 JSON → 回退，slots 置空）
        if self._llm_enabled and self._llm is not None:
            try:
                raw = self._llm.complete(system=_CANON_SYSTEM, prompt=_canon_prompt(stage_a),
                                         model_id=self._model_id,
                                         max_tokens=self._llm_max_tokens, temperature=0.0)
                parsed = _parse_json_obj(raw)
                if parsed is None:
                    ctx.warnings.append("规范化改写：LLM 输出非 JSON，回退阶段A，slots 置空")
                else:
                    cand = _clean(str(parsed.get("canonical") or ""))
                    if cand and len(cand) <= self._max_len:
                        canonical = cand
                    elif cand:
                        ctx.warnings.append(
                            f"规范化改写：canonical 超长({len(cand)}>{self._max_len})，回退阶段A")
                    else:
                        ctx.warnings.append("规范化改写：canonical 为空，回退阶段A")
                    slots = self._normalize_slots(parsed.get("slots"))
            except Exception as e:  # LLM 调用失败 → 降级，不中断链路（Property 13）
                ctx.warnings.append(f"规范化改写：LLM 失败回退阶段A：{e!r}")
        # 阶段C：确定性兜底，锁定标准词
        canonical = self._deterministic(canonical)
        # 规则型确定性抽取：用 stage_a（完全确定性）覆盖 4 个高判别性槽位，
        # 使 comparison/threshold/sort_direction/top_n 不依赖 LLM 温度与逐次抖动。
        if slots is not None:
            slots = self._apply_rules(slots, stage_a)
        ctx.canonical_question = canonical or base
        ctx.semantic_slots = slots   # 可能为 None（fail-safe）
        return ctx

    def _apply_rules(self, slots: SemanticSlots, text: str) -> SemanticSlots:
        comp = _rule_comparison(text)
        thr = _rule_threshold_watts(text)
        direction = _rule_direction(text)
        topn = _rule_top_n(text)
        return replace(
            slots,
            comparison=comp if comp is not None else slots.comparison,
            threshold_watts=thr if thr is not None else slots.threshold_watts,
            sort_direction=direction if direction is not None else slots.sort_direction,
            top_n=topn if topn is not None else slots.top_n,
        )

    # 阶段A / C 共用：确定性替换 + 单位归一（各步独立容错，永不抛断）
    def _deterministic(self, text: str) -> str:
        try:
            replaced = self._replacer.replace(text) if self._replacer is not None else text
        except Exception:
            replaced = text
        try:
            return self._units.normalize(replaced)
        except Exception:
            return replaced

    # ---- 槽位归一（确定性）----
    def _normalize_slots(self, raw: Any) -> Optional[SemanticSlots]:
        if not isinstance(raw, dict):
            return None
        comparison = _map_enum(raw.get("comparison"), _CMP_MAP)
        threshold = _threshold_to_watts(raw.get("threshold"))
        metric = self._norm_term(raw.get("metric"))
        group_by_raw = self._norm_entity(raw.get("group_by"))
        agg_raw = _map_enum(raw.get("agg_func"), _AGG_MAP)
        sort_direction = _map_enum(raw.get("sort_direction"), _DIR_MAP)
        sort_field = self._norm_term(raw.get("sort_field"))
        output_entity = self._norm_entity(raw.get("output_entity"))
        product_line = self._norm_list(raw.get("product_line"))
        top_n = _to_int(raw.get("top_n"))
        extra_filters = self._norm_filters(raw.get("extra_filters"))

        # Fail-safe：基于"原始抽取"（不含默认值）判断是否退化为空，防空对空误命中
        raw_empty = (comparison is None and threshold is None and metric is None
                     and sort_direction is None and sort_field is None and output_entity is None
                     and not product_line and top_n is None and not extra_filters
                     and group_by_raw is None and agg_raw in (None, "NONE"))
        if raw_empty:
            return None

        # 确定性默认（消除"是否显式提及聚合"造成的抖动）：
        # group_by 缺省取 output_entity；agg_func 缺省取 SUM。反例若显式为 单台/平均/按机型，
        # 会保留其显式值而不被默认覆盖，故仍可被门禁区分。
        group_by = group_by_raw or output_entity
        agg_func = agg_raw if agg_raw not in (None, "NONE") else "SUM"

        return SemanticSlots(
            comparison=comparison, threshold_watts=threshold, metric=metric,
            group_by=group_by, agg_func=agg_func, sort_direction=sort_direction,
            sort_field=sort_field, output_entity=output_entity, product_line=product_line,
            top_n=top_n, extra_filters=extra_filters,
        )

    def _norm_term(self, val: Any) -> Optional[str]:
        if val is None:
            return None
        s = str(val).strip()
        if not s or s.lower() in ("null", "none"):
            return None
        try:
            s = self._replacer.replace(s) if self._replacer is not None else s
        except Exception:
            pass
        return s.strip() or None

    def _norm_entity(self, val: Any) -> Optional[str]:
        s = self._norm_term(val)
        if not s:
            return None
        if "机型" in s or "型号" in s:
            return "机型"
        if "单台" in s:
            return "单台"
        if "电站" in s or "站点" in s or "站" in s:
            return "电站"
        return s

    def _norm_list(self, val: Any) -> tuple[str, ...]:
        if not isinstance(val, (list, tuple)):
            if val is None:
                return ()
            val = [val]
        out = []
        for v in val:
            t = self._norm_term(v)
            if t:
                out.append(t)
        return tuple(sorted(set(out)))

    def _norm_filters(self, val: Any) -> tuple[str, ...]:
        if not isinstance(val, (list, tuple)):
            if val is None or str(val).strip() == "":
                return ()
            val = [val]
        out = [str(v).strip() for v in val if str(v).strip()]
        return tuple(sorted(set(out)))
