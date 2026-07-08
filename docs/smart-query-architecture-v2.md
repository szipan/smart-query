# 智能问数系统 架构说明文档（v2 · 研发版）

> **配套架构图**：`../smart-query-architecture-v2.drawio`
> **前序参考**：`./datascout-gap-analysis.md`（现状代码与目标架构差距分析）
> **读者**：研发团队（后端 / 数据 / 平台）
> **文档目的**：说明 v2 落地架构的分层设计、关键决策与理由、各层实现与复用来源、数据流、以及研发需要遵守的安全红线与接口规范。

---

## 1. 系统定位与范围

**定位**：面向 C 端（移动端 / Web / 外部应用）的**自然语言问数系统**。核心诉求：**准确、低延迟、强数据隔离、成本可控**的 Text-to-SQL，服务阳光云光伏业务数据（StarRocks 数仓）。

**本期范围（In Scope）**
- REST API 对外（`POST /v1/query`），供 iOS / Android / Web / 外部应用调用。
- 完整问数链路：上下文改写 → 术语归一化 → 语义缓存 → Text-to-SQL → **数据隔离** → 只读执行。
- **多轮会话**（借助 AgentCore Memory）。
- 单一数据源 **StarRocks**（适配层做成可插拔,预留其他源）。

**明确不在本期范围（Out of Scope）**
- 图表 / 可视化渲染（后续再加，届时考虑 AgentCore Code Interpreter）。
- 对外 MCP Server（暂无 AI Agent 调用方需求）。
- 与其他既有系统整合（智能问数是**独立系统**，独立建设、独立部署）。

---

## 2. 总体架构

主干自上而下为 **Layer 1 ~ Layer 7**；`legacy-router` 是从 Layer 2 旁路出去的分支。

```
Layer 1 客户端 (iOS/Android/Web, 统一 REST)
        │
Layer 2 api-gateway (Amazon API Gateway + 薄 Lambda)  ──旁路──► legacy-router ──► Legacy 外部服务
        │  查询请求
Layer 3 query-engine (EKS 上的 Python 服务)
   ├─ 上下文改写/指代消解 (★多轮, AgentCore Memory)
   ├─ 术语归一化 (硬:实体字典 / 软:术语库)
   ├─ 语义复用缓存 (VectorStore=OpenSearch, 命中→隔离前SQL)
   └─ Layer 4 Text-to-SQL Pipeline (插拔式: Schema Linking→Generation→Selection→Fixer)
        │  未校验 SQL
Layer 5 isolation-guard (★自研 sqlglot: 权限解析→隔离注入→只读校验)
        │  校验通过 SQL
Layer 6 连接代理层 (ProxySQL @ EKS)
        │
Layer 7 datasource-adapter (StarRocks, 只读, 可插拔)
```

**运行载体**：query-engine 与 ProxySQL 跑在**现有 EKS**;网关入口用 **Amazon API Gateway + 薄 Lambda**;**AgentCore（Memory / Identity / 未来 Code Interpreter）作为“能力组件”经 SDK 调用**，不作为托管运行时。

---

## 3. 关键设计决策（决策 + 理由）

| # | 决策 | 理由 / 出处 |
|---|------|------------|
| D1 | **引擎跑在 EKS，不用 AgentCore Runtime 托管** | 引擎有连接池(ProxySQL)与共享热状态;Runtime 的“每会话 microVM”与连接池冲突,且高并发下成本更高。Runtime 的会话隔离优点在高并发问数场景不划算。 |
| D2 | **Layer 5 隔离自研 sqlglot**，不采用 WrenAI | 需求确定性强制注入;自研透明、纯 Python、与栈一致。WrenAI（核心 Apache-2.0，可确定性 RLAC）为候选但需全盘采用 MDL 建模范式，本期不引入。 |
| D3 | **多轮会话用 AgentCore Memory** | 免自建会话记忆基建;支持短期上下文 + 长期(user-preference)记忆。经 SDK 从 EKS 调用。 |
| D4 | **凭证/密钥用 AgentCore Identity** | token vault 托管阳光云 OAuth / StarRocks 只读凭证;支持“代表用户”换取下游 token。 |
| D5 | **向量栈做成可替换，当前用 OpenSearch** | 定义 `VectorStore` / `Embedder` 接口,当前实现 OpenSearch + Bedrock/Cohere Embed。 |
| D6 | **数据源适配层可插拔，当前仅 StarRocks** | `DatasourceAdapter` 接口;当前只接 StarRocks，预留 MySQL/Spark。 |
| D7 | **语义缓存存“隔离前 SQL”，命中仍走 Layer 5** | 缓存的是问题→SQL 模板;命中后按当前用户重新注入隔离,严禁缓存带具体 `org_id/ps_id` 的成品 SQL（否则越权泄露）。 |
| D8 | **只读强制在代码层做，不只靠提示词** | Layer 5 用 sqlglot AST 校验禁非 SELECT + 数据库只读账号双保险。 |
| D9 | **术语归一化分“硬 + 软”** | 关键实体(电站名/指标名)用确定性字典硬归一;一般描述词用术语库软归一(LLM 中介)。 |

