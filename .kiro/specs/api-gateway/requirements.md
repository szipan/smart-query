# Requirements Document

## Introduction

本需求文档描述智能问数系统 v2 的 **Layer 2 —— api-gateway（统一入口 · 网关守门人）** 的功能与行为需求。需求由已批准的设计文档 `design.md` 反向推导，覆盖 Amazon API Gateway 边缘入口与薄 Lambda 编排层，以及旁路 legacy-router 的**路由决策部分**。

Layer 2 的天职是**「判断 + 转发 + 计数」**：对外暴露稳定公共 REST 契约、终结认证、执行单用户业务限流、做 Legacy/Query 路由决策、透传可信身份，并把下游结果整形为统一信封。本层**绝不**生成 SQL、查询数据库或调用 LLM；数据范围（org_ids/ps_ids）解析属于 Layer 5，不在本层。

本文档所有验收标准遵循 EARS 模式，且每条均可独立测试。文末的**可追溯性附录**将设计文档中的 9 条 Correctness Property 映射到覆盖它们的验收标准，供 `design.md` 的 `**Validates: Requirements X.Y**` 引用接线。

## Glossary

- **Layer_2**：api-gateway 层整体，由 API_Gateway 与 Thin_Lambda 组成的统一入口与守门人。
- **API_Gateway**：Amazon API Gateway 边缘入口，负责公共 REST 契约、TLS 终结、WAF 关联、边缘限流与（选项 A 下）认证终结。
- **Thin_Lambda**：薄 Lambda 编排层，AWS Lambda 上运行的编排胶水，只做判断、转发、计数。
- **Lambda_Authorizer**：REQUEST 型 Lambda Authorizer，负责按阳光云 OAuth 2.0 校验 JWT（认证终结选项 A）。
- **Rate_Limiter**：单用户业务限流器，基于 DynamoDB 原子计数按用户按窗口计数。
- **DynamoDB_Counter**：DynamoDB 中的单用户限流计数项，主键 pk = "USER#<user_id>#<window>"，含 count / limit / ttl。
- **Request_Router**：Legacy/Query 路由决策器，按问题内容匹配 Rule_Registry 得出路由目标。
- **Rule_Registry**：可插拔路由规则注册表，持有按优先级排序的 RoutingRule 列表。
- **Identity_Context**：可信身份上下文，含 user_id / tenant_id / scopes / raw_claims / request_id。
- **Engine_Client**：调用 EKS query-engine 内部端点的出站客户端。
- **Legacy_Client**：命中 Legacy 规则时委托 legacy-router 执行的出站客户端。
- **Stream_Endpoint**：SSE 流式变体（变体 B）入口，Lambda Function URL（RESPONSE_STREAM），绕开 API_Gateway。**v2 不实现**，仅作为未来扩展预留概念（见 Requirement 9 与 design.md §12）。
- **Query_Response**：成功响应的统一信封，含 request_id / status / data / source。
- **Error_Envelope**：失败响应的统一信封，含 code / message / request_id / retry_after。
- **Response_Shaper（响应整形层）**：Thin_Lambda 中把「下游（query-engine / legacy-router）原始响应」转换为面向客户端的 Query_Response 统一信封的那段逻辑，是编排流程的最后一步（整形回包）。
- **Downstream_Consumption_Point（下游响应消费点）**：Response_Shaper 读取下游响应体的那个位置。一次性形态下为"一次读完整个响应体，当作一个完整对象"；流式形态下为"循环读取一段段 chunk"。本期要求把它建模为"可分块（chunkable）"的可迭代抽象，本期只产出单块（即完整 JSON），行为等同一次性返回。
- **request_id**：贯穿全链路的追踪标识。
- **query-engine**：运行于 EKS 的下游查询引擎（Layer 3~7），本层的下游依赖。
- **legacy-router**：Layer 2 旁路的外部执行/适配层，执行命中 Legacy 规则的请求。

## Requirements

### Requirement 1: 公共 REST 契约

**User Story:** 作为客户端开发者（iOS/Android/Web/外部应用），我想要一个稳定的公共 REST 入口，以便所有端以统一契约调用问数服务。

#### Acceptance Criteria

1. THE API_Gateway SHALL 以方法 POST、资源路径 `/v1/query` 暴露公共 REST 契约。
2. WHEN 请求到达 `/v1/query`，THE API_Gateway SHALL 对该入站连接终结 TLS。
3. THE API_Gateway SHALL 关联 AWS WAF WebACL，对 `/v1/query` 应用 IP 速率规则与托管 SQLi/XSS 规则组。
4. THE API_Gateway SHALL 在阶段/方法级施加边缘限流，包含稳态速率上限与突发上限两个值。
5. WHEN 请求到达 `/v1/query`，THE API_Gateway SHALL 以 `AWS_PROXY`（Lambda Proxy）集成方式将原始请求整体转发给 Thin_Lambda。
6. THE API_Gateway SHALL 向 iOS、Android、Web、外部应用呈现完全相同的 `POST /v1/query` 契约。

