"""Layer 5 配置与常量。

本期 dialect 硬编码为 "starrocks"；未来由 Layer 7 DatasourceAdapter.dialect 注入，
届时把 DIALECT 的取值来源改为注入即可，其余隔离/校验逻辑不变（Requirements 11.1~11.3）。
"""

# SQL 方言（sqlglot 解析/序列化所用）。本期常量注入，未来经 L7 注入。
DIALECT: str = "starrocks"
