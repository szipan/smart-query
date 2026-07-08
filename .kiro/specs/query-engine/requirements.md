# Requirements Document

## Introduction

本文档定义智能问数系统 v2 中 **Layer 3（query-engine，智能问数核心编排引擎）** 的需求，需求由既有设计文档 `.kiro/specs/query-engine/design.md` 派生而来，并与其正文、数据模型、数据流图及 10 条正确性属性保持一致。

Layer 3 是运行在 EKS 上的 Python 服务，唯一使命是把 Layer 2 透传下来的**已认证请求**（身份 + 原始问题 + 会话标识），通过顺序驱动**本层自有节点**（上下文改写、术语归一化、语义缓存）并**委派下游各层**（Layer 4 生成 SQL、Layer 5 隔离守卫、Layer 6/7 执行），最终转化为一个**面向当前用户的、安全的答案**。

本层两条设计红线：其一，**语义缓存命中也必须经过 Layer 5**——缓存只存"隔离前 SQL 模板"，命中后跳过 Layer 4 但仍按当前用户重注隔离；其二，**多轮上下文改写只在本层做**，交给 Layer 4 的必然是自包含独立问题，身份则原样透传给 Layer 5，本层从不接触用户数据范围（org_ids/ps_ids）。

本需求文档范围限定为 Layer 3 编排职责；下游内部实现分别以 `text2sql-pipeline`（Layer 4）与 `isolation-guard`（Layer 5）的设计为准，本文档不重复。

## Glossary

- **Query_Engine**：Layer 3 服务整体（query-engine），本文档所描述系统的总称。
- **Query_Orchestrator**：查询编排器，按固定顺序驱动本层节点并委派下游，管理请求级状态；只编排、不实现语义逻辑。
- **Context_Rewriter**：上下文改写 / 指代消解节点，基于 AgentCore Memory 的多轮能力把追问补全为独立问题。
- **Term_Normalizer**：术语归一化节点，内部编排硬、软两个子解析器。
- **Hard_Entity_Resolver**：硬归一化子解析器，经确定性字典把关键实体（电站名 / 指标名）映射到标准 id。
- **Soft_Glossary_Resolver**：软归一化子解析器，经术语库向量检索取同义词 + 定义。
- **Semantic_Cache**：语义复用缓存，负责 lookup / store / invalidate；只缓存隔离前 SQL 模板。
- **Memory_Client**：AgentCore Memory 的最小封装，按 actor（用户）+ session 隔离读写多轮会话。
- **Vector_Store**：可插拔向量存储（当前 OpenSearch），供语义缓存与软术语库检索，二者分命名空间。
- **Embedder**：文本向量化能力（当前 Bedrock / Cohere），与 Layer 4 共用抽象。
- **Llm_Client**：大模型客户端（当前 Bedrock Claude Haiku），供上下文改写使用。
- **Entity_Dictionary**：硬归一化字典（当前 DynamoDB），提供确定性精确 / 别名匹配。
- **Layer4_Client**：Layer 4（text2sql-pipeline）编排器入口，输入 `PipelineInput`、输出 `PipelineResult`。
- **Layer5_Guard**：Layer 5（isolation-guard）隔离守卫门面，暴露 `guard(sql, request_id) -> GuardResult`，每请求绑定身份。
- **Layer5_Guard_Factory**：按当前请求身份构造 `Layer5_Guard` 实例的工厂（`for_identity(identity)`）。
- **Datasource_Adapter**：Layer 6/7 数据源适配器，经只读账号 / ProxySQL 执行安全 SQL 并返回 `ResultSet`。
- **Engine_Observer**：可观测组件，记录 per-node 追踪并暴露关键指标。
- **Query_Context**：贯穿全流程的请求级共享状态对象，追加式写入；其中 `request` 字段不可变。
- **Query_Request**：Layer 3 入口输入，含 `request_id`、`identity`、`raw_question`、`session_id`、`datasource_id`、`model_id`、`locale`。
- **Caller_Identity**：可信调用方身份，含 `user_id` 等字段；由 Layer 2 认证后透传，Layer 3 原样透传给 Layer 5。
- **Standalone_Question**：经上下文改写后自包含、无未消解指代 / 省略的问题文本。
- **Resolved_Entity**：硬归一化产物（surface → canonical + entity_id）。
- **Resolved_Term**：软归一化产物（term + definition + synonyms）。
- **Pipeline_Input**：交给 Layer 4 的输入契约，由本层构造，绝不含 org_ids/ps_ids。
- **Pipeline_Result**：Layer 4 输出，含 `status`（OK / NO_SQL / ERROR）与 `unvalidated_sql`（隔离前 SQL）。
- **Guard_Result**：Layer 5 输出，含 `status`（ALLOWED / REJECTED）与 `safe_sql`（注入隔离后的成品 SQL）。
- **Cache_Lookup**：语义缓存查找结果，含 `hit`、`pre_isolation_sql`、`similarity`、`entry_id`。
- **Pre_Isolation_SQL**：隔离前 SQL 模板，不含任何 org_id/ps_id 权限过滤；缓存中唯一被存储的 SQL。
- **Answer**：Layer 3 最终产出信封，含 `status`（OK / NO_SQL / REJECTED / UPSTREAM_ERROR）、`rows`、`sql_source`、`message`、`request_traces`。
- **Node_Trace**：单节点可观测记录（节点名 / 耗时 / 成败 / 关键属性）。
- **Recent_Turns_Limit**：多轮读取轮数，默认 5。
- **Cache_Similarity_Threshold**：语义缓存命中阈值，默认 0.95。
- **Cache_Namespace**：语义缓存命名空间，取值 `semantic_cache`。
- **Glossary_Namespace**：软术语库命名空间，取值 `glossary`。

