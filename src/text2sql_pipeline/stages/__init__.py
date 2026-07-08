"""默认阶段策略实现。导入本包即触发各策略通过 @register 登记到 REGISTRY。"""

from text2sql_pipeline.stages import (  # noqa: F401
    context_acquisition,
    query_fixer,
    sql_generation,
    sql_selection,
)