### Requirement 2: 认证（Token 验证）

**User Story:** 作为平台安全负责人，我想要每个请求在到达引擎前只被认证一次，以便认证可信且不重复消耗资源。

#### Acceptance Criteria

1. WHERE 配置为网关终结选项（选项 A），WHEN 请求携带 bearer token，THE Lambda_Authorizer SHALL 在请求进入 Thin_Lambda 之前按阳光云 OAuth 2.0 校验该 token。
2. WHERE 配置为网关终结选项（选项 A），WHEN token 校验成功，THE Lambda_Authorizer SHALL 将已校验 claims 注入 `requestContext.authorizer` 供 Thin_Lambda 使用。
3. WHERE 配置为透传到引擎选项（选项 B），THE API_Gateway SHALL 将原始 `Authorization` 头原样透传给下游 query-engine 校验。
4. IF bearer token 缺失、已过期或签名无效，THEN THE Layer_2 SHALL 以 HTTP 状态码 401 拒绝该请求，并返回 code 为 `UNAUTHORIZED` 的 Error_Envelope。
5. THE Layer_2 SHALL 保证任一请求在任何下游 engine 或 legacy 调用之前，有且仅有一次成功的身份校验。
6. THE Layer_2 SHALL 仅执行身份认证（authentication），SHALL NOT 执行数据授权（数据范围 scope 解析）。

### Requirement 3: 薄 Lambda 编排（零业务智能）

**User Story:** 作为系统架构师，我想要薄 Lambda 严格按序编排且不含业务智能，以便本层保持「胶水」而非「大脑」。

#### Acceptance Criteria

1. WHEN 请求进入 Thin_Lambda，THE Thin_Lambda SHALL 按固定顺序执行编排：解析请求并构造身份 → 单用户业务限流 → Legacy/Query 路由决策 → 请求整形并透传身份 → 调用下游（query-engine 或 legacy-router）→ 整形回包。
2. THE Thin_Lambda SHALL 将其处理限定为解析、计数、路由决策、整形、转发这五类操作。
3. THE Thin_Lambda SHALL NOT 生成 SQL。
4. THE Thin_Lambda SHALL NOT 查询任何业务数据库。
5. THE Thin_Lambda SHALL NOT 调用任何 LLM。
6. THE Thin_Lambda SHALL 在路由决策之前、且在任何下游调用之前完成单用户限流检查。

### Requirement 4: 单用户业务限流

**User Story:** 作为服务运营方，我想要按用户按窗口限流，以便实现公平配额、防单用户滥用、控制成本。

#### Acceptance Criteria

1. WHEN Thin_Lambda 处理一个已认证请求，THE Rate_Limiter SHALL 对 DynamoDB 中主键 pk = "USER#<user_id>#<window>" 的计数项执行一次原子自增。
2. THE Rate_Limiter SHALL 使用条件更新，仅当当前 count 小于该用户配置的 limit 时才允许自增成功。
3. IF 某用户当期窗口计数超过其配置 limit，THEN THE Layer_2 SHALL 以 HTTP 状态码 429 响应，并返回 code 为 `RATE_LIMITED` 的 Error_Envelope。
4. WHEN 请求因超限被拒，THE Layer_2 SHALL 在响应中包含 `Retry-After` 头，指示距下一窗口的剩余秒数。
5. IF 请求超过单用户 limit，THEN THE Thin_Lambda SHALL NOT 对该请求执行路由决策或任何下游调用。
6. THE Rate_Limiter SHALL 为每个计数项设置 DynamoDB TTL 属性，使窗口过期后计数项被自动回收。
7. WHILE 同一 user_id 与同一窗口的多个请求被并发处理，THE Rate_Limiter SHALL 原子地自增计数且不丢失任何一次自增。

### Requirement 5: Legacy/Query 路由

**User Story:** 作为集成负责人，我想要按问题内容做可插拔的路由决策，以便新问题默认走引擎，而已注册的 Legacy 场景被委托到外部执行。

#### Acceptance Criteria

