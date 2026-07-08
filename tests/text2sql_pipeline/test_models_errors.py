"""Task 2/3 单测：异常与数据模型。"""

from text2sql_pipeline.errors import (
    DuplicateStrategyError,
    PipelineError,
    StagePreconditionError,
    StrategyResolutionError,
    UnknownStrategyError,
)
from text2sql_pipeline.models import PipelineConfig, PipelineInput, PipelineStatus


def test_errors_subclass_base() -> None:
    for e in [
        StrategyResolutionError("s", "x"),
        UnknownStrategyError("s", "x"),
        DuplicateStrategyError("s", "x"),
        StagePreconditionError("s", "f"),
    ]:
        assert isinstance(e, PipelineError)


def test_stage_precondition_carries_field() -> None:
    e = StagePreconditionError("sql_generation", "retrieved_context")
    assert e.stage_type == "sql_generation"
    assert e.missing_field == "retrieved_context"
    assert "retrieved_context" in str(e)


def test_pipeline_input_has_no_scope_fields() -> None:
    pi = PipelineInput(request_id="r", standalone_question="q")
    assert not hasattr(pi, "org_ids")
    assert not hasattr(pi, "ps_ids")


def test_pipeline_config_defaults() -> None:
    c = PipelineConfig()
    assert c.context_acquisition.strategy == "rag"
    assert c.sql_generation.strategy == "single_llm"
    assert c.sql_selection.strategy == "passthrough"
    assert c.query_fixer.strategy == "bounded_retry"
    assert c.query_fixer.params.get("max_attempts") == 2


def test_pipeline_status_values() -> None:
    assert {s.value for s in PipelineStatus} == {"ok", "no_sql", "fixer_exhausted", "error"}
