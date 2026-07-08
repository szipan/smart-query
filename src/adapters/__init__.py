"""真实外部依赖适配器（Bedrock / OpenSearch / DynamoDB）。

这些适配器实现各层 deps.py 中定义的 Protocol 接口，通过依赖注入接入四层核心逻辑，
不改动核心逻辑本身。测试用 mock 注入，不连真实服务。
"""
