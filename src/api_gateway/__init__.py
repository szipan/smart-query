"""Layer 2 —— api-gateway（统一入口 · 网关守门人）。

参见 .kiro/specs/api-gateway/design.md 与 requirements.md。
本期基线：只做变体 A（一次性 JSON），不实现流式；认证选项 A（网关终结）；
认证/身份构造/限流为传输无关的共享库；响应整形消费点为可分块抽象（本期单块）；
薄层零智能——不生成 SQL、不查库、不调 LLM、不解析数据范围。
"""
