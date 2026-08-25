"""Layer 3 测试共享 fixture。"""

from __future__ import annotations

import pytest

from query_engine.deps import EngineDependencies
from query_engine.models import CallerIdentity, QueryRequest

from qe_fakes import (
    FakeEmbedder,
    FakeEntityDictionary,
    FakeGuardFactory,
    FakeSqlGeneratorClient,
    FakeLlm,
    FakeMemory,
    FakeVectorStore,
)


@pytest.fixture
def deps() -> EngineDependencies:
    return EngineDependencies(
        memory=FakeMemory(),
        llm=FakeLlm(),
        embedder=FakeEmbedder(),
        vector_store=FakeVectorStore(),
        entity_dictionary=FakeEntityDictionary(),
        sql_generator=FakeSqlGeneratorClient(),
        guard_factory=FakeGuardFactory(),
        datasource=None,  # 本期不执行
    )


@pytest.fixture
def request_obj() -> QueryRequest:
    return QueryRequest(
        request_id="req-1",
        identity=CallerIdentity(user_id="1001"),
        raw_question="本月各电站发电量",
        session_id="s1",
    )
