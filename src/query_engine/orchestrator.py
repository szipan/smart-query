"""查询编排器（design.md §7；Requirements 1/8/9/10/11/12/13/14）。

固定顺序：context_rewrite → term_normalization → semantic_cache_lookup →
（命中: L5 | 未命中: L4 → L5）→〔本期跳过 execute〕→ 组装 Answer → 写回 Memory。

本期基线：
- 不执行 SQL：跳过 execute 节点；Answer 携带 safe_sql（无 rows）；OK = L5 ALLOWED。
- 缓存 store 放宽为"未命中(PIPELINE) ∧ L5 ALLOWED"→ 存隔离前 SQL（Property 9 本期适配）。
两条红线不受影响：命中仍过 L5；缓存只存隔离前 SQL。
"""

from __future__ import annotations

import time
from typing import Optional

from query_engine.deps import EngineDependencies, Layer4Request
from query_engine.models import (
    Answer,
    AnswerStatus,
    NodeTrace,
    QueryContext,
    QueryRequest,
    QuerySource,
)
from query_engine.nodes.context_rewriter import ContextRewriter
from query_engine.nodes.semantic_cache import SemanticCache
from query_engine.nodes.term_normalizer import HardEntityResolver, SoftGlossaryResolver, TermNormalizer
from query_engine.observer import EngineObserver, NullEngineObserver


def _status_str(status) -> str:
    """兼容 str Enum 与裸字符串。"""
    return str(getattr(status, "value", status))