## Requirements

### Requirement 1: 查询编排（固定顺序 + 分支汇聚）

**User Story:** 作为智能问数系统的集成方，我希望 Layer 3 以固定顺序编排各步骤并只在缓存命中 / 未命中处分支，以便整个链路行为可预测、可测试、可审计。

#### Acceptance Criteria

1. WHEN THE Query_Orchestrator 收到一个 Query_Request, THE Query_Orchestrator SHALL 按固定顺序执行 context_rewrite、term_normalization、semantic_cache_lookup 三个节点。
2. WHEN THE Semantic_Cache 的 Cache_Lookup 结果为命中, THE Query_Orchestrator SHALL 跳过 Layer4_Client 并直接进入 Layer5_Guard。
3. WHEN THE Semantic_Cache 的 Cache_Lookup 结果为未命中, THE Query_Orchestrator SHALL 先调用 Layer4_Client 再进入 Layer5_Guard。
4. THE Query_Orchestrator SHALL 使命中路径与未命中路径均在 Layer5_Guard 之后才进入执行节点。
5. WHEN THE Layer5_Guard 返回 ALLOWED 且执行成功, THE Query_Orchestrator SHALL 先组装 Answer 再调用写回 Memory 步骤。
6. WHEN THE Query_Orchestrator 处理请求, THE Query_Orchestrator SHALL 初始化一个 Query_Context 并在所有节点间传递该对象。
7. THE Query_Orchestrator SHALL 将 SQL 生成委派给 Layer4_Client、将隔离注入委派给 Layer5_Guard、将物理执行委派给 Datasource_Adapter，而不在编排器内实现上述语义逻辑。

### Requirement 2: 上下文改写 / 指代消解（多轮）

**User Story:** 作为多轮对话用户，我希望我的追问能被补全为独立问题，以便下游无需依赖对话历史即可正确理解我的意图。

#### Acceptance Criteria

1. WHEN THE Context_Rewriter 处理请求, THE Context_Rewriter SHALL 以 actor_id 等于 Caller_Identity.user_id、session_id 等于 Query_Request.session_id 从 Memory_Client 读取最近 5 轮（Recent_Turns_Limit）会话。
2. IF THE Memory_Client 返回的历史轮次为空, THEN THE Context_Rewriter SHALL 将 Standalone_Question 置为 Query_Request.raw_question。
3. WHEN THE Memory_Client 返回非空历史轮次, THE Context_Rewriter SHALL 使用 Llm_Client 将 raw_question 改写为独立、自包含的 Standalone_Question。
4. IF THE Memory_Client 读取失败, THEN THE Context_Rewriter SHALL 将 Standalone_Question 置为 raw_question、将 Query_Context.memory_degraded 置为 True、并向 Query_Context.warnings 追加一条降级记录。

### Requirement 3: 术语归一化—硬通道（确定性实体解析）

**User Story:** 作为业务用户，我希望电站名、指标名等关键实体被确定性地映射到标准 id，以便查询命中正确的数据对象。

#### Acceptance Criteria

