"""Task 7 单测：默认四策略（Requirements 2/4/5/6）。"""

import pytest

from text2sql_pipeline.errors import ConfigurationError, StagePreconditionError
from text2sql_pipeline.models import PipelineContext, PipelineInput, RetrievedContext, SqlCandidate
from text2sql_pipeline.stages.context_acquisition import RagContextAcquisition
from text2sql_pipeline.stages.query_fixer import BoundedRetryFixer
from text2sql_pipeline.stages.sql_generation import SingleLlmGeneration, extract_sql
from text2sql_pipeline.stages.sql_selection import PassthroughSelection


def _ctx(**kw):
    return PipelineContext(input=PipelineInput(request_id="r", standalone_question="查询发电量", **kw))


# ---- RAG 上下文获取 ----
def test_rag_writes_retrieved_context(deps) -> None:
    stage = RagContextAcquisition(deps, {})
    ctx = stage.run(_ctx())
    assert ctx.retrieved_context is not None
    assert ctx.retrieved_context.strategy_name == "rag"
    assert ctx.retrieved_context.schema_snippets


def test_rag_requires_embedder_and_vector_store(deps) -> None:
    deps.embedder = None
    with pytest.raises(ConfigurationError):
        RagContextAcquisition(deps, {})


# ---- 单 LLM 生成 ----
def test_single_llm_appends_one_candidate(deps) -> None:
    ctx = _ctx()
    ctx.retrieved_context = RetrievedContext(schema_snippets=["CREATE TABLE t (a int)"])
    stage = SingleLlmGeneration(deps, {})
    ctx = stage.run(ctx)
    assert len(ctx.sql_candidates) == 1
    c = ctx.sql_candidates[0]
    assert c.sql and c.generator_name == "single_llm" and c.model_id


def test_single_llm_requires_retrieved_context(deps) -> None:
    stage = SingleLlmGeneration(deps, {})
    with pytest.raises(StagePreconditionError):
        stage.run(_ctx())


def test_single_llm_system_forbids_permission_filters(deps) -> None:
    ctx = _ctx()
    ctx.retrieved_context = RetrievedContext(schema_snippets=["t"])
    SingleLlmGeneration(deps, {}).run(ctx)
    # 检查传给 LLM 的 system 明确禁止权限过滤
    sysmsg = deps.llm_client.calls[0]["system"]
    assert "org_id" in sysmsg and "隔离由下游" in sysmsg


def test_request_model_id_overrides_default(deps) -> None:
    ctx = PipelineContext(input=PipelineInput(request_id="r", standalone_question="q", model_id="my-model"))
    ctx.retrieved_context = RetrievedContext(schema_snippets=["t"])
    SingleLlmGeneration(deps, {}).run(ctx)
    assert ctx.sql_candidates[0].model_id == "my-model"


def test_extract_sql_strips_fence() -> None:
    assert extract_sql("```sql\nSELECT 1;\n```") == "SELECT 1"
    assert extract_sql("SELECT 2") == "SELECT 2"


# ---- 直通选择 ----
def test_passthrough_selects_first(deps) -> None:
    ctx = _ctx()
    ctx.sql_candidates = [SqlCandidate("SELECT 1", "g", "m"), SqlCandidate("SELECT 2", "g", "m")]
    ctx = PassthroughSelection(deps, {}).run(ctx)
    assert ctx.selected_sql.sql == "SELECT 1"


def test_passthrough_empty_candidates_raises(deps) -> None:
    with pytest.raises(StagePreconditionError):
        PassthroughSelection(deps, {}).run(_ctx())


# ---- Query Fixer（静态校验版）----
def test_fixer_no_validator_passthrough(deps) -> None:
    ctx = _ctx()
    ctx.selected_sql = SqlCandidate("SELECT 1", "g", "m")
    deps.validator = None
    ctx = BoundedRetryFixer(deps, {"max_attempts": 2}).run(ctx)
    assert ctx.final_sql is ctx.selected_sql


def test_fixer_static_pass_sets_final(deps) -> None:
    class OkValidator:
        def validate_for_probe(self, sql, request_id):
            return sql

    deps.validator = OkValidator()
    ctx = _ctx()
    ctx.selected_sql = SqlCandidate("SELECT ps_id FROM fact_power_station_kpi_month", "g", "m")
    ctx = BoundedRetryFixer(deps, {"max_attempts": 2}).run(ctx)
    assert ctx.final_sql is not None
    assert ctx.fix_attempts == 0


def test_fixer_static_fail_then_exhaust(deps) -> None:
    class BadValidator:
        def validate_for_probe(self, sql, request_id):
            raise ValueError("readonly violation")

    deps.validator = BadValidator()
    ctx = _ctx()
    ctx.selected_sql = SqlCandidate("DROP TABLE t", "g", "m")
    ctx = BoundedRetryFixer(deps, {"max_attempts": 2}).run(ctx)
    assert ctx.final_sql is None
    assert ctx.fix_attempts == 2
    assert any("用尽重试" in w for w in ctx.warnings)


def test_fixer_precondition_requires_selected(deps) -> None:
    with pytest.raises(StagePreconditionError):
        BoundedRetryFixer(deps, {}).run(_ctx())