---

## 4. 逐层设计

### Layer 1 — 客户端
- iOS / Android / Web / 外部应用，**统一走 REST**（`POST /v1/query`）。
- 不区分端;差异化交给客户端自身。

### Layer 2 — api-gateway
- **Amazon API Gateway**：对外公共 REST 契约、TLS、WAF、边缘限流。
- **薄 Lambda（编排胶水，不含业务智能）**，按序做：
  1. 接住请求（已过认证、边缘限流），取出身份与问题；
  2. 单用户业务限流（DynamoDB 计数）；
  3. Legacy / Query 内容路由；
  4. 请求整形 + 透传用户身份（供 Layer 5）；
  5. 调用 query-engine（或 legacy-router）并整形回包。
- **认证**：阳光云 OAuth 2.0；在 API Gateway 或应用层校验（二选一，勿双层）。
- ⚠️ **流式**：若保留 SSE 逐字输出，注意 API Gateway 不支持真流式且有超时；流式链路需绕开 API Gateway（Lambda Function URL / 直连 EKS 服务）。本期若一次性返回则无此问题。

### legacy-router（Layer 2 旁路）
- 路由“判断”在 Layer 2（薄 Lambda 按问题内容分流）；`legacy-router` 是被路由到之后的**执行/适配层**：匹配已注册规则 → 调用外部 Legacy 服务 → 整形返回。
- 独立成层，便于 Legacy 集成独立演进。

### Layer 3 — query-engine（核心引擎，EKS 上的 Python 服务）
包含以下节点（编排器串联）：

1. **上下文改写 / 指代消解（★多轮，新增）**
   - 从 **AgentCore Memory** 取最近若干轮对话，把“那上个月呢”这类追问**补全为独立问题**，再往下走。
   - 每轮问答结束写回 Memory。
2. **术语归一化**
   - **硬（确定性）**：关键业务实体（电站名 / 指标名）走字典精确匹配 → 映射到标准词 / id。字典可存 DynamoDB。
   - **软（LLM 中介）**：一般术语借鉴 SQLBot 术语库机制（同义词 + 定义，向量检索后注入 prompt，由 LLM 对齐）。
3. **语义复用缓存**
   - 用户问题向量化 → `VectorStore`（当前 OpenSearch）检索相似历史问题（阈值如 0.95）。
   - **命中**：取出“隔离前 SQL”，**跳过 Layer 4，但仍进 Layer 5**（按当前用户重注隔离）。
   - **未命中**：进 Layer 4 生成。
   - 注：SQLBot 现成的是 few-shot 检索（辅助生成，不短路），本项目**需自建“命中即复用”的短路逻辑**。

### Layer 4 — Text-to-SQL Pipeline（插拔式，嵌于 Layer 3）
- **① Schema Linking**：默认 RAG 检索（Bedrock Knowledge Bases）；可选 M-Schema / NL Semantic Layer。
- **② SQL Generation**：默认单 LLM（Bedrock Claude Sonnet）；可选多生成器 / Gated 路由。
- **③ SQL Selection**：默认直通；可选 Self-Consistency / Selection Agent。
- **Query Fixer**：执行失败 → 结构化修复（有限次重试）。
- 各阶段以统一接口实现，可按配置切换算法（借鉴 SQLBot 的分阶段模板）。

### Layer 5 — isolation-guard（安全关键 · 自研 sqlglot · 不可绕过）
三步，**全部在代码层确定性完成，绝不依赖 LLM**：

