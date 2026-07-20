"""AhoCorasickEntityDictionary 单测（内存 loader，不连真实服务）。"""

from __future__ import annotations

from adapters.entity_dictionary import AhoCorasickEntityDictionary


def _row(alias, canonical, et, eid):
    return {"alias_original": alias, "canonical": canonical, "entity_type": et, "entity_id": eid}


def _rows():
    return [
        _row("华能一站", "华能一站电站", "power_station", "1001"),
        _row("一号站", "华能一站电站", "power_station", "1001"),
        _row("满发小时", "满发小时", "metric", "p83025"),
    ]


def _dict(rows, **kw):
    return AhoCorasickEntityDictionary(lambda: list(rows), **kw)


def test_match_finds_entities_in_question() -> None:
    d = _dict(_rows())
    hits = d.match("华能一站上个月满发小时是多少")
    keys = {(h["entity_type"], h["entity_id"]) for h in hits}
    assert ("power_station", "1001") in keys
    assert ("metric", "p83025") in keys
    # 命中带 surface + canonical
    ps = next(h for h in hits if h["entity_id"] == "1001")
    assert ps["surface"] == "华能一站" and ps["canonical"] == "华能一站电站"


def test_no_match_returns_empty() -> None:
    assert _dict(_rows()).match("今天天气怎么样") == []


def test_longest_match_wins_on_overlap() -> None:
    # "华能一站" 与 "一站" 均登记，问题含"华能一站" -> 取最长
    rows = [
        _row("华能一站", "华能一站电站", "power_station", "1001"),
        _row("一站", "别的实体", "power_station", "9999"),
    ]
    hits = _dict(rows).match("华能一站发电量")
    assert len(hits) == 1 and hits[0]["entity_id"] == "1001"


def test_min_alias_length_filters_short() -> None:
    rows = [_row("站", "短别名", "x", "1"), _row("华能一站", "华能一站电站", "power_station", "1001")]
    hits = _dict(rows, min_alias_len=2).match("华能一站的站")
    ids = {h["entity_id"] for h in hits}
    assert "1" not in ids and "1001" in ids   # "站" 因长度<2 被过滤


def test_normalization_consistency() -> None:
    # 别名大小写/全角，问题用不同写法也能命中
    rows = [_row("ＰＲ值", "性能比", "metric", "pr")]
    hits = _dict(rows).match("这个电站的 pr值 是多少")
    assert len(hits) == 1 and hits[0]["entity_id"] == "pr"


def test_loader_failure_degrades_to_empty() -> None:
    def bad_loader():
        raise RuntimeError("dynamodb down")

    d = AhoCorasickEntityDictionary(bad_loader)
    assert d.match("华能一站") == []   # 不抛出，降级为空


def test_ttl_triggers_reload() -> None:
    rows = [_row("华能一站", "c", "power_station", "1001")]
    fake_time = {"t": 1000.0}
    d = AhoCorasickEntityDictionary(lambda: list(rows), ttl_seconds=100,
                                    clock=lambda: fake_time["t"])
    assert len(d.match("华能一站")) == 1
    # 新增别名后，未超 TTL 不重载
    rows.append(_row("二号站", "c2", "power_station", "2002"))
    assert len(d.match("二号站")) == 0
    # 超过 TTL -> 重载生效
    fake_time["t"] += 200
    assert len(d.match("二号站")) == 1


def test_explicit_refresh() -> None:
    rows = [_row("华能一站", "c", "power_station", "1001")]
    d = AhoCorasickEntityDictionary(lambda: list(rows), ttl_seconds=10_000)
    d.match("x")
    rows.append(_row("二号站", "c2", "power_station", "2002"))
    d.refresh()
    assert len(d.match("二号站")) == 1