1. WHEN THE Hard_Entity_Resolver 处理 Standalone_Question, THE Hard_Entity_Resolver SHALL 经 Entity_Dictionary 将识别到的候选实体表述映射为带标准 id 的 Resolved_Entity。
2. THE Hard_Entity_Resolver SHALL 对同一实体表述恒定产出同一 Resolved_Entity，且不调用 Llm_Client、不引入随机性。
3. IF THE Entity_Dictionary 对某个候选表述无命中, THEN THE Hard_Entity_Resolver SHALL 不为该表述产出 Resolved_Entity 且继续处理其余表述而不中断主流程。
4. WHEN THE Hard_Entity_Resolver 完成解析, THE Hard_Entity_Resolver SHALL 将结果写入 Query_Context.resolved_entities。

### Requirement 4: 术语归一化—软通道（术语库向量检索）

**User Story:** 作为业务用户，我希望口语化描述词被补充同义词与定义，以便下游 LLM 在生成时能对齐业务口径。

#### Acceptance Criteria

1. WHEN THE Soft_Glossary_Resolver 处理 Standalone_Question, THE Soft_Glossary_Resolver SHALL 经 Embedder 向量化后在 Glossary_Namespace 用 Vector_Store 检索同义词与定义。
2. IF THE Vector_Store 检索结果为空, THEN THE Soft_Glossary_Resolver SHALL 不产出 Resolved_Term 且不中断主流程。
3. IF THE Vector_Store 检索失败, THEN THE Soft_Glossary_Resolver SHALL 不产出 Resolved_Term 且不中断主流程。
4. WHEN THE Soft_Glossary_Resolver 完成检索, THE Soft_Glossary_Resolver SHALL 将结果写入 Query_Context.resolved_terms。

### Requirement 5: 语义缓存—查找（lookup）

**User Story:** 作为系统运营方，我希望语义相近的历史问题能被复用，以便降低下游生成延迟与成本。

#### Acceptance Criteria

1. WHEN THE Semantic_Cache 执行 lookup, THE Semantic_Cache SHALL 经 Embedder 向量化 Standalone_Question 并在 Cache_Namespace 以 top_k 等于 1 做相似检索。
2. IF 最相似条目的相似度大于等于 Cache_Similarity_Threshold（0.95）, THEN THE Semantic_Cache SHALL 返回 hit 为 True 且携带该条目的 Pre_Isolation_SQL 与相似度分值。
3. IF 最相似条目的相似度小于 0.95, THEN THE Semantic_Cache SHALL 返回 hit 为 False。
4. IF THE Vector_Store 检索无结果, THEN THE Semantic_Cache SHALL 返回 hit 为 False。
5. IF THE lookup 过程失败, THEN THE Semantic_Cache SHALL 按未命中处理返回 hit 为 False 并向 Query_Context.warnings 追加一条记录。

### Requirement 6: 语义缓存—命中短路（仍过 Layer 5）

**User Story:** 作为安全负责人，我希望缓存命中在跳过生成的同时绝不跳过隔离，以便复用不会导致跨用户越权。

#### Acceptance Criteria

1. WHEN THE Cache_Lookup 命中, THE Query_Orchestrator SHALL 将命中的 Pre_Isolation_SQL 写入 Query_Context.pre_isolation_sql 并将 Query_Context.sql_source 置为 CACHE_HIT。
2. WHEN THE Cache_Lookup 命中, THE Query_Orchestrator SHALL 跳过 Layer4_Client。
3. WHEN THE Cache_Lookup 命中, THE Query_Orchestrator SHALL 仍将 Pre_Isolation_SQL 送入 Layer5_Guard 以按当前用户重注隔离。
4. WHILE 处于命中路径, THE Query_Orchestrator SHALL NOT 触发 Semantic_Cache 的 store 操作。

### Requirement 7: 语义缓存—存储策略与失效

**User Story:** 作为安全负责人，我希望缓存只存隔离前 SQL 且仅在验证并执行成功后写入，以便避免越权泄露与污染坏条目。

#### Acceptance Criteria

1. IF 本次为缓存未命中且 Layer5_Guard 返回 ALLOWED 且安全 SQL 执行成功, THEN THE Semantic_Cache SHALL 将 Pipeline_Result.unvalidated_sql（即 Pre_Isolation_SQL）写入 Cache_Namespace。
2. THE Semantic_Cache SHALL 仅写入 Pre_Isolation_SQL，且不将 Guard_Result.safe_sql（注入隔离后的成品 SQL）写入缓存。
3. IF 未命中、Layer 5 通过、执行成功三个条件中任一不满足, THEN THE Semantic_Cache SHALL 不写入缓存。
4. WHEN 收到 schema 版本失效、TTL 到期或显式 invalidate 调用, THE Semantic_Cache SHALL 失效对应的缓存条目。