1. **① 权限解析**：由用户身份解析出数据范围 `scope = {org_ids, ps_ids}`。身份 / 权限来自阳光云 OAuth；凭证可经 **AgentCore Identity** 托管。
2. **② 隔离注入（sqlglot）**：把受控物理表替换成“带过滤的子查询”，贴着表强制注入 `WHERE org_id/ps_id IN (...)`。
3. **③ 只读校验（sqlglot）**：解析 AST，禁止任何非 SELECT 节点（思路借 pandas-ai `is_sql_query_safe`）。

**参考实现（研发按此落地 + 补边界测试）**：

```python
import sqlglot
from sqlglot import exp

FORBIDDEN = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create,
             exp.Alter, exp.TruncateTable, exp.Command)  # Command 覆盖 SET/USE/CALL 等

def assert_read_only(sql: str, dialect: str = "starrocks") -> exp.Expression:
    stmts = sqlglot.parse(sql, dialect=dialect)          # 多语句 → 拒绝
    if len(stmts) != 1 or stmts[0] is None:
        raise ValueError("只允许单条语句")
    root = stmts[0]
    if not isinstance(root, (exp.Select, exp.Union)):    # 顶层必须是查询
        raise ValueError(f"只允许 SELECT，拒绝 {type(root).__name__}")
    if list(root.find_all(*FORBIDDEN)):                  # 树里不得有写操作
        raise ValueError("检测到非 SELECT 操作")
    return root

# 受控物理表 → 隔离列
TABLE_RULES = {"gen": "ps_id", "power_station": "ps_id", "orders": "org_id"}

def inject_isolation(sql: str, scope: dict, dialect="starrocks") -> str:
    root = assert_read_only(sql, dialect)
    def wrap(node):
        if isinstance(node, exp.Table):
            col = TABLE_RULES.get(node.name)
            if not col:
                return node                              # 不受控表，原样保留
            ids = scope["ps_ids"] if col == "ps_id" else scope["org_ids"]
            vals = ", ".join(str(int(x)) for x in ids) or "NULL"   # 来自认证，转 int 防注入
            alias = node.alias_or_name                   # 保留原别名
            return (exp.select("*").from_(node.name)
                       .where(f"{col} IN ({vals})", dialect=dialect)
                       .subquery(alias=alias))
        return node
    return root.transform(wrap).sql(dialect=dialect)
```

**研发铁律（务必遵守）**
- 隔离逻辑硬编码在此层，**任何绕过都视为安全事故**。
- **缓存命中路径同样必须跑 `inject_isolation`**（用当前用户 scope 重注）。
- 字符串类 id 必须正确转义 / 参数化，不能直接拼。
- 必须配一套**边界测试**：JOIN、子查询、CTE、UNION、多受控表、别名冲突、嵌套。
- 生产库连接使用**只读账号**作为第二道防线（即使代码有漏，DB 也拒写）。

### Layer 6 — 连接代理层
- **ProxySQL**（StarRocks 走 MySQL 协议，可用）做连接池，部署于**现有 EKS**。
- 职责：连接复用、限流、故障隔离。**此层无法用 AgentCore 替代**（AgentCore 无连接池能力，且 microVM/会话模型与共享池冲突）。

### Layer 7 — datasource-adapter（可插拔，只读）
- 当前唯一目标源 **StarRocks（51.3TB 数仓）**，只读连接。
- 通过 `DatasourceAdapter` 接口接入；**预留 MySQL / Spark 等**，新增源只加实现类，不改上层。
- 凭证走 Secrets Manager 或 AgentCore Identity；账号为**只读**。

---

## 5. 数据流

**① 标准查询（缓存未命中）**
```
客户端 → API Gateway(认证/限流) → 薄Lambda(路由/整形) → query-engine
   → [Memory 取上下文 + 问题改写] → 术语归一化 → 语义缓存(未命中)
   → Layer4(Schema Linking→Generation→Selection→Fixer) → 未校验SQL
   → Layer5(权限解析→sqlglot注入→只读校验) → 校验通过SQL
   → ProxySQL → StarRocks → 结果 → 回包 → 写回 Memory
```

**② 缓存命中（跳过 Layer 4，但不跳 Layer 5）**
```
… → 语义缓存(命中, 取隔离前SQL) → Layer5(按当前用户重注隔离+只读校验)
   → ProxySQL → StarRocks → 结果
```

**③ 多轮追问**
```
本轮问题 + Memory最近N轮 → 上下文改写为独立问题 → 走 ①/② → 结束写回 Memory
```

