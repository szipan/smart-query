"""Bedrock AgentCore Memory 适配器（实现 L3 ``MemoryClient`` Protocol）。

用 AgentCore 短期记忆（events）承载多轮会话：
- ``write_turn`` -> ``create_event``：把一轮的问题(USER)+答案摘要(ASSISTANT)写成一个事件。
- ``read_recent_turns`` -> ``list_events``：按 actor+session 拉最近若干事件，映射回
  ``{"question","answer"}`` 供上下文改写用。

隔离：``actorId = user_id``（per-user actor 隔离，符合设计）；``sessionId`` 为会话 id。
本适配器只用短期 events，不建长期记忆策略（保持最小资源）。底层 boto3 client 依赖注入，
单测用 mock，不连真实服务。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


class AgentCoreMemory:
    def __init__(
        self,
        *,
        memory_id: str,
        client: Any = None,
        region: Optional[str] = None,
        default_session: str = "default",
    ) -> None:
        if not memory_id:
            raise ValueError("AgentCoreMemory 需要 memory_id")
        self._memory_id = memory_id
        self._client = client if client is not None else _make_client(region)
        self._default_session = default_session

    def _sid(self, session_id: Optional[str]) -> str:
        # ListEvents 要求 sessionId 必填；None 时用默认会话
        return session_id or self._default_session

    # ---------- 写入一轮 ----------
    def write_turn(self, actor_id: str, session_id: Optional[str],
                   question: str, answer_summary: str) -> None:
        payload: list[dict] = [
            {"conversational": {"role": "USER", "content": {"text": question or ""}}}
        ]
        if answer_summary:
            payload.append(
                {"conversational": {"role": "ASSISTANT", "content": {"text": answer_summary}}}
            )
        self._client.create_event(
            memoryId=self._memory_id,
            actorId=actor_id,
            sessionId=self._sid(session_id),
            eventTimestamp=datetime.now(timezone.utc),
            payload=payload,
        )

    # ---------- 读最近若干轮 ----------
    def read_recent_turns(self, actor_id: str, session_id: Optional[str],
                          limit: int) -> list[dict]:
        resp = self._client.list_events(
            memoryId=self._memory_id,
            actorId=actor_id,
            sessionId=self._sid(session_id),
            includePayloads=True,
            maxResults=max(1, limit),
        )
        events = resp.get("events", []) or []
        # 按时间升序，保证历史顺序稳定；取最近 limit 条
        events.sort(key=_event_ts)
        turns: list[dict] = []
        for ev in events[-limit:]:
            turns.append(_event_to_turn(ev))
        return turns


def _event_ts(ev: dict):
    ts = ev.get("eventTimestamp")
    return ts if ts is not None else datetime.min.replace(tzinfo=timezone.utc)


def _event_to_turn(ev: dict) -> dict:
    """从一个事件的 payload 提取首个 USER 文本为 question、首个 ASSISTANT 文本为 answer。"""
    q, a = "", ""
    for item in ev.get("payload", []) or []:
        conv = item.get("conversational") or {}
        text = (conv.get("content") or {}).get("text", "")
        role = conv.get("role")
        if role == "USER" and not q:
            q = text
        elif role == "ASSISTANT" and not a:
            a = text
    return {"question": q, "answer": a}


def _make_client(region: Optional[str]):
    import boto3  # 延迟导入

    return boto3.client("bedrock-agentcore", region_name=region)
