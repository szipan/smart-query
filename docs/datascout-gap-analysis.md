# 智能问数系统 — 现状代码与目标架构差距分析

> **文档目的**：对比当前代码库（`data-agent-demo`，基于 AWS Bedrock AgentCore 的通用多智能体平台）与智能问数目标架构设计稿（`architecture-guide.md` / `data-scout-architecture.drawio`），识别已满足、部分满足、未满足的能力，给架构师作为演进决策参考。
>
> **读者**：架构师 / 技术负责人
> **结论先行**：当前代码与目标架构是**两种不同性质的系统**，核心查询引擎（Layer 3/4）与安全隔离层（Layer 5）几乎完全缺失，需作为重点投入方向。
>
> **层级命名**：本文档与架构图保持一致，主干自上而下为 **Layer 1 ~ Layer 7**；`legacy-router`（Legacy 路由）是从 **Layer 2** 旁路出去的分支，不在主干编号内。

---

## 1. 结论速览（TL;DR）

| 维度 | 当前代码库 | 智能问数目标 |
|------|-----------|---------------|
| 系统性质 | **通用 AgentCore 多智能体平台**，Text-to-SQL（自然语言转 SQL，Structured Query Language 结构化查询语言）只是挂在 LLM（Large Language Model，大语言模型）上的一个 `data-analysis` 技能（提示词驱动） | **专用、生产级 Text-to-SQL 系统**，强调确定性流程工程化 |
| 数据源 | 单一 SQLite demo 库（`northwind.db`） | **当前仅接 StarRocks(51.3TB)**；适配层设计为可插拔，预留 MySQL / Spark 等其他源 |
| 数据隔离 | **无**（仅提示词约束只读） | 硬编码、不可跳过的行级隔离（org_id / ps_id 注入） |
| 查询引擎 | LLM + SKILL.md 自由生成 | 术语归一化 + 语义缓存 + 插拔式三阶段 Pipeline |
| 整体匹配度 | **偏低**，安全与核心引擎为最大缺口 | — |

**匹配度热力图（按目标架构层级）：**

| 层级 | 匹配度 | 一句话判断 |
|-----------|:------:|-----------|
| Layer 1 客户端 | 🟡 部分 | 有 Web；移动端/外部应用 REST（Representational State Transfer，表述性状态转移）入口待补。AI Agent（MCP，Model Context Protocol 模型上下文协议）暂无需求，不在范围 |
| Layer 2 api-gateway（API（Application Programming Interface，应用程序接口）网关） | 🟡 部分 | 认证有；公共 REST 契约/限流/Legacy 路由缺（入口建议 **Amazon API Gateway**） |
| legacy-router（Legacy 路由，Layer 2 旁路分支） | 🔴 缺失 | 无 |
| Layer 3 query-engine（查询引擎） | 🔴 缺失 | 术语归一化/语义缓存全无 |
| Layer 4 Text-to-SQL Pipeline（插拔式） | 🔴 缺失 | Schema Linking/Generation/Selection/Fixer 全无 |
| Layer 5 isolation-guard（隔离 & SQL 校验） | 🔴 缺失 | **安全红线**，数据隔离不存在 |
| Layer 6 连接代理层 | 🔴 缺失 | 无 ProxySQL（StarRocks 连接池） |
| Layer 7 datasource-adapter（数据源适配） | 🔴 缺失 | 仅 SQLite；目标先接 StarRocks，适配层需可扩展 |

> 图例：🟢 满足 / 🟡 部分满足 / 🔴 未满足 / ⚪ 暂不在范围

---

## 2. 系统定位对比

- **当前代码（data-agent-demo）**：企业级 *Agent 管理平台*。可配置 Agent、模型、工具、技能；多智能体（Supervisor + agents-as-tools）；跑在 AgentCore Runtime 上；通过 MCP/Gateway 接外部工具。Text-to-SQL 仅是其中一个能力演示。
- **目标（智能问数）**：面向 C 端客户的 *自然语言问数系统*。核心诉求是**准确、低延迟、强隔离、可控成本**的 Text-to-SQL，服务于阳光云光伏业务数据。

两者重叠区只有"LLM 生成 SQL"这一点，工程化程度差异巨大。

---

