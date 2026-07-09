"""上下文改写 / 指代消解节点（design.md §3；Requirements 2）。

基于 Memory 的最近轮会话，把追问改写为独立、自包含问题；Memory 读取失败则降级为原问题。
"""

from __future__ import annotations

from typing import Optional

from query_engine.config import RECENT_TURNS_LIMIT, REWRITE_MAX_LEN
from query_engine.deps import LlmClient, MemoryClient
from query_engine.models import QueryContext


def _turn_answer(turn: dict) -> str:
    """兼容不同 Memory 实现的答案字段名（answer / answer_summary）。"""
    return turn.get("answer") or turn.get("answer_summary") or ""


def _clean_rewrite(text: str) -> str:
    """清洗 LLM 改写输出：去空白、去成对引号/代码围栏。"""
    t = (text or "").strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in ("\"", "'", "`"):
        t = t[1:-1].strip()
    return t


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
        history = "\n".join(f"Q: {t.get('question', '')}\nA: {_turn_answer(t)}" for t in turns)
        system = "你负责指代消解。把用户的追问改写为独立、自包含、无需依赖对话历史即可理解的问题。只输出改写后的问题。"
        prompt = f"对话历史:\n{history}\n\n追问:\n{raw}"
        try:
            rewritten = _clean_rewrite(self._llm.complete(system=system, prompt=prompt))
        except Exception as e:  # LLM 调用失败 → 降级为原问题，不让链路中断
            ctx.standalone_question = raw
            ctx.warnings.append(f"改写 LLM 调用失败降级: {e!r}")
            return ctx
        # 保护：空 → 回退；异常超长（跑偏/被注入扩写）→ 回退并记 warning
        if not rewritten:
            ctx.standalone_question = raw
        elif len(rewritten) > REWRITE_MAX_LEN:
            ctx.standalone_question = raw
            ctx.warnings.append(
                f"改写结果超长({len(rewritten)}>{REWRITE_MAX_LEN})，回退原问题")
        else:
            ctx.standalone_question = rewritten
        return ctx
