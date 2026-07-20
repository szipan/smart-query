"""知识管理后端（knowledge-admin）：软/硬通道术语与实体的管理面服务。

本期只实现后端（FastAPI）。软通道术语写入向量库（OpenSearch glossary 命名空间）供 L3
软通道检索；硬通道实体存 DynamoDB，并由 Aho-Corasick 匹配器（adapters/entity_dictionary）
在查询面消费。参见 .kiro/specs/knowledge-admin/。
"""