1. WHEN Thin_Lambda 持有一个已认证且未被限流的请求，THE Request_Router SHALL 将 question 对 Rule_Registry 进行规则匹配。
2. THE Request_Router SHALL 按 priority 升序遍历规则，并选取首个命中的规则。
3. WHEN 某规则命中，THE Request_Router SHALL 返回 target 为 `LEGACY` 并附带命中的 rule_id。
4. IF 没有任何规则匹配该 question，THEN THE Request_Router SHALL 返回 target 为 `QUERY`（默认 fail-to-query）。
5. WHEN 路由目标为 `LEGACY`，THE Legacy_Client SHALL 将执行委托给 legacy-router（外部执行/适配层）。
6. WHEN 路由目标为 `QUERY`，THE Engine_Client SHALL 调用 EKS 上的 query-engine。
7. THE Layer_2 SHALL 在本层完成路由决策，SHALL NOT 在本层执行 legacy 或 query 的实际执行。
8. THE Rule_Registry SHALL 支持从配置（文件/DynamoDB）重载规则而无需修改代码。

### Requirement 6: 身份透传

**User Story:** 作为 Layer 5 隔离层的实现者，我想要 Layer 2 完整可信地透传身份，以便我能据此解析数据范围。

#### Acceptance Criteria

1. WHEN 身份校验成功，THE Thin_Lambda SHALL 构造 Identity_Context，包含 user_id、tenant_id、scopes、raw_claims、request_id。
2. THE Thin_Lambda SHALL 在调用任何下游服务之前保证 Identity_Context.user_id 非空。
3. WHEN 调用 query-engine，THE Engine_Client SHALL 通过 HTTP 头（`X-Identity-User-Id`、`X-Request-Id`）与请求 body（`identity.raw_claims`）透传 Identity_Context。
4. THE Layer_2 SHALL NOT 从 raw_claims 解析或推导 org_ids 或 ps_ids（数据范围解析属于 Layer 5）。
5. THE Engine_Client SHALL 将 raw_claims 原样透传给引擎，不做修改。

### Requirement 7: 统一响应 / 错误信封

**User Story:** 作为客户端开发者，我想要一致的成功响应与错误信封，以便可预测地处理结果与故障。

#### Acceptance Criteria

1. WHEN 请求成功，THE Layer_2 SHALL 返回 Query_Response，包含 request_id、status、data、source。
2. THE Layer_2 SHALL 在由引擎处理时将 source 置为 `"query"`，在由 legacy-router 处理时将 source 置为 `"legacy"`。
3. IF 进入任何失败路径，THEN THE Layer_2 SHALL 返回 Error_Envelope，包含 code、message、request_id。
4. THE Layer_2 SHALL 按如下映射设置 HTTP 状态码：`UNAUTHORIZED`→401、`BAD_REQUEST`→400、`RATE_LIMITED`→429、`DOWNSTREAM_ERROR`→502/503/504。
5. THE Layer_2 SHALL 从面向客户端的 message 字段中排除内部实现细节（堆栈、SQL、身份内部细节）。
6. IF question 为空或长度超过 2000 字符，THEN THE Layer_2 SHALL 以 400 响应并返回 code 为 `BAD_REQUEST` 的 Error_Envelope。
7. IF 下游 query-engine 或 legacy-router 超时、返回 5xx 或连接失败，THEN THE Layer_2 SHALL 返回 code 为 `DOWNSTREAM_ERROR` 的 Error_Envelope。

### Requirement 8: request_id 全链路贯通

**User Story:** 作为运维人员，我想要 request_id 贯穿入口、下游与响应，以便端到端追踪与排障。

#### Acceptance Criteria

1. WHEN 请求到达，THE Layer_2 SHALL 生成新的 request_id，或采用客户端提供的 `X-Request-Id`。
2. THE Layer_2 SHALL 将 request_id 透传给每一次下游调用。
3. THE Layer_2 SHALL 将 request_id 写入该请求的每一条结构化日志。
4. THE Layer_2 SHALL 使响应的 request_id 恒等于该请求入口分配/透传的 request_id。

### Requirement 9: 一次性返回与流式扩展性（已决策：v2 仅实现一次性）

**User Story:** 作为产品与研发，我想要 v2 只落地一次性 JSON 路径，同时相关模块为未来流式做好扩展性准备，以便后续引入流式时无需重写核心逻辑，且不违反「认证只做一次、限流不缺席」。

#### Acceptance Criteria

