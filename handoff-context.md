# 智能问数系统 v2 · 开发交接上下文

> 本文件用于把开发进度与未决设计带到新机器 / 新会话。它**不是** steering（不会自动进入对话上下文）；在 Kiro 里需要时用 `#handoff-context.md` 手动带入对话即可接续。
>
> steering 规则（`.kiro/steering/`）与各层 spec（`.kiro/specs/`）已随仓库版本控制，clone 后自动生效，无需手动搬运。

## 这是什么

面向 C 端（移动端 / Web / 外部应用）的**自然语言问数系统（Text-to-SQL）**，服务阳光云光伏业务数据（StarRocks 数仓）。核心诉求：**准确、低延迟、强数据隔离、成本可控**。对外以 REST（`POST /v1/query`）暴露。

## 七层架构（主干自上而下）

- **Layer 1 客户端**：iOS / Android / Web / 外部应用，统一 REST。
- **Layer 2 api-gateway**：API Gateway + 薄 Lambda（认证 / 边缘限流 / 单用户业务限流 / Legacy 路由 / 身份透传）。
- **Layer 3 query-engine**（EKS 上 Python 服务）：编排器 + 上下文改写（多轮，AgentCore Memory）+ 术语归一化（硬字典 + 软术语库）+ 语义复用缓存（命中即短路）。
- **Layer 4 text2sql-pipeline**（嵌于 L3，插拔式）：Schema Linking → SQL Generation → Selection → Query Fixer。
- **Layer 5 isolation-guard**（安全关键，自研 sqlglot）：权限解析 → 隔离注入 → 只读校验。**不可绕过**。
- **Layer 6 connection-proxy**：ProxySQL 常驻 EKS（连接池 / 只读路由 / 故障隔离）。基础设施 + 配置契约。
- **Layer 7 datasource-adapter**：可插拔 `DatasourceAdapter`，当前仅 StarRocks 只读实现。

## 当前进度（务必先读）

**本期已实现并完成的层：L5 / L4 / L3 / L2 + knowledge-admin（知识管理后端）。**

- **全量测试 315 passed**（`.\.venv\Scripts\python.exe -m pytest -q`）。
- **真实外部依赖已全部接入并冒烟通过**：Bedrock（Converse）/ OpenSearch Serverless（AOSS）/ DynamoDB / AgentCore Memory。
- **最新提交** `44815c9`（`feat: knowledge-admin 知识管理后端 + 硬通道 Aho-Corasick 匹配器`），已推到 `origin/main`（`https://github.com/szipan/smart-query.git`）。
- knowledge-admin：软通道术语库 CRUD + 向量同步、硬通道实体（DynamoDB 事务写 + 别名全局唯一）、批量导入/导出（Excel+CSV）、鉴权占位、Aho-Corasick 硬通道运行时匹配器、L3 接线，全部完成（14 任务）。

> 注：`.kiro/specs/*/tasks.md` 各层任务清单已生成（早期版本的本文件曾写"tasks 尚未生成"，已过时）。

## 新机器环境重建（按序）

1. 装 Kiro + Git + Python≥3.10；AWS 凭证配到 `~/.aws` 或环境变量（后端资源都在 **us-west-2 同一账号**，无需迁移，有凭证可访问即可）。
2. `git clone` 仓库并用 Kiro 打开该目录。
3. 重建虚拟环境（**不要拷贝 `.venv`**）：
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -e ".[dev,aws,admin]"
   ```
4. 建 `.env`：`Copy-Item .env.example .env`（`.env.example` 已含全部真实配置值）。
5. 如需继续参考 SQLBot：重新 clone 到 `SQLBot/`（被 `.gitignore` 故意排除，GPLv3 只借思路不入库）。
6. 验证：设好环境变量后跑 `pytest -q`，应见 **315 passed**。

### 运行前置环境变量（真实依赖冒烟/脚本）
```powershell
$env:AWS_REGION="us-west-2"
$env:SMARTQUERY_OPENSEARCH_ENDPOINT="https://cqo59tw52rympj7xvdqd.us-west-2.aoss.amazonaws.com"
```

### 不在 git、需手动处理的文件
- `scripts/manual_test_*.py`（manual_test_synonym / manual_test_cache_recall / manual_test_context_rewriter 部分）：本地测试脚本，约定**不提交**，迁移需手动拷贝。
- `cache_recall_result.csv`：实测输出，可重跑生成，不必迁。
- `docs/weekly-report-*.html`：周报，按需手动拷贝。

## 真实 AWS 资源清单（us-west-2）

- **LLM**：`us.anthropic.claude-haiku-4-5-20251001-v1:0`（Converse API；旧版 Claude 3/3.5 在本账号已 Legacy/EOL 不可用）。
- **Embedding**：`amazon.titan-embed-text-v2:0`，维度 1024。
- **OpenSearch Serverless**：endpoint `https://cqo59tw52rympj7xvdqd.us-west-2.aoss.amazonaws.com`，集合 `smart-query`（**VPC-only**，经 `vpce-0a0e0d10ba02c9bda`，SG `sg-0ac149b2678da1aa1` 已放行 `172.31.0.0/16:443`），index `smartquery-vectors`。
- **DynamoDB**：`api_gateway_rate_limit` + 知识表 `knowledge_glossary` / `knowledge_glossary_name` / `knowledge_entity` / `knowledge_alias_index`。
- **AgentCore Memory**：`smart_query_memory-m4IH8j7Ren`。
- 建资源脚本在 `scripts/`（create_aoss_collection / create_dynamodb_table / create_agentcore_memory / create_knowledge_tables）。

