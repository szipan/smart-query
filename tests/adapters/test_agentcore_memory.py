"""AgentCoreMemory 单测（mock bedrock-agentcore client，不连真实服务）。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from adapters.agentcore_memory import AgentCoreMemory


class FakeAgentCore:
    def __init__(self, events=None) -> None:
        self._events = events or []
        self.created: list[dict] = []
        self.list_calls: list[dict] = []

    def create_event(self, **kwargs):
        self.created.append(kwargs)
        return {"event": {"eventId": "e1"}}

    def list_events(self, **kwargs):
        self.list_calls.append(kwargs)
        return {"events": list(self._events)}


def _conv(role, text):
    return {"conversational": {"role": role, "content": {"text": text}}}


def test_requires_memory_id() -> None:
    with pytest.raises(ValueError):
        AgentCoreMemory(memory_id="", client=FakeAgentCore())


def test_write_turn_builds_conversational_event() -> None:
    fake = FakeAgentCore()
    AgentCoreMemory(memory_id="mem-1", client=fake).write_turn(
        "1001", "s1", "本月发电量", "SELECT ps_id ...")
    call = fake.created[0]
    assert call["memoryId"] == "mem-1"
    assert call["actorId"] == "1001"
    assert call["sessionId"] == "s1"
    assert isinstance(call["eventTimestamp"], datetime)
    roles = [p["conversational"]["role"] for p in call["payload"]]
    assert roles == ["USER", "ASSISTANT"]
    assert call["payload"][0]["conversational"]["content"]["text"] == "本月发电量"


def test_write_turn_omits_empty_answer() -> None:
    fake = FakeAgentCore()
    AgentCoreMemory(memory_id="m", client=fake).write_turn("u", "s", "q", "")
    assert len(fake.created[0]["payload"]) == 1  # 只有 USER


def test_write_turn_default_session_when_none() -> None:
    fake = FakeAgentCore()
    AgentCoreMemory(memory_id="m", client=fake, default_session="D").write_turn("u", None, "q", "a")
    assert fake.created[0]["sessionId"] == "D"


def test_read_recent_turns_maps_events_in_time_order() -> None:
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    t1 = datetime(2026, 7, 2, tzinfo=timezone.utc)
    events = [
        {"eventTimestamp": t1, "payload": [_conv("USER", "q2"), _conv("ASSISTANT", "a2")]},
        {"eventTimestamp": t0, "payload": [_conv("USER", "q1"), _conv("ASSISTANT", "a1")]},
    ]
    fake = FakeAgentCore(events)
    turns = AgentCoreMemory(memory_id="m", client=fake).read_recent_turns("u", "s", 5)
    assert turns == [{"question": "q1", "answer": "a1"}, {"question": "q2", "answer": "a2"}]
    lc = fake.list_calls[0]
    assert lc["actorId"] == "u" and lc["sessionId"] == "s" and lc["includePayloads"] is True


def test_read_recent_turns_respects_limit() -> None:
    events = [
        {"eventTimestamp": datetime(2026, 7, d, tzinfo=timezone.utc),
         "payload": [_conv("USER", f"q{d}"), _conv("ASSISTANT", f"a{d}")]}
        for d in range(1, 6)
    ]
    turns = AgentCoreMemory(memory_id="m", client=FakeAgentCore(events)).read_recent_turns("u", "s", 2)
    assert [t["question"] for t in turns] == ["q4", "q5"]  # 最近两条


def test_read_recent_turns_empty() -> None:
    assert AgentCoreMemory(memory_id="m", client=FakeAgentCore([])).read_recent_turns("u", "s", 5) == []
