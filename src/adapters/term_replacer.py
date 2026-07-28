"""规范化改写确定性替换源（Aho-Corasick），实现 L3 的 TermReplacer 契约。

- 从 loader 加载软术语库 enabled 术语的 ``synonyms→标准词``，构建纯 Python Aho-Corasick 自动机。
- replace(text)：对归一化后的文本做最长匹配、非重叠、从左到右替换为标准词；确定性、不调 LLM。
- 归一化与写入侧共用 ``knowledge_admin.models.normalize``（与硬通道匹配器一致）。
- 刷新：启动惰性加载 + TTL 周期重建 + 显式 refresh()；加载失败降级为空自动机（不阻断查询）。
- loader() -> list[dict]，每行含 ``synonym`` 与 ``term``（标准词）。

复用硬通道运行时匹配器同源的自研 AC 自动机（``adapters.entity_dictionary``），clean-room 思路。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from adapters.entity_dictionary import _Automaton, _resolve_longest_non_overlapping
from knowledge_admin.models import normalize

DEFAULT_MIN_SYNONYM_LEN = 2
DEFAULT_TTL_SECONDS = 300


class AhoCorasickTermReplacer:
    """实现 L3 TermReplacer 契约（replace(text) -> str）。"""

    def __init__(self, loader: Callable[[], list[dict[str, Any]]], *,
                 min_synonym_len: int = DEFAULT_MIN_SYNONYM_LEN,
                 ttl_seconds: int = DEFAULT_TTL_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._loader = loader
        self._min_len = min_synonym_len
        self._ttl = ttl_seconds
        self._clock = clock
        self._automaton: Optional[_Automaton] = None
        self._loaded_at: float = 0.0

    def refresh(self) -> None:
        """从 loader 重建自动机；失败保留上一份（降级不清空）。"""
        try:
            rows = self._loader() or []
        except Exception:
            if self._automaton is None:
                self._automaton = _Automaton()
                self._automaton.build_fail()
                self._loaded_at = self._clock()
            return
        ac = _Automaton()
        terms: set[str] = set()
        for row in rows:
            term = (row.get("term") or "").strip()
            syn = row.get("synonym") or ""
            key = normalize(syn)
            if not term:
                continue
            terms.add(term)
            if not key or len(key) < self._min_len:
                continue
            # 标准词自身不作为同义词替换（恒等模式在下方统一登记）
            if key == normalize(term):
                continue
            ac.add(key, {"term": term})
        # 为每个标准词登记恒等模式：借"最长匹配"保护标准词，
        # 使其不被自身子串的同义词误替换（如 电站名→电站名称 不会把"电站名称"改坏）。
        for term in terms:
            tkey = normalize(term)
            if tkey:
                ac.add(tkey, {"term": term})
        ac.build_fail()
        self._automaton = ac
        self._loaded_at = self._clock()

    def _ensure_fresh(self) -> None:
        if self._automaton is None or (self._clock() - self._loaded_at) > self._ttl:
            self.refresh()

    def replace(self, text: str) -> str:
        self._ensure_fresh()
        norm = normalize(text)
        if self._automaton is None or not norm:
            return norm or (text or "")
        hits = self._automaton.find(norm)
        resolved = _resolve_longest_non_overlapping(hits)   # 已按起点升序、非重叠
        if not resolved:
            return norm
        out: list[str] = []
        cursor = 0
        for start, end, payload in resolved:
            out.append(norm[cursor:start])
            out.append(payload["term"])
            cursor = end
        out.append(norm[cursor:])
        return "".join(out)


def build_term_replacer_from_dynamodb(client: Any, glossary_table: str, *,
                                      name_table: str = "",
                                      min_synonym_len: int = DEFAULT_MIN_SYNONYM_LEN,
                                      ttl_seconds: int = DEFAULT_TTL_SECONDS
                                      ) -> AhoCorasickTermReplacer:
    """工厂：loader = DynamoDbTermStore.scan（展开 enabled 术语的 synonyms→标准词）。"""
    from knowledge_admin.dynamodb_stores import DynamoDbTermStore

    store = DynamoDbTermStore(client, glossary_table, name_table)

    def _loader() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for term in store.scan():
            if not getattr(term, "enabled", True):
                continue
            for syn in getattr(term, "synonyms", ()):
                rows.append({"synonym": syn, "term": term.term})
        return rows

    return AhoCorasickTermReplacer(_loader, min_synonym_len=min_synonym_len, ttl_seconds=ttl_seconds)
