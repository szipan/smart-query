"""Layer 3 —— query-engine（核心编排引擎）。

参见 .kiro/specs/query-engine/design.md 与 requirements.md。
本期基线：除"物理执行/取数"外全实现（Memory 多轮、术语归一化硬+软、语义缓存、委派 L4/L5）；
跳过 execute 节点，Answer 携带 safe_sql（无 rows），AnswerStatus.OK = L5 ALLOWED；
缓存 store 放宽为"未命中 ∧ L5 通过"；dialect 硬编码 starrocks。
"""
