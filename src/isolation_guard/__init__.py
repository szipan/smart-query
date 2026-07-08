"""Layer 5 —— isolation-guard（隔离守卫 · 安全红线）。

参见 .kiro/specs/isolation-guard/design.md 与 requirements.md。
本期基线：不执行 SQL、零 DB 连接、子查询注入、dialect 硬编码 starrocks。
"""