## 3. 逐层差距分析

### Layer 1 — 客户端

| 目标入口 | 状态 | 说明 / 证据 |
|---------|:----:|------------|
| Web 端 | 🟢 | `source/portal`（Next.js） |
| iOS / Android | 🔴 | 无原生端；应走 **REST API**，目前仅有 IM 接入（`source/chat-gateway`，飞书/Slack） |
| 外部应用 REST API | 🟡 | 有 `backend-api` + `/invocations`，非目标的 `POST /v1/query` 契约 |
| AI Agent (MCP Client) | ⚪ 暂不在范围 | **当前无 AI Agent 调用智能问数的需求**，对外 MCP Server 暂不实现。（注：现有代码的 MCP 是"连出去用工具"的 **Client** 方向，与此入口无关） |

> **接入方式约定**：iOS / Android / Web / 外部应用等确定性程序一律走 **REST API**（`POST /v1/query`），这是必做项。MCP Server 仅用于"其他自主 AI Agent 把智能问数当工具调用"的场景——当前无此需求，**不在范围**；未来如需，可由 AgentCore Gateway 原生暴露，作为 REST 之外的附加门面。

### Layer 2 — api-gateway（API 网关）

| 目标能力 | 状态 | 说明 / 证据（推荐实现） |
|---------|:----:|------------|
| 入口 / 公共 REST `/v1/query` 契约 | 🔴 | 目标需对外稳定 REST 契约；**推荐用 Amazon API Gateway 暴露**。当前无独立公共契约层 |
| 计算载体（跑代码的运行环境） | 🟢 | **Lambda（薄计算/路由）+ AgentCore Runtime（跑 Agent）**，托管无服务器、免自管集群；如确需常驻容器，复用用户**现有 EKS**。（入口层另由 **Amazon API Gateway** 承担，见上一行） |
| Token 验证（认证） | 🟢 | `shared/auth.py` Cognito JWT（JSON Web Token）校验；推荐在 **Amazon API Gateway** 或 **AgentCore Inbound JWT Authorizer** 终结（二选一） |
| 限流 | 🔴 | 无真实限流器；**推荐 Amazon API Gateway 做边缘限流 + 薄 Lambda/DynamoDB 做单用户业务限流** |
| 请求路由 (Legacy/Query) | 🔴 | 无内容路由分流逻辑；**应置于 Amazon API Gateway 之后的薄 Lambda 层**（API Gateway 与 AgentCore 均不做内容路由） |

### legacy-router — Legacy 路由（Layer 2 旁路分支）
🔴 完全缺失。源码 grep `legacy` 无命中。

> **职责区分**：路由"判断"在 Layer 2（薄 Lambda 看问题内容决定走 Legacy 还是 Text-to-SQL）；`legacy-router` 是被路由到之后的**执行/适配层**——匹配具体已注册规则、调用外部 Legacy 服务、整形返回。独立成层是为了把"重且独立演进的 Legacy 集成"从薄网关里剥离。

### Layer 3 — query-engine（查询引擎，核心）

| 目标能力 | 状态 | 说明 / 证据 |
|---------|:----:|------------|
| 查询编排器 | 🔴 | 无统一编排，靠 Agent 自由发挥 |
| 术语归一化（N-gram + 字典 + Embed 兜底） | 🔴 | 无字典、无 NER（Named Entity Recognition，命名实体识别）、无 Cohere Embed |
| 语义复用缓存（向量检索 ≥0.95） | 🔴 | 无 OpenSearch、无 embedding 缓存 |

### Layer 4 — Text-to-SQL Pipeline（插拔式，嵌于 Layer 3 内）

| 目标能力 | 状态 | 说明 / 证据 |
|---------|:----:|------------|
| Schema Linking | 🔴 | 有 `KnowledgeBaseTool`（通用 RAG，Retrieval-Augmented Generation 检索增强生成）但未接成 schema linking；当前用静态 `data-schema.md` 全量塞模型 |
| SQL Generation | 🟡 | 单次 LLM + `SKILL.md` 提示词自由生成，非可配置生成器 |
| SQL Selection | 🔴 | 无多候选、无 self-consistency |
| Query Fixer（失败修复） | 🟡 | 仅靠模型自觉（SKILL.md 写"出错就改"），无结构化重试组件 |
| 插拔式 Pipeline | 🔴 | 不存在 pipeline 抽象与接口 |