### Requirement 8: 委派 Layer 4（构造输入 / 处理结果）

**User Story:** 作为 Layer 4 的上游，我希望在缓存未命中时收到一个干净、自包含且不含用户范围的输入，以便 Layer 4 专注生成 SQL。

#### Acceptance Criteria

1. WHEN 缓存未命中, THE Query_Orchestrator SHALL 构造一个 Pipeline_Input，其 standalone_question 等于 Query_Context.standalone_question，并携带 Query_Context.resolved_entities 与 Query_Context.resolved_terms。
2. THE Query_Orchestrator SHALL 构造不含任何 org_ids 或 ps_ids 字段的 Pipeline_Input。
3. WHEN THE Pipeline_Result.status 等于 OK, THE Query_Orchestrator SHALL 取 Pipeline_Result.unvalidated_sql 写入 Query_Context.pre_isolation_sql 并将 Query_Context.sql_source 置为 PIPELINE。
4. IF THE Pipeline_Result.status 等于 NO_SQL, THEN THE Query_Orchestrator SHALL 返回状态为 NO_SQL 的 Answer 且不写缓存、不执行 SQL。
5. IF THE Pipeline_Result.status 等于 ERROR, THEN THE Query_Orchestrator SHALL 返回状态为 UPSTREAM_ERROR 的 Answer 且不写缓存、不执行 SQL。

### Requirement 9: 委派 Layer 5（统一守卫入口 / 每请求构造）

**User Story:** 作为安全负责人，我希望任何待执行 SQL 都经同一守卫入口按当前身份处理，以便隔离与只读校验无法被绕过。

#### Acceptance Criteria

1. WHEN 需要守卫待执行 SQL, THE Query_Orchestrator SHALL 对命中的 Pre_Isolation_SQL 与 Layer 4 的未校验 SQL 使用同一个 `guard(sql, request_id)` 入口。
2. WHEN THE Guard_Result.status 等于 ALLOWED, THE Query_Orchestrator SHALL 将 Guard_Result.safe_sql 送入执行节点。
3. IF THE Guard_Result.status 等于 REJECTED, THEN THE Query_Orchestrator SHALL 返回状态为 REJECTED 的 Answer 且不执行 SQL。
4. WHEN THE Query_Orchestrator 处理请求, THE Query_Orchestrator SHALL 经 Layer5_Guard_Factory.for_identity(identity) 为该请求构造 Layer5_Guard 实例。

### Requirement 10: 身份透传（不解析范围）

**User Story:** 作为安全负责人，我希望 Layer 3 只透传身份而绝不接触用户数据范围，以便范围解析职责唯一归属 Layer 5。

#### Acceptance Criteria

1. THE Query_Orchestrator SHALL 将 Caller_Identity 逐字段一致地透传给 Layer5_Guard，不修改、不增删任何身份声明。
2. THE Query_Context SHALL NOT 包含 org_ids 或 ps_ids 字段。
3. THE Query_Orchestrator SHALL NOT 解析用户数据范围，且不构造带权限过滤子句的 SQL。

### Requirement 11: 执行与答案组装

**User Story:** 作为终端用户，我希望根据执行结果得到状态明确的答案，以便清楚查询是成功、无法生成、被拒绝还是上游出错。

#### Acceptance Criteria

1. WHEN THE Guard_Result.status 等于 ALLOWED, THE Datasource_Adapter SHALL 执行 Guard_Result.safe_sql 并返回 ResultSet。
2. WHEN 安全 SQL 执行成功并返回结果行, THE Query_Orchestrator SHALL 组装状态为 OK 且 rows 为该 ResultSet 的 Answer。
3. WHEN 组装 Answer, THE Query_Orchestrator SHALL 将 Answer.sql_source 置为对应来源（CACHE_HIT 或 PIPELINE）。
4. IF THE Datasource_Adapter 执行失败或返回空 ResultSet, THEN THE Query_Orchestrator SHALL 返回状态为 UPSTREAM_ERROR 的 Answer 且不写缓存。

### Requirement 12: 写回 Memory

**User Story:** 作为多轮对话用户，我希望本轮问答被记录，以便下一轮追问能被正确改写。

#### Acceptance Criteria

1. WHEN THE Query_Orchestrator 收尾, THE Query_Orchestrator SHALL 调用 Memory_Client.write_turn 写回本轮的 Standalone_Question 与答案摘要。
2. IF THE Memory_Client.write_turn 失败, THEN THE Query_Orchestrator SHALL 向 Query_Context.warnings 追加一条 warning 且仍正常返回本轮 Answer。

