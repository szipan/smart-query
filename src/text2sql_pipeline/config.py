"""Layer 4 配置常量。"""

# SQL 方言（本期硬编码；未来经 DatasourceAdapter.dialect 注入）
DIALECT: str = "starrocks"

# 默认 Bedrock 模型（可被 PipelineInput.model_id 或策略 params 覆盖）。
# 真实接入优先由 wiring 经环境变量 SMARTQUERY_LLM_MODEL_ID 注入 request.model_id；
# 此常量仅作兜底，取实测可用的跨区推理模型。
DEFAULT_MODEL_ID: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
