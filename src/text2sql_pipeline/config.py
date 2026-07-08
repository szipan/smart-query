"""Layer 4 配置常量。"""

# SQL 方言（本期硬编码；未来经 DatasourceAdapter.dialect 注入）
DIALECT: str = "starrocks"

# 默认 Bedrock 模型（可被 PipelineInput.model_id 或策略 params 覆盖）
DEFAULT_MODEL_ID: str = "anthropic.claude-3-5-sonnet-20240620-v1:0"
