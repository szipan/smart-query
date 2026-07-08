"""Layer 4 —— Text-to-SQL Pipeline（插拔式）。

参见 .kiro/specs/text2sql-pipeline/design.md 与 requirements.md。
本期基线：最小可替换；不执行 SQL（Query Fixer 静态校验版，无 dry_run）；dialect 硬编码 starrocks；
只产出未校验 SQL，隔离/只读/权限一律不在本层（由 Layer 5 保证）。
"""
