"""AhoCorasickTermReplacer 单测（内存 loader，不连真实服务）。

覆盖 Requirement 16.3：synonyms→标准词 精确替换；最长匹配、非重叠；未登记不动；
短同义词过滤；标准词不自替换；确定性（同输入同输出）。
"""

from __future__ import annotations

from adapters.term_replacer import AhoCorasickTermReplacer

_BIG_TTL = 10_000


def _replacer(rows, **kw):
    r = AhoCorasickTermReplacer(lambda: list(rows), ttl_seconds=_BIG_TTL, **kw)
    r.refresh()
    return r


def test_basic_synonym_replacement():
    r = _replacer([
        {"synonym": "家用", "term": "户用"},
        {"synonym": "装机功率", "term": "装机容量"},
    ])
    # normalize() 会把全角逗号 NFKC 归一为半角（两侧一致，不影响命中）
    assert r.replace("产品线家用，装机功率大") == "产品线户用,装机容量大"


def test_english_synonym_lowercased_match():
    r = _replacer([{"synonym": "residential", "term": "户用"}])
    assert r.replace("Residential 机型") == "户用 机型"


def test_unregistered_word_unchanged():
    r = _replacer([{"synonym": "家用", "term": "户用"}])
    assert r.replace("工商业电站") == "工商业电站"


def test_short_synonym_filtered_by_min_len():
    r = _replacer([{"synonym": "户", "term": "户用"}], min_synonym_len=2)
    assert r.replace("户用") == "户用"  # 单字同义词不入自动机，不产生误替换


def test_standard_term_not_self_replaced():
    r = _replacer([{"synonym": "户用", "term": "户用"}])
    assert r.replace("户用产品线") == "户用产品线"


def test_longest_match_wins():
    r = _replacer([
        {"synonym": "家庭户用", "term": "AAA"},
        {"synonym": "户用", "term": "BBB"},
    ])
    assert r.replace("家庭户用") == "AAA"


def test_non_overlapping_multiple_hits():
    r = _replacer([
        {"synonym": "家用", "term": "户用"},
        {"synonym": "降序", "term": "倒序"},
    ])
    assert r.replace("家用按降序") == "户用按倒序"


def test_synonym_substring_of_standard_term_is_protected():
    # 同义词是标准词的子串：恒等模式 + 最长匹配保护，不得把标准词改坏
    r = _replacer([{"synonym": "电站名", "term": "电站名称"}])
    assert r.replace("电站名称") == "电站名称"        # 标准词整体命中恒等，不被内部"电站名"改坏
    assert r.replace("电站名,装机") == "电站名称,装机"  # 单独出现的"电站名"仍被补全为标准词


def test_deterministic_same_input_same_output():
    r = _replacer([{"synonym": "家用", "term": "户用"}])
    q = "家用户用家用"
    assert r.replace(q) == r.replace(q)


def test_empty_and_whitespace():
    r = _replacer([{"synonym": "家用", "term": "户用"}])
    assert r.replace("") == ""


def test_loader_failure_degrades_to_identity_normalized():
    def _bad():
        raise RuntimeError("dynamo down")

    r = AhoCorasickTermReplacer(_bad, ttl_seconds=_BIG_TTL)
    # 首次加载失败 → 空自动机，不抛错，返回归一化文本
    assert r.replace("家用产品线") == "家用产品线"