### Layer 5 — isolation-guard（隔离注入 & SQL 安全校验，安全红线）

| 目标能力 | 状态 | 说明 / 证据 |
|---------|:----:|------------|
| 权限解析（用户→组织→电站） | 🔴 | 当前权限是 App/Agent/Skill 三级**组 ACL（Access Control List，访问控制列表）**（`shared/permissions.py`），控制"能否用某 Agent"，非数据行级 |
| 隔离条件注入（WHERE org_id/ps_id IN） | 🔴 | `execute_sql` Lambda 仅收 `sql` 字符串，**拿不到用户身份**；grep `org_id`/`ps_id` 源码零命中 |
| SQL 安全校验（禁止非 SELECT） | 🔴 | 代码层无校验；`gateway-lambda-target/index.py` 甚至有 `conn.commit()` 分支允许写 |

> **关键风险**：目标系统卖点"隔离硬编码、不可跳过"，当前**唯一约束是 `data-analysis/SKILL.md` 的提示词**，靠模型自觉。一旦用户能用该 Agent，即可查全库。

### Layer 6 — 连接代理层
🔴 完全缺失。无 ProxySQL（StarRocks 连接池）、无连接池管理；Lambda 直连本地 SQLite 文件。

### Layer 7 — datasource-adapter（数据源适配）

| 目标 | 状态 | 说明 |
|------|:----:|------|
| StarRocks 接入（当前唯一目标数据源） | 🔴 | 仅单一 SQLite（`northwind.db`，打包进 Lambda） |
| 适配层可扩展（预留 MySQL / Spark 等其他源） | 🔴 | 无数据源抽象层，`execute_sql` 写死单个 SQLite 连接，加新源需改代码 |
| 只读凭证 via Secrets Manager | 🔴 | 凭证即本地 sqlite 文件，无只读账号、无 Secrets Manager |

---

## 4. 安全四层防护对比

| 层 | 目标 | 当前 | 结论 |
|----|------|------|:----:|
| 认证层 | 每请求 OAuth（Open Authorization，开放授权）校验 | Cognito JWT 校验 | 🟢 概念满足（IdP，即 Identity Provider 身份提供方，不同） |
| 授权层 | 用户→组织→电站 + SQL 条件注入 | 仅组级资源 ACL，无数据边界注入 | 🔴 |
| SQL 层 | 禁止非 SELECT | 代码无校验，仅提示词 | 🔴 |
| 数据库层 | 只读凭证 | SQLite 文件可写 | 🔴 |

---

## 5. 设计亮点对比

| 目标亮点 | 状态 | 说明 |
|---------|:----:|------|
| 插拔式 Pipeline | 🔴 | 无 |
| 术语归一化前置 | 🔴 | 无 |
| 隔离不可跳过 | 🔴 | 隔离本身不存在 |
| 成本分级（Haiku/Sonnet/Opus） | 🟡 | 模型可按 Agent 配置，但无按问题复杂度自动路由 |
| 连接代理层 | 🔴 | 无 |

---

## 6. AWS 托管服务对比

| 服务 | 目标用途 | 当前 |
|------|---------|------|
| Amazon Bedrock | Claude + Cohere Embed | 🟢 Claude 用了；🔴 Cohere Embed 未用 |
| OpenSearch Serverless | 语义缓存向量库 | 🔴 未用 |
| Bedrock Knowledge Bases | Schema RAG | 🟡 有通用 KB 工具，未用于 schema linking |
| 计算载体 | 应用部署 | 🟢 AgentCore Runtime + Lambda（托管无服务器）；常驻容器需求**复用现有 EKS** |
| DynamoDB | 缓存元数据 + 术语字典 | 🟡 用了，但仅存聊天历史/Agent 配置 |
| Secrets Manager | DB（Database，数据库）凭证 / OAuth 密钥 | 🟡 AgentCore Identity 管 API key，DB 凭证未走 |

---

## 7. 可复用资产（非从零开始）

当前代码中可作为目标架构底座的部分：

