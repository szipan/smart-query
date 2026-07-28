"""Layer 3 配置常量。"""

DIALECT: str = "starrocks"

# 多轮上下文改写读取的最近轮数
RECENT_TURNS_LIMIT: int = 5

# 改写结果长度上限：超过视为 LLM 跑偏，回退原问题（防扩写/注入）
REWRITE_MAX_LEN: int = 2000

# 语义缓存向量召回阈值：有"槽位指纹门禁"保证精确率，故可放宽以提升召回（0.85）。
# 门禁按关键槽位精确判等拒误命中，向量仅负责召回候选，因此低阈值不会放大误命中。
CACHE_SIMILARITY_THRESHOLD: float = 0.85

# 语义缓存向量召回候选数（有槽位门禁兜底，取多个候选逐一门禁）
CACHE_TOP_K: int = 5

# 向量命名空间（缓存与软术语库相互隔离）
CACHE_NAMESPACE: str = "semantic_cache"
GLOSSARY_NAMESPACE: str = "glossary"

# —— 规范化改写（canonical_rewrite）——
# 阶段B 是否启用 LLM 结构规范化（可开关，便于降级/离线测试）
CANONICAL_LLM_ENABLED: bool = True
# 阶段B LLM 输出长度上限；超长回退阶段A 结果（Rewrite_Max_Len）
CANONICAL_REWRITE_MAX_LEN: int = 200
