"""text2sql_pipeline 测试共享 fixture（mock 依赖见 fakes.py）。"""

from __future__ import annotations

import pytest

from text2sql_pipeline.deps import StageDependencies
from text2sql_pipeline.models import PipelineInput

from fakes import FakeDatasource, FakeEmbedder, FakeLlm, FakeVectorStore


@pytest.fixture
def deps() -> StageDependencies:
    return StageDependencies(
        embedder=FakeEmbedder(),
        vector_store=FakeVectorStore(),
        llm_client=FakeLlm(),
        datasource=FakeDatasource(),
    )


@pytest.fixture
def pipeline_input() -> PipelineInput:
    return PipelineInput(request_id="req-1", standalone_question="查询本月各电站发电量")
