"""硬通道运行时匹配器（Aho-Corasick），实现 L3 的 EntityDictionary 契约。

- 从 loader 加载「别名→实体」行，构建纯 Python Aho-Corasick 自动机做多模式串匹配。
- match(question) 返回 [{surface, canonical, entity_type, entity_id}]，供 L3 HardEntityResolver。
- 消解：最长匹配、非重叠、从左到右；最小别名长度阈值（默认 2）过滤短词。
- 归一化与写入侧共用 knowledge_admin.models.normalize（Req 11.5）。
- 刷新：启动惰性加载 + TTL 周期性重建 + 显式 refresh()；加载失败降级为空（不阻断查询）。
- loader() -> list[dict]，每行含 alias_normalized / alias_original / canonical / entity_type / entity_id。
  典型 loader = DynamoDbEntityStore(...).scan_aliases（仅 enabled 实体的别名）。

参考 SQLBot「术语/实体召回」的思路，clean-room 重写；AC 自动机为自研实现。
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any, Callable, Optional

from knowledge_admin.models import normalize

DEFAULT_MIN_ALIAS_LEN = 2
DEFAULT_TTL_SECONDS = 300


class _Node:
    __slots__ = ("children", "fail", "outputs")

    def __init__(self) -> None:
        self.children: dict[str, _Node] = {}
        self.fail: Optional[_Node] = None
        # 该节点为终点的模式：(pattern_len, payload)
        self.outputs: list[tuple[int, dict[str, Any]]] = []


class _Automaton:
    """不可变的 Aho-Corasick 自动机（一次构建，多次匹配）。"""

    def __init__(self) -> None:
        self.root = _Node()

    def add(self, pattern: str, payload: dict[str, Any]) -> None:
        node = self.root
        for ch in pattern:
            node = node.children.setdefault(ch, _Node())
        node.outputs.append((len(pattern), payload))

    def build_fail(self) -> None:
        q: deque[_Node] = deque()
        for child in self.root.children.values():
            child.fail = self.root
            q.append(child)
        while q:
            cur = q.popleft()
            for ch, nxt in cur.children.items():
                f = cur.fail
                while f is not None and ch not in f.children:
                    f = f.fail
                nxt.fail = f.children[ch] if (f and ch in f.children) else self.root
                # 继承 fail 链上的输出（后缀模式）
                nxt.outputs = nxt.outputs + nxt.fail.outputs
                q.append(nxt)

    def find(self, text: str) -> list[tuple[int, int, dict[str, Any]]]:
        """返回 (start, end, payload)，end 为结束后一位（半开区间）。"""
        node = self.root
        hits: list[tuple[int, int, dict[str, Any]]] = []
        for i, ch in enumerate(text):
            while node is not None and ch not in node.children:
                node = node.fail
            node = node.children[ch] if (node and ch in node.children) else self.root
            for plen, payload in node.outputs:
                end = i + 1
                hits.append((end - plen, end, payload))
        return hits


def _resolve_longest_non_overlapping(
    hits: list[tuple[int, int, dict[str, Any]]]
) -> list[tuple[int, int, dict[str, Any]]]:
    """最长匹配、非重叠、从左到右。"""
    # 每个起点保留最长
    best_at_start: dict[int, tuple[int, int, dict[str, Any]]] = {}
    for h in hits:
        s = h[0]
        if s not in best_at_start or (h[1] - h[0]) > (best_at_start[s][1] - best_at_start[s][0]):
            best_at_start[s] = h
    result: list[tuple[int, int, dict[str, Any]]] = []
    covered = 0
    for s in sorted(best_at_start):
        if s < covered:
            continue
        h = best_at_start[s]
        result.append(h)
        covered = h[1]
    return result


class AhoCorasickEntityDictionary:
    """实现 L3 EntityDictionary 契约（match(question) -> list[dict]）。"""

    def __init__(self, loader: Callable[[], list[dict[str, Any]]], *,
                 min_alias_len: int = DEFAULT_MIN_ALIAS_LEN,
                 ttl_seconds: int = DEFAULT_TTL_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._loader = loader
        self._min_len = min_alias_len
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
                self._automaton = _Automaton()   # 首次加载失败 -> 空自动机
                self._automaton.build_fail()
                self._loaded_at = self._clock()
            return   # 已有可用自动机则保留
        ac = _Automaton()
        for row in rows:
            key = row.get("alias_normalized") or normalize(row.get("alias_original", ""))
            if not key or len(key) < self._min_len:
                continue
            ac.add(key, {
                "surface": row.get("alias_original", key),
                "canonical": row.get("canonical", ""),
                "entity_type": row.get("entity_type", ""),
                "entity_id": row.get("entity_id"),
            })
        ac.build_fail()
        self._automaton = ac
        self._loaded_at = self._clock()

    def _ensure_fresh(self) -> None:
        if self._automaton is None or (self._clock() - self._loaded_at) > self._ttl:
            self.refresh()

    def match(self, question: str) -> list[dict[str, Any]]:
        self._ensure_fresh()
        if self._automaton is None:
            return []
        text = normalize(question)
        if not text:
            return []
        hits = self._automaton.find(text)
        resolved = _resolve_longest_non_overlapping(hits)
        return [dict(payload) for _s, _e, payload in resolved]


def build_entity_dictionary_from_dynamodb(client: Any, entity_table: str, *,
                                          min_alias_len: int = DEFAULT_MIN_ALIAS_LEN,
                                          ttl_seconds: int = DEFAULT_TTL_SECONDS
                                          ) -> AhoCorasickEntityDictionary:
    """工厂：loader = DynamoDbEntityStore.scan_aliases（扫 entity 表展开 enabled 实体别名）。"""
    from knowledge_admin.dynamodb_stores import DynamoDbEntityStore

    store = DynamoDbEntityStore(client, entity_table, alias_table="")  # alias_table 不用于 scan_aliases
    return AhoCorasickEntityDictionary(store.scan_aliases, min_alias_len=min_alias_len,
                                       ttl_seconds=ttl_seconds)