- **认证骨架** — `shared/auth.py` JWT 校验，换阳光云 OAuth 即可复用。
- **组级权限框架** — `shared/permissions.py` 可作为"用户→组织"映射起点（需扩展到数据行级）。
- **Bedrock 模型层** — `custom_bedrock_model.py`（prompt caching、guardrail、多模型）可直接用于 SQL Generation。
- **流式协议 / 前端** — `portal` + SSE（Server-Sent Events，服务器推送事件）协议（`shared/streaming`）成熟，可作 Web 入口。
- **MCP / Gateway 工具机制** — 现为"连出去用工具"的 Client 方向；如未来出现 AI Agent 调用需求，可由 AgentCore Gateway 原生暴露成对外 MCP Server（当前不在范围）。

---

## 8. 演进路线与优先级建议

按"价值 × 缺口"排序：

### P0 — Layer 5 隔离层 isolation-guard（安全红线，必须先做）
- 让 `execute_sql` 拿到调用者身份 → 解析 org_id / ps_id → **强制注入 WHERE 边界条件**。
- 加 SQL AST（Abstract Syntax Tree，抽象语法树）解析校验（仅允许 SELECT），并改用**只读 DB 账号**（凭证走 Secrets Manager）作为第二道防线。
- 原则：隔离逻辑硬编码在网关/适配层，**不依赖模型行为**。

### P1 — Layer 3/4 查询引擎 + Text-to-SQL Pipeline（核心价值）
- 术语归一化（字典存 DynamoDB + Cohere Embed 兜底）。
- 语义复用缓存（OpenSearch Serverless 向量检索）。
- 插拔式三阶段 Pipeline（Schema Linking / Generation / Selection + Query Fixer），统一接口、配置切换算法。

### P2 — Layer 7 数据源（先接 StarRocks）+ Layer 6 连接代理层
- **当前只接 StarRocks**：SQLite → StarRocks 适配 + ProxySQL 连接池；只读凭证统一 Secrets Manager 管理。
- **适配层做成可插拔抽象**（统一数据源接口 + 方言/连接驱动按源切换），让未来新增 MySQL / Spark 等数据源时低成本接入——现在不实现多源，但不写死单源。
- ProxySQL 这类**需要常驻进程 / 连接池**的组件，部署到用户**现有 EKS**。

### P3 — Layer 2 网关层 api-gateway
- **入口用 Amazon API Gateway** 暴露公共 REST `/v1/query` 契约，承担边缘限流、WAF（Web Application Firewall，Web 应用防火墙）、TLS（Transport Layer Security，传输层安全）。
- 其后接**薄 Lambda**（Legacy/Query 内容路由 + 单用户业务限流 + 调 InvokeAgentRuntime）→ **AgentCore Runtime**（跑 Agent）。
- 完整组合：**Amazon API Gateway + 薄 Lambda + AgentCore Runtime**（详见第 9 节）。

---

## 9. Layer 2 实现建议：Amazon API Gateway + AgentCore

> 结论：**AgentCore 能替代 Layer 2 的"安全接入"内核，但替代不了"业务网关"外壳**，建议两者组合。

**推荐拓扑：**

```
客户端 → Amazon API Gateway → 薄计算层(Lambda) → AgentCore Runtime → AgentCore Gateway → 数据源
```

> **计算载体策略**：Layer 2 全链路走**托管无服务器**（API Gateway + Lambda + AgentCore Runtime）。仅当出现"高并发长流式"或"常驻连接池"等必须常驻容器的场景时，才保留常驻服务，且**复用用户现有 EKS**。

**薄计算层 Lambda 职责（编排胶水，不含业务智能）：**

夹在 API Gateway 与 AgentCore Runtime 之间，按顺序做：

1. **接住请求** — 承接 API Gateway 转发进来的请求（此时已过认证、边缘限流），取出身份与问题。
2. **单用户业务限流** — 查/写 DynamoDB 计数器（如"用户 A 今天问了几次"），超限则拒绝。
3. **Legacy/Query 内容路由** — 看问题内容决定走 `legacy-router`（Legacy 服务），还是走 Text-to-SQL。
4. **请求整形** — 把对外的 `/v1/query` 契约转成 `InvokeAgentRuntime` 所需入参格式，并透传用户身份（供 Layer 5 数据隔离使用）。
5. **调用与回包** — 调用 AgentCore Runtime（或 Legacy 服务），拿到结果再整形成 REST 响应返回。