**④ Legacy 命中**
```
薄Lambda(内容路由命中Legacy) → legacy-router(规则匹配) → Legacy外部服务 → 回包
```

---

## 6. 可替换抽象接口规范

研发按接口编程，实现可切换：

| 接口 | 职责 | 当前实现 | 备注 |
|------|------|---------|------|
| `VectorStore` | 向量写入 / 相似检索 | OpenSearch Serverless | 语义缓存 + 软术语检索共用 |
| `Embedder` | 文本 → 向量 | Bedrock / Cohere Embed | 可换其他 embedding |
| `DatasourceAdapter` | 连接 / 执行 / 方言 | StarRocks | 预留 MySQL/Spark |
| `TextToSqlStage` | Pipeline 各阶段 | RAG / 单 LLM / 直通 | 借鉴 SQLBot 分阶段 |

> 若复用 SQLBot 组件（术语库 / 示例召回 / schema embedding），其原生 pgvector 调用需改写到 `VectorStore` 接口后面（当前用 OpenSearch 实现），不要直接照搬 pgvector。

---

## 7. AgentCore 能力使用规范（已核对官方文档）

| 能力 | 用途 | 关键说明（有出处） |
|------|------|-------------------|
| **Memory** | 多轮会话（短期）+ 用户偏好（长期） | 支持 short-term 与 long-term(semantic/summarization/user-pref/episodic) + per-user actor ID 隔离；可从 EKS 经 SDK 调用。 |
| **Identity** | 凭证 vault + 代表用户换 token | vault 支持 OAuth2/API key/证书/SAML；workload access token 同时携带 agent 与终端用户身份。**非 Runtime 宿主（EKS）需用 SDK 自取 workload token，并显式传入终端用户 id**（Runtime 才会自动注入 `X-Amzn-Bedrock-AgentCore-Runtime-User-Id` header）。 |
| **Code Interpreter** | （未来）可视化 / 结果二次分析 | 沙箱跑 Python 出图/统计；本期不启用。 |
| Runtime / Gateway / Browser | 不使用 | Runtime 与连接池冲突且高并发成本高；Gateway(MCP)/Browser 不在范围。 |

> 参考文档：Identity Overview / Obtain credentials / harness-vs-runtime / runtime-how-it-works（AWS Bedrock AgentCore devguide）。

---

## 8. 计算载体与部署

- **query-engine**：容器化，跑在**现有 EKS**（Deployment + Service + Ingress/ALB + HPA）。
- **ProxySQL**：EKS 上常驻。
- **网关**：Amazon API Gateway + 薄 Lambda。
- **权限 / IAM**：EKS Pod 用 **IRSA** 获取访问 Bedrock / OpenSearch / DynamoDB / Secrets / AgentCore 的权限。
- **不使用 ECS Fargate、不使用 AgentCore Runtime**。

**并发与成本要点**
- 高并发下,EKS 共享进程模型（共享连接池 / 缓存 / 资源打包）比 AgentCore Runtime 的“每会话 microVM”更省、吞吐更好。
- microVM 优点是会话级隔离，但与连接池冲突且高并发成本高，故不选。
- ⚠️ AgentCore 具体费率本文档未逐项核实,做成本模型前以官方定价页为准。

---

## 9. 安全红线 Checklist（研发自检）

- [ ] 每个请求都经过认证，身份可信来源（阳光云 OAuth）。
- [ ] Layer 5 **对所有查询路径生效**（含缓存命中、多轮）。
- [ ] 隔离条件由代码注入，**不依赖 LLM / 提示词**。
- [ ] 缓存只存**隔离前 SQL**，命中按当前用户重注。
- [ ] SQL 经 sqlglot **只读校验**，且 DB 用**只读账号**。
- [ ] Layer 5 有覆盖 JOIN/子查询/CTE/UNION 的边界测试。
- [ ] id 值转义 / 参数化，防注入。
- [ ] 隔离层操作可审计（日志留痕）。

---

## 10. 复用与自研清单（组件级）

| 能力 | 来源 | 说明 |
|------|------|------|
| 术语库 / SQL 示例召回 / 分阶段 Pipeline | **借鉴 SQLBot** | 向量检索改到 `VectorStore`；不取其闭源 xpack |
| 语义缓存“命中短路” | **自研** | SQLBot 只有 few-shot，需自建复用+短路 |
| **Layer 5 隔离** | **自研 sqlglot** | 未采用 WrenAI（避免全盘 MDL） |
| 只读 SQL 校验 | **借 pandas-ai 思路** | `is_sql_query_safe` |
| 认证骨架 / monorepo / Docker 模式 | **基于选定代码库 / 标准脚手架自建** | 阳光云 OAuth 2.0 接入 |
| 会话记忆 / 凭证托管 | **AgentCore Memory / Identity** | SDK 调用 |
| 硬术语归一化 | **自研** | 关键实体字典精确匹配 |