class QueryOrchestrator:
    def __init__(self, deps: EngineDependencies, observer: Optional[EngineObserver] = None) -> None:
        self._deps = deps
        self._obs = observer or NullEngineObserver()
        self._rewriter = ContextRewriter(deps.memory, deps.llm)
        self._term = TermNormalizer(
            HardEntityResolver(deps.entity_dictionary),
            SoftGlossaryResolver(deps.embedder, deps.vector_store),
        )
        self._cache = SemanticCache(deps.embedder, deps.vector_store)

    # ---- 节点执行包装：计时 + 追踪 ----
    def _node(self, ctx: QueryContext, name: str, fn) -> None:
        started = time.monotonic()
        ok, err = True, None
        try:
            fn(ctx)
        except Exception as e:
            ok, err = False, repr(e)
            raise
        finally:
            trace = NodeTrace(node=name, started_at=started, ended_at=time.monotonic(), ok=ok, error=err)
            ctx.traces.append(trace)
            self._obs.on_node(trace)

    def run(self, request: QueryRequest) -> Answer:
        ctx = QueryContext(request=request)

        # ① 上下文改写
        self._node(ctx, "context_rewrite", self._rewriter.run)
        # ② 术语归一化
        self._node(ctx, "term_normalization", self._term.run)
        # ③ 语义缓存 lookup
        self._node(ctx, "semantic_cache_lookup", self._lookup_node)

        # ④ 分支：命中短路 L4，未命中走 L4；两分支都进 L5
        if ctx.cache_lookup and ctx.cache_lookup.hit:
            ctx.pre_isolation_sql = ctx.cache_lookup.pre_isolation_sql
            ctx.sql_source = QuerySource.CACHE_HIT
        else:
            terminal = self._invoke_layer4(ctx)
            if terminal is not None:
                return self._finalize(ctx, terminal)

        # ⑤ Layer 5 守卫（命中/未命中都经此）
        terminal = self._invoke_layer5(ctx)
        if terminal is not None:
            return self._finalize(ctx, terminal)

        # ⑥ 本期跳过 execute（不执行、不取数）—— 直接以 safe_sql 组装 OK 答案
        answer = Answer(
            request_id=request.request_id,
            status=AnswerStatus.OK,
            safe_sql=ctx.safe_sql,
            rows=None,
            sql_source=ctx.sql_source,
            request_traces=tuple(ctx.traces),
        )

        # ⑦ 缓存 store（本期放宽：未命中(PIPELINE) ∧ L5 通过 → 存隔离前 SQL）
        if ctx.sql_source == QuerySource.PIPELINE:
            try:
                self._cache.store(ctx.standalone_question, ctx.pre_isolation_sql)
            except Exception as e:
                ctx.warnings.append(f"缓存 store 失败: {e!r}")

        return self._finalize(ctx, answer)

    # ---- 节点：缓存 lookup（失败按未命中 + warning）----
    def _lookup_node(self, ctx: QueryContext) -> None:
        try:
            ctx.cache_lookup = self._cache.lookup(ctx.standalone_question)
        except Exception as e:
            ctx.cache_lookup = None
            ctx.warnings.append(f"缓存 lookup 失败按未命中处理: {e!r}")

    # ---- 委派 Layer 4 ----
    def _invoke_layer4(self, ctx: QueryContext) -> Optional[Answer]:
        req = ctx.request
        started = time.monotonic()
        l4req = Layer4Request(
            request_id=req.request_id,
            standalone_question=ctx.standalone_question or req.raw_question,
            resolved_entities=tuple(ctx.resolved_entities),
            resolved_terms=tuple(ctx.resolved_terms),
            datasource_id=req.datasource_id,
            model_id=req.model_id,
        )
        result = self._deps.layer4.generate(l4req)
        status = _status_str(result.status)
        self._trace_downstream(ctx, "layer4_pipeline", started, {"status": status})

        if status == "ok":
            ctx.pre_isolation_sql = result.unvalidated_sql
            ctx.sql_source = QuerySource.PIPELINE
            return None
        if status == "no_sql":
            return Answer(request_id=req.request_id, status=AnswerStatus.NO_SQL,
                          message="无法为该问题生成查询", request_traces=tuple(ctx.traces))
        # error / fixer_exhausted / 其他
        return Answer(request_id=req.request_id, status=AnswerStatus.UPSTREAM_ERROR,
                      message="生成查询时出错", request_traces=tuple(ctx.traces))

    # ---- 委派 Layer 5（每请求按身份构造，统一入口）----
    def _invoke_layer5(self, ctx: QueryContext) -> Optional[Answer]:
        req = ctx.request
        started = time.monotonic()
        guard = self._deps.guard_factory.for_identity(req.identity)  # 身份逐字段透传
        gr = guard.guard(ctx.pre_isolation_sql, req.request_id)
        ctx.guard_result = gr
        gstatus = _status_str(gr.status)
        self._trace_downstream(ctx, "layer5_guard", started, {"status": gstatus})

        if gstatus != "allowed":
            return Answer(request_id=req.request_id, status=AnswerStatus.REJECTED,
                          message="查询被安全策略拒绝", sql_source=ctx.sql_source,
                          request_traces=tuple(ctx.traces))
        ctx.safe_sql = getattr(gr, "safe_sql", None)
        return None

    def _trace_downstream(self, ctx: QueryContext, name: str, started: float, attrs: dict) -> None:
        trace = NodeTrace(node=name, started_at=started, ended_at=time.monotonic(), ok=True, attrs=attrs)
        ctx.traces.append(trace)
        self._obs.on_node(trace)

    # ---- 收尾：写回 Memory（best-effort）+ 附 traces ----
    def _finalize(self, ctx: QueryContext, answer: Answer) -> Answer:
        if self._deps.memory is not None:
            try:
                summary = answer.safe_sql or answer.message or answer.status.value
                self._deps.memory.write_turn(
                    ctx.request.identity.user_id, ctx.request.session_id,
                    ctx.standalone_question or ctx.request.raw_question, summary or "")
            except Exception as e:
                ctx.warnings.append(f"Memory 写回失败: {e!r}")
        # 确保 traces 最新
        object.__setattr__(answer, "request_traces", tuple(ctx.traces))
        return answer
