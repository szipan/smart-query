"""Layer 3 配置常量。"""

DIALECT: str = "starrocks"

# 多轮上下文改写读取的最近轮数
RECENT_TURNS_LIMIT: int = 5

# 改写结果长度上限：超过视为 LLM 跑偏，回退原问题（防扩写/注入）
REWRITE_MAX_LEN: int = 2000

# 语义缓存命中阈值
CACHE_SIMILARITY_THRESHOLD: float = 0.95

# 向量命名空间（缓存与软术语库相互隔离）
CACHE_NAMESPACE: str = "semantic_cache"
GLOSSARY_NAMESPACE: str = "glossary"
