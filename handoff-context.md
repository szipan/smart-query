# 智能问数系统 v2 · 设计交接上下文

> 本文件用于把既有设计讨论带到新机器 / 新代码库。它**不是** steering（不会自动进入对话上下文）；在新项目里需要时，用 `#` 手动把它带入 Kiro 对话即可接续。

## 这是什么

面向 C 端（移动端 / Web / 外部应用）的**自然语言问数系统（Text-to-SQL）**，服务阳光云光伏业务数据（StarRocks 数仓）。核心诉求：**准确、低延迟、强数据隔离、成本可控**。对外以 REST（`POST /v1/query`）暴露，供 iOS / Android / Web / 外部应用调用。

**本 bundle 只含设计产物**（spec + 架构文档 + 图），不含实现源代码。实现将基于另行选定的、更合适的代码库进行。原先评估用的 `data-agent-demo`（AgentCore 多智能体平台）源代码**已决定不复用、不带入**。

## 七层架构（主干自上而下）

- **Layer 1 客户端**：iOS / Android / Web / 外部应用，统一 REST。
- **Layer 2 api-gateway**：API Gateway + 薄 Lambda（认证 / 边缘限流 / 单用户业务限流 / Legacy 路由 / 身份透传）。旁路 `legacy-router`。
- **Layer 3 query-engine**（EKS 上 Python 服务）：编排器 + 上下文改写（多轮，AgentCore Memory）+ 术语归一化（硬字典 + 软术语库）+ 语义复用缓存（命中即短路）。
- **Layer 4 text2sql-pipeline**（嵌于 L3，插拔式）：Schema Linking → SQL Generation → Selection → Query Fixer。
- **Layer 5 isolation-guard**（安全关键，自研 sqlglot）：权限解析 → 隔离注入 → 只读校验。**不可绕过**。
- **Layer 6 connection-proxy**：ProxySQL 常驻 EKS，连接池 / 复用 / 限流 / 故障隔离 / 只读路由。基础设施 + 配置契约，非业务代码。
- **Layer 7 datasource-adapter**：可插拔 `DatasourceAdapter`，当前仅 StarRocks 只读实现。`ResultSet` 的归属层。

## 关键设计决策（务必延续）

- **引擎跑 EKS，不用 AgentCore Runtime 托管**：引擎有连接池与共享热状态，Runtime 的每会话 microVM 与连接池冲突且高并发成本更高。
- **Layer 5 隔离自研 sqlglot**，不采用 WrenAI 的 MDL（WrenAI 核心 `wren-core-py` 是 Apache-2.0，可作为隔离规则日趋复杂时的备选）。
- **多轮会话用 AgentCore Memory**（per-user actor 隔离 + 短期上下文 + 长期偏好，SDK 从 EKS 调用）。
- **凭证 / OAuth token 用 AgentCore Identity**（token vault + 代表用户换 token；EKS 上需 SDK 自取 workload token 并显式传终端用户 id）。
- **向量栈可替换，当前 OpenSearch**；`Embedder` 当前 Bedrock/Cohere。`VectorStore`/`Embedder` L3、L4 共用。
- **数据源适配层可插拔，当前仅 StarRocks**，预留 MySQL/Spark。
- **语义缓存只存"隔离前 SQL 模板"，命中仍过 Layer 5**（按当前用户重注隔离；严禁缓存带具体 org_id/ps_id 的成品 SQL → 否则跨用户越权）。
- **只读强制在代码层做**（Layer 5 sqlglot AST 禁非 SELECT + DB 只读账号双保险）。
- **术语归一化分硬 + 软**：关键实体（电站名/指标名）确定性字典硬归一；一般描述词术语库软归一（LLM 中介）。
- 本期**不做**图表可视化、不做对外 MCP Server、不与 data-agent 整合。

## 贯穿全栈的关键契约（已对齐一致）

- **三道彼此独立的只读防线**：L5 确定性只读 AST 校验（权威）→ L6 只读路由 → L7 只读账号（DB 兜底）。
- **`DatasourceAdapter`**（L7 定义，L3/L4 依赖）：`dialect` / `execute(safe_sql) -> ResultSet` / `dry_run(sql) -> None` / `get_schema_snippet(table_names) -> list[str]`。
- **`ResultSet`**（L7 为 owner）：columns / rows / row_count / truncated / meta；L3 `Answer.rows` 承载。
- **身份只透传不解析**：L2/L3 原样透传 `CallerIdentity` 给 L5，范围（org_ids/ps_ids）解析唯一归 L5。
- **Layer 4 永远收到独立问题**（多轮改写只在 L3 做）。
- **连接经 L6 :6033（数据面），不碰 :6032（Admin）**；连接池归 L6，L7 不自建池。

## 当前进度

- 六层 spec 的 **design.md + requirements.md 已全部完成**，且每层"正确性属性 ↔ 需求验收标准"双向追溯已连通（design 里带 `**Validates: Requirements X.Y**`，requirements 末尾带追溯附录）。
- **tasks.md 尚未生成**（任一层都还没有）。

## 下一步

按依赖顺序为各层生成实现任务清单（tasks）：**L5 → L7 → L6 → L4 → L3 → L2**。

## 协作约定（重要）

- **文档正文用中文**（团队以中文协作），**代码示例用 Python**。
- **不确定的地方读代码 / 读官方文档，不要编**；claim 要能对应到源码或文档出处。
- 每层作为独立 feature，走 **design-first**（先设计后需求）；需求用 **EARS**（WHEN/WHILE/WHERE/IF/THEN/THE/SHALL）+ Glossary + 正确性属性追溯附录。
- 保持各层边界清晰（"拥有什么 / 不拥有什么"表），复用共享抽象（`VectorStore`/`Embedder`/`DatasourceAdapter`）。
- 许可注意：SQLBot 是 modified-GPLv3（借鉴思路、重新实现，不复制源码）；WrenAI 核心 Apache-2.0；pandas-ai 的只读校验思路可借鉴。

## 开源选型结论（源码级评估过 7 个 text2sql 项目）

- **复用组件思路**：SQLBot（术语库 / 示例召回 / 分阶段 pipeline / StarRocks 适配）。
- **借思路**：pandas-ai（`is_sql_query_safe` 只读校验）、vanna（可插拔抽象设计参考）。
- **候选保留**：WrenAI（确定性 RLAC/CLAC，隔离规则复杂时可重估）。
- **不用**：sqlchat / Chat2DB / jimureport。