> 注：这层**不生成 SQL、不查库、不调大模型**——只做"判断 + 转发 + 计数"的调度工作（即"薄"）。Text-to-SQL 在 AgentCore Runtime（Layer 3/4），数据隔离在 Layer 5。

**职责划分：**

| Layer 2 职责 | 由谁实现 |
|------------|---------|
| 公共 REST `/v1/query` 契约、WAF、边缘安全 | Amazon API Gateway |
| 单用户业务限流 | API Gateway usage plan，或薄层 + DynamoDB 计数 |
| Token 认证 | API Gateway 或 AgentCore Inbound JWT Authorizer（二选一，勿重复） |
| Legacy/Query 内容路由 | 薄计算层 或 Agent 内部（AgentCore 不做内容路由） |
| (可选) MCP Server 接入 | AgentCore Gateway 原生支持；**当前无 AI Agent 调用需求，暂不实现** |
| 托管运行 / 扩缩容 / 会话 / 服务级限流 | AgentCore Runtime |

**AgentCore 能力佐证（官方文档）：**
- 入站认证：[Inbound JWT Authorizer](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/inbound-jwt-authorizer.html) — IdP 无关，校验 `aud`/`client_id`/`scopes`/自定义 claim。
- MCP 入口与路由：[Gateway Core Concepts](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-core-concepts.html) — MCP/HTTP/Inference 三类 target（协议/目标层路由，非内容路由）。
- 服务级限流：[Quotas](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/bedrock-agentcore-limits.html) — `InvokeAgentRuntime` 默认 200 TPS（Transactions Per Second，每秒事务数）/agent/account（可调），属账户级防护，非业务限流。

---

## 10. 关键风险与注意事项

1. **数据隔离缺失（最高优先级）**：当前任何能调数据查询 Agent 的用户都可查全库，无行级边界。生产前必须补 Layer 5。
2. **SSE 流式 vs API Gateway**：当前 `/invocations` 返回 `text/event-stream`。Amazon API Gateway 不支持真正流式且有 29s 集成超时。若保留逐字流式，需绕开 API Gateway（用 Lambda Function URL RESPONSE_STREAM / 直连 AgentCore Runtime 流式接口 / 或复用**现有 EKS** 上的常驻服务）；若接受非流式 JSON 一次性返回（目标延迟 ≤10s 可接受），API Gateway 可用。**任何"需要常驻"的取舍都优先落到现有 EKS。**
3. **认证勿做两层**：API Gateway 终结认证（→ SigV4（Signature Version 4，签名版本 4）调 AgentCore）或透传 JWT（→ AgentCore 验），二选一。`auth.py` 已同时支持两条路径。
4. **单用户限流不能开箱即用**：API Gateway usage plan 绑 API Key，对终端用户不友好；通常需在 Authorizer/薄层用 DynamoDB 做每用户计数。
5. **只读强制不能只靠提示词**：必须代码层 SQL 校验 + DB 只读账号双保险。

---

## 11. 证据索引（关键文件）

| 主题 | 文件路径 |
|------|---------|
| SQL 执行（无身份/无校验/可写） | `source/infrastructure/lambda/gateway-lambda-target/index.py` |
| Text-to-SQL 行为（提示词驱动） | `source/agentic-app/skills/data-analysis/SKILL.md` |
| 认证（JWT/IAM/Skip 三路径） | `source/agentic-app/auth.py`、`source/shared/src/shared/auth.py` |
| 组级权限 ACL | `source/shared/src/shared/permissions.py` |
| Agent 编排 / 模型 / 工具加载 | `source/agentic-app/agent_loader.py` |
| Bedrock 模型封装（缓存/guardrail） | `source/agentic-app/custom_bedrock_model.py` |
| 入口与配置 | `source/agentic-app/main.py`、`source/agentic-app/config.py` |
| 前端 / 限流文案 | `source/portal/`（`lib/errors.ts`） |

---

*本文档基于对仓库源码的静态走读与 AWS 官方文档核对整理，作为架构演进参考；落地前请结合实际部署环境复核。*
