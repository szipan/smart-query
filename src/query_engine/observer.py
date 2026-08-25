"""可观测（design.md §11，Requirements 14）。日志脱敏，不落用户数据范围/凭证/结果内容。"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional, Protocol, runtime_checkable

from query_engine.models import NodeTrace, QueryContext


@runtime_checkable
class EngineObserver(Protocol):
    def on_node(self, trace: NodeTrace) -> None: ...


class InMemoryEngineObserver:
    def __init__(self) -> None:
        self.traces: list[NodeTrace] = []

    def on_node(self, trace: NodeTrace) -> None:
        self.traces.append(trace)


class NullEngineObserver:
    def on_node(self, trace: NodeTrace) -> None:
        return None


class VerboseEngineObserver:
    """开发调试用：每步打印详细输入/输出到控制台。

    接收 NodeTrace + QueryContext，打印该步骤产出的关键字段变化。
    """

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._log = logger or logging.getLogger("smart-query.pipeline")
        if not self._log.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
            ))
            self._log.addHandler(handler)
            self._log.setLevel(logging.DEBUG)

    def on_node(self, trace: NodeTrace) -> None:
        # 基础 trace 信息（无 ctx）
        elapsed = (trace.ended_at - trace.started_at) * 1000
        status = "OK" if trace.ok else f"FAIL: {trace.error}"
        self._log.info(f"  [{trace.node}] {status} ({elapsed:.0f}ms)")
        if trace.attrs:
            self._log.debug(f"    attrs: {trace.attrs}")

    def on_node_with_context(self, trace: NodeTrace, ctx: QueryContext) -> None:
        """增强版：打印步骤完成后 ctx 中的关键字段。由 orchestrator._node() 调用。"""
        elapsed = (trace.ended_at - trace.started_at) * 1000
        status = "OK" if trace.ok else f"FAIL: {trace.error}"
        sep = "─" * 60
        self._log.info(f"\n┌{sep}")
        self._log.info(f"│ Step: {trace.node}  |  {status}  |  {elapsed:.0f}ms")
        self._log.info(f"├{sep}")

        node = trace.node
        if node == "context_rewrite":
            self._log.info(f"│ input_question : {ctx.request.raw_question}")
            self._log.info(f"│ standalone_question: {ctx.standalone_question}")
            if ctx.memory_degraded:
                self._log.warning(f"│ ⚠ memory_degraded=True")

        elif node == "term_normalization":
            entities = [f"{e.surface}→{e.canonical}({e.entity_type})" for e in ctx.resolved_entities] if ctx.resolved_entities else []
            terms = [f"{t.term}: {t.definition[:40]}" for t in ctx.resolved_terms] if ctx.resolved_terms else []
            self._log.info(f"│ resolved_entities: {entities or '(none)'}")
            self._log.info(f"│ resolved_terms  : {terms or '(none)'}")

        elif node == "canonical_rewrite":
            self._log.info(f"│ canonical_question: {ctx.canonical_question}")
            if ctx.semantic_slots:
                slots = ctx.semantic_slots
                slot_info = {k: v for k, v in vars(slots).items() if v}
                self._log.info(f"│ semantic_slots: {slot_info}")

        elif node == "semantic_cache_lookup":
            if ctx.cache_lookup and ctx.cache_lookup.hit:
                self._log.info(f"│ cache: HIT (score={getattr(ctx.cache_lookup, 'score', '?')})")
                self._log.info(f"│ cached_sql: {ctx.cache_lookup.pre_isolation_sql}")
            else:
                score = getattr(ctx.cache_lookup, 'score', None) if ctx.cache_lookup else None
                self._log.info(f"│ cache: MISS (best_score={score})")

        elif node == "sql_generator":
            self._log.info(f"│ pre_isolation_sql: {ctx.pre_isolation_sql}")
            self._log.info(f"│ sql_source: {ctx.sql_source}")
            if trace.attrs:
                self._log.info(f"│ gen_status: {trace.attrs}")

        elif node == "security_guard":
            self._log.info(f"│ safe_sql: {ctx.safe_sql}")
            if trace.attrs:
                self._log.info(f"│ guard_status: {trace.attrs}")

        if ctx.warnings:
            for w in ctx.warnings:
                self._log.warning(f"│ ⚠ {w}")

        self._log.info(f"└{sep}")

