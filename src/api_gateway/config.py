"""Layer 2 配置常量。"""

# 问题长度上限
MAX_QUESTION_LEN: int = 2000

# 单用户默认配额（每窗口），未来可接用户分级服务
DEFAULT_USER_LIMIT: int = 200

# 限流窗口策略：按天
WINDOW_STRATEGY: str = "daily"

# 下游超时（一次性 JSON 路径，API Gateway 硬上限 29s）
ENGINE_TIMEOUT_S: int = 29