### Requirement 13: 层边界与状态不变式

**User Story:** 作为系统架构师，我希望 Layer 3 严守职责边界并维持请求级状态的不变式，以便各层关注点清晰分离、可独立演进。

#### Acceptance Criteria

1. THE Query_Orchestrator SHALL NOT 执行认证或限流（该职责属于 Layer 2）。
2. THE Query_Orchestrator SHALL NOT 生成 SQL（该职责属于 Layer 4）。
3. THE Query_Orchestrator SHALL NOT 注入数据隔离或强制只读校验（该职责属于 Layer 5）。
4. THE Query_Orchestrator SHALL NOT 持有连接池或执行物理查询（该职责属于 Layer 6/7）。
5. WHEN 任一节点写入 Query_Context, THE Query_Context SHALL 以追加方式写入而不覆盖其他节点已写入的产出字段。
6. WHEN 任一节点执行完成, THE Query_Context.request 字段（原始问题、身份、session）SHALL 与节点执行前逐字段相同。

### Requirement 14: 可观测

**User Story:** 作为运维人员，我希望链路可追踪、有指标且脱敏，以便定位问题并度量缓存、延迟与降级情况。

#### Acceptance Criteria

1. THE Query_Orchestrator SHALL 使 request_id 贯穿本层并透传给 Layer4_Client（Pipeline_Input.request_id）与 Layer5_Guard（guard 的 request_id 参数）。
2. WHEN 每个节点执行, THE Engine_Observer SHALL 向 Query_Context.traces 追加一条 Node_Trace，记录节点耗时、成败、以及相似度或下游状态等关键属性。
3. THE Engine_Observer SHALL 暴露缓存命中率、各节点延迟、Layer 5 拒绝率与 Memory 降级率指标。
4. THE Engine_Observer SHALL 对输出日志脱敏，不落用户数据范围与敏感明文凭证。

### Requirement 15: 复用与许可约束

**User Story:** 作为技术负责人，我希望本层的复用与许可边界清晰，以便合规使用第三方思路与既有系统抽象。

#### Acceptance Criteria

1. THE Soft_Glossary_Resolver SHALL 借鉴 SQLBot（GPLv3）的术语库 / 示例召回思路重新实现，且不复制其 GPLv3 源码。
2. THE Query_Engine SHALL 与 Layer 4 共用同一套 Vector_Store 与 Embedder 抽象。
3. THE Query_Engine SHALL 使用 AgentCore Memory 承载多轮会话、使用 AgentCore Identity 托管身份与凭证。

## Appendix A：正确性属性 → 验收标准 可追溯性映射

下表将 `design.md` 中 Layer 3 的 10 条正确性属性（Property 1 ~ Property 10）映射到本文档中覆盖它们的验收标准，确保每条属性均有可验证的需求支撑。

| 设计属性 | 属性含义（摘要） | 覆盖的验收标准 |
|---------|-----------------|---------------|
| Property 1：缓存命中仍过 Layer 5 | 命中取出的隔离前 SQL 必然经 Layer5_Guard 后才可能执行 | 1.2、1.4、6.2、6.3、9.1 |
| Property 2：缓存永不存注入后 SQL | 写入缓存的恒为隔离前 SQL，绝不存 safe_sql | 7.1、7.2 |
| Property 3：Layer 4 收到独立问题 | Pipeline_Input.standalone_question 为改写后的自包含问题 | 2.2、2.3、8.1 |
| Property 4：L3 不注入隔离 / 不解析范围 | 送下游的载荷不含权限过滤，L3 不解析 scope | 8.2、10.1、10.2、10.3 |
| Property 5：硬归一化确定性 | 同一实体表述恒定映射同一标准 id，不依赖 LLM / 随机 | 3.2 |
| Property 6：编排顺序固定 | 固定节点序列，分支仅在命中 / 未命中且都经 Layer 5 | 1.1、1.2、1.3、1.4 |
| Property 7：身份透传一致 | 传给 Layer 5 的身份与入口身份逐字段相同 | 9.4、10.1 |
| Property 8：上下文输入不可变 | 任一节点执行后 Query_Context.request 保持不变 | 13.5、13.6 |
| Property 9：存缓存前置条件 | 仅"未命中 ∧ 通过 ∧ 执行成功"三者同时满足才写缓存 | 7.1、7.3、11.4 |
| Property 10：答案状态映射 | Answer.status 与实际结果一致（OK / NO_SQL / REJECTED / UPSTREAM_ERROR） | 8.4、8.5、9.2、9.3、11.2、11.4 |