1. WHEN 请求到达 `POST /v1/query`，THE API_Gateway SHALL 以一次性 JSON 变体（变体 A）处理该请求，并要求 query-engine 在 29 秒内返回。
2. THE Layer_2 SHALL NOT 在 v2 实现 SSE 流式变体（变体 B）。
3. IF 请求中 stream 为 true，THEN THE Layer_2 SHALL 以 400 响应并返回 code 为 `BAD_REQUEST` 的 Error_Envelope，指示流式在本期不可用。
4. THE Layer_2 SHALL 将 token 认证与身份构造逻辑实现为**传输无关**的共享库（不绑定 API Gateway event 结构），以便未来流式入口（Function URL）复用而无需重写。
5. THE Layer_2 SHALL 将单用户限流逻辑（Rate_Limiter）实现为独立于入口形态的共享库，以便未来流式路径复用同一实现。
6. THE Layer_2 SHALL 使编排逻辑（解析→限流→路由→透传→调下游→整形）不假设"一次性返回"，且 Response_Shaper（响应整形层）SHALL 把 Downstream_Consumption_Point（下游响应消费点）建模为可分块（chunkable）的可迭代抽象，而非写死为"下游响应必为完整对象、一次读完"。本期该抽象只产出单块（即完整 JSON），行为等同一次性返回；未来流式时可产出多块并逐块 emit，无需重写编排与整形逻辑。
7. THE Query_Response 信封结构 SHALL 设计为在未来流式下可作为「最终结果事件」复用，避免破坏客户端契约。
8. WHERE 未来引入流式变体 B，THE Stream_Endpoint SHALL 复用共享的 Identity_Context 与 Rate_Limiter 逻辑，自行完成等价的 token 认证、单用户限流与 WAF 等价防护，且两变体对任一单请求互斥。

### Requirement 10: 运行载体

**User Story:** 作为平台工程师，我想要本层固定运行在托管无服务器载体上，以便与既定架构约束一致。

#### Acceptance Criteria

1. THE Layer_2 SHALL 运行在托管无服务器组件之上（API Gateway + Lambda）。
2. THE Layer_2 SHALL NOT 使用 ECS Fargate。
3. THE Layer_2 SHALL 将运行于 EKS 的 query-engine 作为下游依赖进行调用。
4. THE Layer_2 SHALL NOT 依赖 LLM、StarRocks、sqlglot 或 ProxySQL。

### Requirement 11: 层边界

**User Story:** 作为系统架构师，我想要严格界定本层不拥有的职责，以便职责清晰、输出可预期。

#### Acceptance Criteria

1. THE Layer_2 SHALL NOT 执行术语归一化或语义缓存（属于 Layer 3）。
2. THE Layer_2 SHALL NOT 执行 SQL 生成（属于 Layer 4）。
3. THE Layer_2 SHALL NOT 执行数据隔离或数据范围解析（属于 Layer 5）。
4. THE Layer_2 SHALL NOT 执行查询执行（属于 Layer 6/7）。
5. THE Layer_2 SHALL 使其输出为下游结果信封或错误信封二者之一。

### Requirement 12: 可观测性

**User Story:** 作为运维人员，我想要每步的结构化日志与关键指标，以便监控与告警。

#### Acceptance Criteria

1. WHEN 每个编排步骤完成，THE Thin_Lambda SHALL 为该步骤（认证结果、限流决策、路由决策、下游耗时、状态码）打出一条结构化日志。
2. THE Thin_Lambda SHALL 在每条结构化日志中包含 request_id 与脱敏后的 user_id。
3. THE Layer_2 SHALL 发出 429 率、下游延迟、路由分布（query vs legacy）的指标。

## Appendix A：Correctness Property 可追溯性映射

下表将 `design.md` 中的 9 条 Correctness Property 映射到覆盖它们的验收标准。设计文档可据此为每条 Property 补充 `**Validates: Requirements X.Y**` 引用。

| 设计 Property | 描述 | 覆盖的验收标准 |
|---------------|------|----------------|
| Property 1：认证前置（Auth Before Engine） | 到达引擎前有且仅有一次成功认证 | 2.1, 2.2, 2.3, 2.4, 2.5 |
| Property 2：薄层零智能（No Business Intelligence） | 薄 Lambda 不生成 SQL / 不查库 / 不调 LLM | 3.2, 3.3, 3.4, 3.5, 11.2 |
| Property 3：身份必达（Identity Always Propagated） | 路由到引擎的请求 user_id 非空且被透传 | 6.1, 6.2, 6.3 |
| Property 4：不解析数据范围（No Scope Resolution in Layer 2） | 本层不产出 org_ids/ps_ids | 2.6, 6.4, 11.3 |
| Property 5：单用户限流生效（Per-User Limit Enforced） | 超限返回 429 且不调用下游 | 4.1, 4.2, 4.3, 4.4, 4.5, 4.7 |
| Property 6：路由确定性（Deterministic Routing） | 未命中→QUERY，命中→LEGACY（按优先级取首个） | 5.1, 5.2, 5.3, 5.4 |
| Property 7：限流早于路由与下游（Rate-Limit Ordering） | 限流检查严格早于路由与下游调用 | 3.1, 3.6, 4.5 |
| Property 8：错误信封统一（Uniform Error Envelope） | 失败路径产出带 request_id 的统一信封，状态码符合映射 | 7.3, 7.4, 7.5, 8.4 |
| Property 9：request_id 贯通（Request ID Propagation） | 响应 request_id 恒等于入口/透传 request_id | 8.1, 8.2, 8.3, 8.4 |