### AOSS 已知限制（已在 `src/adapters/opensearch.py` 规避）
VECTORSEARCH 集合不支持自定义 `_id`、不支持 `delete_by_query`。upsert/delete 已改为：index 自动 `_id` + search 找 `_id` + 按 `_id` 删。

## 关键设计决策（务必延续）

- **引擎跑 EKS，不用 AgentCore Runtime 托管**（连接池与每会话 microVM 冲突）。
- **Layer 5 隔离自研 sqlglot**（WrenAI 的 `wren-core-py` 为 Apache-2.0，隔离规则复杂时可重估）。
- **多轮会话用 AgentCore Memory**；**凭证/token 用 AgentCore Identity**。
- **向量栈可替换（当前 OpenSearch），Embedder 当前 Bedrock Titan**；`VectorStore`/`Embedder` 由 L3、L4 共用。
- **语义缓存只存"隔离前 SQL 模板"，命中仍过 Layer 5**（按当前用户重注隔离；严禁缓存带具体 org_id/ps_id 的成品 SQL）。
- **只读双保险**：L5 sqlglot AST 禁非 SELECT（权威）+ DB 只读账号；加 L6 只读路由共三道防线。
- **术语归一化分硬 + 软**：关键实体（电站名/指标名）确定性字典硬归一；一般描述词术语库软归一。
- **身份只透传不解析**：范围（org_ids/ps_ids）解析唯一归 L5。
- 本期**不做**图表可视化、对外 MCP Server、与 data-agent 整合。

## 语义缓存召回率实测结论（2026-07 记录）

- 方法：把原始问题存入 `semantic_cache` 命名空间，对 25 个业务变体问法逐一真实 `lookup`。
- 结果：基线（原问题查自己）sim=1.0 命中；**25 个变体 0 命中**（阈值 0.95），相似度 0.69~0.94，最高为 5 号 0.9372。
- **关键发现（读代码确认）**：当前编排里缓存 lookup 嵌入的是 `standalone_question`（上下文改写后的自然语言原句），**归一化产出（resolved_entities/terms）只流向 L4，不参与缓存命中判定**。因此"缓存命中率"当前只反映嵌入模型的语义相似度 + 阈值，与归一化模块无关。
- 0.95 阈值刻意从严（拒误命中）→ 语义等价但措辞改写的变体本就应 miss；**miss ≠ 出错**（走正常 L4 生成，结果通常仍正确），只是没吃到缓存降本。

## 未决设计：新增"规范化改写"节点（已定方向，3 点待确认）

**目标**：把变体问法折叠成统一标准叙述后再进缓存，以真正提升命中率（用户已确认此方向有效且要做）。

**已定方向**：
- 流水线插入新节点：`context_rewrite → term_normalization →【canonical_rewrite(新)】→ semantic_cache_lookup`。
- `QueryContext` 新增字段 `canonical_question`；**缓存 store+lookup 都改用它**；**传给 L4 的问题也替换为它**（用户确认第 3 点）。
- 算法三段：A 确定性同义词→标准词精确替换；B LLM 结构规范化（temperature=0、提示词严格约束、超长按 `REWRITE_MAX_LEN` 回退）；C 再跑一次确定性替换兜底锁定标准词。任一步失败回退上一步，最差回退 `standalone_question`。
- 稳定性说明：缓存匹配是**向量相似度 ≥ 阈值**（非字符串精确相等），故 LLM 措辞轻微抖动可容忍——只要规范化把变体朝标准叙述收拢即可。

**待用户确认的 3 点（未确认前不要动手）**：
1. 确定性替换的数据来源/机制：推荐"读**软通道术语库**登记的 `synonyms→term`，用已实现的 **Aho-Corasick 精确匹配**做替换"（软通道向量检索仅用于召回定义喂 L4，不宜承担确定性替换）。
2. "户用/家用/residential" 这类**字段枚举值同义词**按**软通道**建模（标准词=户用，同义词=[家用, residential, 家庭户用...]），确认是否如此。
3. 流程：**spec 先行**（更新 query-engine 的 requirements/design/tasks）再实现 + 补齐单测/集成/属性测试；确认是否 spec 先行。

## 协作约定（重要）

- **文档正文用中文，代码用 Python**。
- **不确定即暂停确认、不臆断**（见 `.kiro/steering/00-highest-priority.md`）：只要"猜错要返工或出错"就必须先问。
- **每层每模块必须单测 + 层间集成测试 + 正确性属性测试（hypothesis）**（见 `.kiro/steering/20-testing.md`）。
- **改文件一律用编辑工具（str_replace/fsWrite/fsAppend），禁用 PowerShell `Set-Content` 改中文文件**（防乱码，UTF-8；见 `.kiro/steering/30-file-encoding.md`）。
- **SQLBot 仅设计参考**（modified-GPLv3）：借思路、clean-room 重写，**不复制源码**（见 `.kiro/steering/10-reference-sqlbot.md`）。

## 开源选型结论（源码级评估过 7 个 text2sql 项目）

- **复用组件思路**：SQLBot（术语库 / 示例召回 / 分阶段 pipeline / StarRocks 适配）。
- **借思路**：pandas-ai（只读校验）、vanna（可插拔抽象）。
- **候选保留**：WrenAI（Apache-2.0，隔离规则复杂时可重估）。
- **不用**：sqlchat / Chat2DB / jimureport。
