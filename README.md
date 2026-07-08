# 智能问数系统 v2 · 设计交付包

这是智能问数（Text-to-SQL）系统 v2 的**纯设计产物**打包，不含任何实现源代码。用于迁移到新机器 / 新代码库继续落地。

## 目录结构

```
smart-query-design/
├── .kiro/
│   └── specs/                          # 六层 spec（每层含 design.md + requirements.md + .config.kiro）
│       ├── api-gateway/                # Layer 2
│       ├── query-engine/               # Layer 3
│       ├── text2sql-pipeline/          # Layer 4
│       ├── isolation-guard/            # Layer 5
│       ├── connection-proxy/           # Layer 6
│       └── datasource-adapter/         # Layer 7
├── docs/
│   ├── smart-query-architecture-v2.md  # 架构说明（研发版，含七层设计 / 关键决策 / 安全红线 / 选型结论）
│   └── datascout-gap-analysis.md       # 现状与目标架构差距分析
├── smart-query-architecture-v2.drawio  # 架构图（draw.io）
├── handoff-context.md                  # 设计交接上下文（新对话可用 # 手动带入 Kiro）
└── README.md                           # 本文件
```

## 现状

- 六层 spec 的 **设计 + 需求已全部完成**，正确性属性与需求验收标准双向追溯已连通。
- **tasks.md 尚未生成**。下一步按依赖顺序生成任务清单：L5 → L7 → L6 → L4 → L3 → L2。

## 在新机器 / 新项目里怎么用

1. 把本文件夹内容并入你选定的新代码库（`.kiro/specs/` 与 `docs/` 直接放到新项目根下即可被 Kiro 识别）。
2. 打开新项目，在 Kiro 对话里用 `#` 引入 `handoff-context.md` 接续设计讨论。
3. 如需版本管理，在新项目里正常 `git add` 提交即可（本包未单独初始化 git）。

## 阅读顺序建议

`docs/smart-query-architecture-v2.md`（全局与决策）→ `handoff-context.md`（速览与约定）→ 各层 `.kiro/specs/*/design.md` 与 `requirements.md`。
