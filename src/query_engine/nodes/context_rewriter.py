"""上下文改写 / 指代消解节点（design.md §3；Requirements 2）。

基于 Memory 的最近轮会话，把追问改写为独立、自包含问题；Memory 读取失败则降级为原问题。
"""

from __future__ import annotations

from typing import Optional

from query_engine.config import RECENT_TURNS_LIMIT
from query_engine.deps import LlmClient, MemoryClient
from query_engine.models import QueryContext


class ContextRewriter:
    def __init__(self, memory: Optional[MemoryClient], llm: Optional[LlmClient],
                 recent_turns_limit: int = RECENT_TURNS_LIMIT) -> None:
        self._memory = memory
        self._llm = llm
        self._limit = recent_turns_limit

    def run(self, ctx: QueryContext) -> QueryContext:
        req = ctx.request
        raw = req.raw_question

        if self._memory is None:
            ctx.standalone_question = raw
            return ctx

        try:
            turns = self._memory.read_recent_turns(req.identity.user_id, req.session_id, self._limit)
        except Exception as e:  # 读取失败 → 降级（Req 2.4）
            ctx.standalone_question = raw
            ctx.memory_degraded = True
            ctx.warnings.append(f"Memory 读取失败降级: {e!r}")
            return ctx

        if not turns:  # 无历史 → 原问题（Req 2.2）
            ctx.standalone_question = raw
            return ctx

        # 有历史 → LLM 改写为独立问题（Req 2.3）
        if self._llm is None:
            ctx.standalone_question = raw
            return ctx
        history = "\n".join(f"Q: {t.get('question', '')}\nA: {t.get('answer', '')}" for t in turns)
        system = "你负责指代消解。把用户的追问改写为独立、自包含、无需依赖对话历史即可理解的问题。只输出改写后的问题。"
        prompt = f"对话历史:\n{history}\n\n追问:\n{raw}"
        rewritten = (self._llm.complete(system=system, prompt=prompt) or "").strip()
        ctx.standalone_question = rewritten or raw
        return ctx