---

## 11. 待决事项 / 风险

1. **流式 vs 一次性返回**：影响 Layer 2 是否需绕开 API Gateway。需产品拍板。
2. **术语硬归一化字典的建设与维护**：谁维护、多久更新（关键实体准确率依赖它）。
3. **AgentCore 成本**：需按真实并发/时长 + 官方费率建模，确认 Memory/Identity 用量成本。
4. **MDL vs sqlglot 的长期取舍**：若隔离规则日趋复杂（多层组织/列级密级），需重估是否引入 WrenAI 的 MDL 确定性注入。
5. **Layer 5 边界正确性**：自研注入的“无懈可击”依赖充分测试，是主要工程风险点。

---

## 12. 开源项目选型结论（评估过的 7 个 text2sql 项目）

我们对本地 `../text2sql/` 下的 7 个真实项目做了源码级评估（详见差距分析与调研记录）。结论如下，供研发理解“为什么复用这些、不用那些”：

| 项目 | 性质 | 建议 | 复用点 / 不用理由 |
|------|------|:----:|------------------|
| **SQLBot**（DataEase） | 基于 LLM+RAG 的“智能问数系统”，与本项目定位一致 | 🟢 **复用组件** | 复用：术语库、SQL 示例召回、分阶段 Pipeline、StarRocks 适配、pgvector 检索思路（改到 `VectorStore`）。**不取**：行/列隔离依赖闭源 `sqlbot_xpack` 且靠 LLM 改写；语义缓存只是 few-shot，需自建“命中短路”。 |
| **WrenAI**（Canner） | 语义层(MDL)引擎，核心 `wren-core-py` 为 **Apache-2.0** | ⚪ **候选未采用** | 亮点：计划层**确定性 RLAC/CLAC** 行列隔离（`transform_sql`），是隔离红线的强方案。**未采用原因**：需全盘采用 MDL 建模范式、LLM 需生成语义 SQL；本期选自研 sqlglot。**隔离规则日趋复杂时可重估引入**。 |
| **pandas-ai** | 面向 DataFrame 的分析库 | 🟡 **借思路** | 复用：`is_sql_query_safe` 的**只读 SQL 校验**（关键字黑名单 + sqlglot）；未来可视化可参考其 docker 沙箱。无租户隔离。 |
| **vanna** | RAG 式可插拔 text2sql 框架 | 🟡 **参考架构** | 其**可插拔抽象**（`SqlRunner`/`ErrorRecoveryStrategy`/`UserResolver`/向量记忆）设计干净，可作为我们接口分层的参考。**无内建行级隔离**（需自实现）。 |
| **sqlchat** | 对话式查库前端（Next.js） | 🔴 **不用** | 纯前端产品，自带连接、执行任意 SQL，无隔离 / 无 schema pipeline / 无只读。 |
| **Chat2DB** | AI 增强 SQL 客户端 / IDE（Java） | 🔴 **不用** | 面向“连自己库”的开发者工具，非租户隔离的问数服务；无语义层 pipeline。 |
| **jimureport** | 低代码报表设计器（Java） | 🔴 **不用** | SQL 在报表模板手工配置，**非自然语言生成**，与本项目核心无关。 |

> 一句话：**主要复用 SQLBot（引擎组件）+ pandas-ai（只读校验）+ vanna（接口设计参考）**；**WrenAI 作为隔离层的备选方案保留**；sqlchat / Chat2DB / jimureport 不用。

---

## 13. 参考

- 架构图：`../smart-query-architecture-v2.drawio`
- 差距分析：`./datascout-gap-analysis.md`
- 开源参考：SQLBot（DataEase）、WrenAI（Canner，core Apache-2.0）、pandas-ai
- AWS 文档：Bedrock AgentCore devguide（Identity / Memory / Runtime / harness-vs-runtime）

---

*本文档基于团队讨论与对开源实现、AWS 官方文档的核对整理，作为 v2 落地依据；细节（尤其成本、配额、边界测试）落地前请复核。*
