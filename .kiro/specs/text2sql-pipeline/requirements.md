# Requirements Document

## Introduction

本需求文档从已批准的设计文档 `design.md`（智能问数系统 v2 · **Layer 4 — Text-to-SQL Pipeline（插拔式）**）反向推导而来，用于以可验证、可追溯的方式固化设计意图。

Layer 4 的职责是：把一个**已由 Layer 3 改写为独立、自包含的自然语言问题**转换为一条**未经安全校验的候选 SQL（unvalidated SQL）**，并连同元数据移交 Layer 5（隔离守卫）。其核心不是实现某一条固定链路，而是把链路拆成**可插拔、可配置切换、彼此隔离、易扩展**的若干阶段：上下文获取（Schema Linking）→ SQL 生成 → SQL 选择 → Query Fixer。

三大设计目标为**抽象（Abstraction）、隔离（Isolation / 关注点分离）、可扩展（Extensibility）**。本文档的每条需求均以 EARS 模式书写，验收标准力求精确、可测试，并与设计文档 §Correctness Properties 中的 8 条正确性属性对齐（映射见文末附录）。

> 本文档仅描述“做什么（what）”，实现细节（how）见 `design.md`。安全隔离与只读强制的正确性由 Layer 5 保证，不属于本层职责。

## Glossary

- **Layer_4 / 流水线（Pipeline）**：本文档所描述的 Text-to-SQL 插拔式流水线，输出未校验 SQL。
- **编排器（Orchestrator）**：按固定阶段顺序驱动各阶段执行、解析配置并组装结果的组件。
- **阶段（Stage）**：流水线中的一个可插拔处理单元，实现统一的 `PipelineStage` 接口。
- **上下文获取阶段（Context_Acquisition_Stage）**：阶段一，产出生成 SQL 所需的 schema / 示例 / 术语 / 语义层片段等上下文。
- **SQL生成阶段（SQL_Generation_Stage）**：阶段二，组装提示词并调用 LLM 生成一个或多个候选 SQL。
- **SQL选择阶段（SQL_Selection_Stage）**：阶段三，从候选集合中选出一条 `selected_sql`。
- **Query修复阶段（Query_Fixer_Stage）**：阶段四，对选定 SQL 做有限次结构化修复。
- **策略注册表（Strategy_Registry）**：以 `(stage_type, strategy_name)` 为键注册与创建阶段实现的组件。
- **PipelineConfig**：决定每个阶段选用哪个策略实现的配置模型。
- **PipelineInput**：Layer 4 的入口输入，为已解析实体 / 术语的独立问题；**不含用户数据范围**。
- **PipelineContext**：贯穿全流程、追加式写入的阶段间共享状态容器。
- **PipelineResult**：Layer 4 的最终产出，含未校验 SQL、状态、trace 与元数据。
- **StageTrace**：单阶段可观测记录（策略名、起止时间、ok、error、attrs）。
- **StageDependencies**：注入给各阶段的能力抽象集合。
- **PipelineStatus**：结果状态枚举，取值 `OK` / `NO_SQL` / `FIXER_EXHAUSTED` / `ERROR`。
- **未校验SQL（unvalidated SQL）**：Layer 4 产出、尚未做隔离注入与只读校验的候选 SQL。
- **VectorStore**：向量写入 / 相似检索抽象（当前实现 OpenSearch）。
- **Embedder**：文本 → 向量抽象（当前实现 Bedrock / Cohere Embed）。
- **LlmClient**：大模型调用抽象（当前实现 Amazon Bedrock，默认 Claude Sonnet）。
- **DatasourceAdapter**：数据源方言 / 只读探测执行 / schema 片段抽象（当前实现 StarRocks 只读）。
- **SemanticLayerStore**：语义层片段的存取抽象（`semantic_layer` 策略所需）。
- **IsolationValidator**：Layer 5 暴露给 Query 修复阶段探测执行的最小校验入口（隔离注入 + 只读校验）。
- **StrategyResolutionError**：编排器在启动阶段解析策略实现失败时抛出的错误。
- **UnknownStrategyError**：以未注册的 `(stage_type, strategy_name)` 查找时策略注册表抛出的错误。
- **DuplicateStrategyError**：以已存在的 `(stage_type, strategy_name)` 重复注册时策略注册表抛出的错误。
- **Layer_3**：上游查询引擎节点，负责多轮上下文改写、指代消解与术语归一化。
- **Layer_5**：隔离守卫，负责数据隔离注入（`org_id/ps_id`）与只读强制校验。
- **Layer_6**：连接代理层（ProxySQL），提供连接池执行。

## Requirements

### Requirement 1: 配置驱动的插拔式阶段编排

**User Story:** 作为平台研发，我希望流水线的四个阶段实现均由配置选择、并按固定顺序编排，以便在不改动骨架的前提下切换算法。

#### Acceptance Criteria

1. WHEN 一次流水线执行开始, THE 编排器 SHALL 依据 PipelineConfig 从策略注册表解析出上下文获取、SQL生成、SQL选择、Query修复四个阶段的实现实例。
2. THE 编排器 SHALL 按 `context_acquisition → sql_generation → sql_selection → query_fixer` 的固定顺序依次执行各阶段。
3. THE 编排器 SHALL 仅在前一阶段成功完成后才启动下一阶段。
4. IF PipelineConfig 为某阶段指定的实现名称未在策略注册表中注册, THEN THE 编排器 SHALL 抛出 `StrategyResolutionError`、在执行任何阶段之前终止本次流水线，并在错误信息中指明缺失的阶段类型与实现名称。
5. IF 某下游阶段执行时其所需前置字段为空（值为 null 或未设置）, THEN THE 编排器 SHALL 抛出 `StagePreconditionError`、终止流水线，并在错误信息中指明该阶段名称与缺失的字段名。
6. WHERE 某阶段在 PipelineConfig 中配置了非空 `params`, THE 编排器 SHALL 将该 `params` 原样传入对应策略实例。
7. WHERE 某阶段在 PipelineConfig 中缺省或提供空 `params`, THE 编排器 SHALL 以该策略的默认参数运行对应策略实例。

### Requirement 2: 上下文获取的抽象与可互换策略

**User Story:** 作为算法研发，我希望“上下文获取”是一个抽象的、可互换的策略点，以便在 RAG、语义层、MDL 等实现间切换而不触碰其他阶段。

#### Acceptance Criteria

1. THE 上下文获取阶段 SHALL 通过统一的 `PipelineStage` 接口暴露实现，且该接口方法签名对所有策略保持一致。
2. THE 上下文获取阶段 SHALL 支持以下五种可互换策略且仅支持这五种：`rag`、`semantic_layer`、`mdl`、`m_schema`、`nl_semantic_layer`。
3. WHERE PipelineConfig 未显式指定上下文获取策略, THE 编排器 SHALL 使用 `rag` 作为默认策略。
4. IF PipelineConfig 指定的上下文获取策略名称未在策略注册表中注册, THEN THE 编排器 SHALL 终止本次上下文获取阶段、不修改 `PipelineContext.retrieved_context`，并返回指示“策略名称无效”的错误。
5. WHEN 一个新的上下文获取策略实现被注册到策略注册表, THE 编排器与其余阶段 SHALL 无需修改任何源代码即可选用该策略。
6. WHEN 一个新的上下文获取策略实现被注册到策略注册表, THE 编排器与其余阶段 SHALL 保持 `PipelineContext` 的字段集合与字段类型不变。
7. WHEN 上下文获取阶段成功完成, THE 上下文获取阶段 SHALL 向 `PipelineContext.retrieved_context` 写入检索结果。
8. WHEN 上下文获取阶段成功完成, THE 上下文获取阶段 SHALL 将本次实际使用的策略名称写入 `PipelineContext` 的 `strategy_name` 字段，且其值等于所选策略的标识。
9. IF 上下文获取阶段在检索过程中失败, THEN THE 上下文获取阶段 SHALL 不写入部分结果、保持 `PipelineContext.retrieved_context` 为写入前的状态，并返回指示“检索失败”的错误。

### Requirement 3: 语义层检索策略

**User Story:** 作为数据研发，我希望有一个基于语义层的上下文获取策略，先构建/维护语义层，再按问题检索相关上下文组装生成提示词，以提升复杂业务口径下的生成质量。

#### Acceptance Criteria

1. WHERE 配置为 `semantic_layer` 策略, THE 上下文获取阶段 SHALL 依据问题从语义层检索相关的模型 / 度量 / 维度片段。
2. WHEN 检索到相关片段, THE 上下文获取阶段 SHALL 将其组装进 `RetrievedContext` 的 `semantic_fragments`、`schema_snippets` 与 `term_glossary` 字段。
3. WHERE `semantic_layer` 策略的相关性阈值经 `params` 配置, THE 上下文获取阶段 SHALL 仅保留相关性得分不低于该阈值的片段，其中阈值默认 `0.7`、取值范围 `0.0` 至 `1.0`。
4. WHERE `semantic_layer` 策略的最大片段数经 `params` 配置, THE 上下文获取阶段 SHALL 将组装的片段数限制在该上限以内，其中上限默认 `20`、取值范围 `1` 至 `100`。
5. WHEN 语义层检索未命中任何满足阈值的片段, THE 上下文获取阶段 SHALL 返回字段均为空集合的 `RetrievedContext`，且不抛出异常。
6. IF `semantic_layer` 策略被选用但未注入 SemanticLayerStore, THEN THE 上下文获取阶段 SHALL 抛出 `ConfigurationError` 并不返回任何 `RetrievedContext`。
7. THE 语义层 SHALL 将其可检索表征写入 VectorStore 的独立命名空间 `semantic_layer`，与其他用途的向量索引相互隔离。
8. WHEN 数仓 schema 发生变更, THE 语义层维护流程 SHALL 仅对受影响的片段进行增量重建，并保持未受影响片段的表征不变。
9. IF 语义层片段的增量重建失败, THEN THE 语义层维护流程 SHALL 保留该片段重建前的表征，并记录指示重建失败的错误。

### Requirement 4: 带模型选择的 SQL 生成

**User Story:** 作为算法研发，我希望默认用 Bedrock Claude Sonnet 生成 SQL，并支持请求级模型覆盖与可选的多生成器 / 门控路由，以便在质量与成本间权衡。

#### Acceptance Criteria

1. WHERE PipelineConfig 未指定生成策略, THE SQL生成阶段 SHALL 使用 `single_llm` 默认策略，并默认调用 Bedrock Claude Sonnet 模型。
2. WHEN `PipelineInput.model_id` 非空且指向受支持且可用的模型, THE SQL生成阶段 SHALL 使用该请求级指定模型，其优先级高于策略默认模型。
3. WHEN SQL生成阶段完成, THE SQL生成阶段 SHALL 向 `PipelineContext.sql_candidates` 追加候选：`single_llm` 策略下恰好 1 个 `SqlCandidate`，`multi_generator` 与 `gated_routing` 策略下 2 至 10 个 `SqlCandidate`。
4. WHEN SQL生成阶段追加任一 `SqlCandidate`, THE SQL生成阶段 SHALL 确保该候选的 `sql`、`generator_name`、`model_id` 三个字段均非空。
5. WHERE 配置为 `multi_generator` 或 `gated_routing`, THE SQL生成阶段 SHALL 产出至少 2 个候选，供 SQL选择阶段挑选。
6. THE SQL生成阶段 SHALL 在生成提示词与产出 SQL 中排除任何权限过滤条件（隔离由 Layer 5 负责）。
7. IF `PipelineInput.model_id` 非空但指向不受支持或不可用的模型, THEN THE SQL生成阶段 SHALL 拒绝该请求、不向 `PipelineContext.sql_candidates` 追加任何候选，并返回指示模型不可用的错误。
8. IF 模型调用失败或在 60 秒内未返回结果, THEN THE SQL生成阶段 SHALL 终止本次生成、不追加部分候选，并返回指示 SQL 生成失败的错误。

### Requirement 5: SQL 选择

**User Story:** 作为算法研发，我希望默认直通选择首个候选，并可选自一致性 / 选择 Agent，以便在多候选场景提升准确率。

#### Acceptance Criteria

1. WHERE 未指定选择策略, THE SQL选择阶段 SHALL 使用 `passthrough` 默认策略并选取按候选生成顺序排列的第一个候选。
2. IF 候选集合为空, THEN THE SQL选择阶段 SHALL 抛出 `StagePreconditionError`。
3. WHERE 配置为 `self_consistency`, THE SQL选择阶段 SHALL 依据自一致性投票算法从候选集合选出一条 `selected_sql`。
4. WHERE 配置为 `selection_agent`, THE SQL选择阶段 SHALL 依据选择 Agent 打分算法从候选集合选出一条 `selected_sql`。
5. IF PipelineConfig 指定的选择策略名称未在策略注册表中注册, THEN THE SQL选择阶段 SHALL 返回指示“选择策略无效”的错误，并不写入 `PipelineContext.selected_sql`。
6. IF 选择算法在执行过程中失败, THEN THE SQL选择阶段 SHALL 返回指示“选择失败”的错误，并不写入 `PipelineContext.selected_sql`。
7. WHEN SQL选择阶段成功选出候选, THE SQL选择阶段 SHALL 向 `PipelineContext.selected_sql` 写入所选候选。

### Requirement 6: 有界重试的 Query Fixer 与探测执行隔离

**User Story:** 作为安全与算法研发，我希望 Query Fixer 只做有限次修复，且若进行探测执行必须先经 Layer 5 校验，绝不绕过数据隔离。

#### Acceptance Criteria

1. THE Query修复阶段 SHALL 将修复重试次数限制在 `max_attempts` 以内，即 `ctx.fix_attempts ≤ max_attempts`，其中 `max_attempts` 默认 `3`、取值范围为 `1` 至 `10` 的整数。
2. WHERE Query修复阶段执行探测执行（dry_run）, THE Query修复阶段 SHALL 先调用 `IsolationValidator.validate_for_probe` 取得已通过 Layer 5 校验的 SQL。
3. WHERE Query修复阶段执行探测执行（dry_run）, THE Query修复阶段 SHALL 仅对经 `validate_for_probe` 返回的 SQL 进行只读试跑，且该试跑 SHALL 不产生任何数据写入、并在 30 秒内完成。
4. IF 未注入 IsolationValidator, THEN THE Query修复阶段 SHALL 跳过探测执行并将 `selected_sql` 直接作为 `final_sql`。
5. WHEN 探测执行失败, THE Query修复阶段 SHALL 将报错信息回灌 LLM 以生成修正后的候选 SQL。
6. WHILE `ctx.fix_attempts < max_attempts`, THE Query修复阶段 SHALL 在每次探测失败后将 `fix_attempts` 递增 1 并以修正后的候选重试探测。
7. IF `ctx.fix_attempts` 达到 `max_attempts` 仍未通过探测, THEN THE Query修复阶段 SHALL 不产出 `final_sql`，并返回指示“修复用尽”的可观测失败信息。

### Requirement 7: 严格的层边界（仅产出未校验 SQL）

**User Story:** 作为安全负责人，我希望 Layer 4 只产出“未校验 SQL”，绝不注入数据隔离或执行只读强制，也不接触用户数据范围，以杜绝越权风险。

#### Acceptance Criteria

1. THE 流水线 SHALL 仅输出未校验 SQL（unvalidated SQL），其隔离注入与只读校验由 Layer 5 负责。
2. THE 流水线 SHALL 在产出的 SQL 中排除 `org_id` / `ps_id` 等数据隔离过滤条件。
3. THE 只读强制校验（禁止非 SELECT） SHALL 由 Layer 5 承担。
4. THE 流水线 SHALL 不在 Layer 4 执行只读强制校验。
5. THE PipelineInput SHALL 不携带用户数据范围（`org_ids` / `ps_ids`）。
6. THE 流水线 SHALL 不进行权限解析（用户 → `org_ids` / `ps_ids`）。
7. THE 流水线 SHALL 将输入视为已由 Layer 3 完成处理的独立、自包含问题。
8. THE 流水线 SHALL 在 Layer 4 排除对多轮上下文改写 / 指代消解与术语归一化的重复执行。
9. IF 检测到本层执行了越界职责（隔离注入、只读强制或权限解析）, THEN THE 流水线 SHALL 返回指示“层边界违规”的错误，并不产出未校验 SQL。

### Requirement 8: 阶段隔离与共享上下文通信

**User Story:** 作为平台研发，我希望阶段之间仅通过共享的 PipelineContext（追加式）通信、互不耦合，从而每个阶段可独立测试与 mock。

#### Acceptance Criteria

1. WHEN 任意阶段执行完成后, THE 该阶段 SHALL 保持 `PipelineContext.input` 与执行前深度相等（所有字段值逐一相等，无新增、删除或修改）。
2. THE 每个阶段 SHALL 仅写入其在 StageDependencies 中声明负责的产出字段。
3. THE 每个阶段 SHALL 保持所有非其声明负责的产出字段与执行前深度相等（无新增、删除或修改）。
4. THE 各阶段 SHALL 仅通过对共享 PipelineContext 的追加式写入（仅新增字段，不修改或删除已有字段）交换状态。
5. THE 各阶段 SHALL 排除对其他阶段内部实现模块的直接引用（导入依赖清单中不包含其他阶段的内部模块）。
6. WHEN 以 mock 依赖替换 StageDependencies 注入的全部外部能力抽象后, THE 各阶段 SHALL 在无真实外部依赖的条件下完成独立单元测试执行。

### Requirement 9: 可扩展性与策略注册

**User Story:** 作为算法研发，我希望以 `(stage_type, strategy_name)` 注册策略，新增策略只需新增实现类与一行注册，且未知策略应显式报错。

#### Acceptance Criteria

1. THE 策略注册表 SHALL 以 `(stage_type, strategy_name)` 为键注册阶段实现，其中 `stage_type` 与 `strategy_name` 均为非空字符串。
2. WHEN 以已注册的 `(stage_type, strategy_name)` 为键发起查找, THE 策略注册表 SHALL 返回该键对应的已注册阶段实现。
3. IF PipelineConfig 引用了未在策略注册表中注册的 `(stage_type, strategy_name)`, THEN THE 策略注册表 SHALL 抛出 `UnknownStrategyError`，且不返回任何阶段实现。
4. IF 以已存在的 `(stage_type, strategy_name)` 重复注册, THEN THE 策略注册表 SHALL 抛出 `DuplicateStrategyError`，且保留首次注册的阶段实现不被覆盖。
5. WHEN 以某个 `stage_type` 调用 `available(stage_type)`, THE 策略注册表 SHALL 返回该 `stage_type` 下全部已注册 `strategy_name` 的集合。
6. IF 调用 `available(stage_type)` 时该 `stage_type` 无任何已注册策略, THEN THE 策略注册表 SHALL 返回空集合而非抛出错误。

### Requirement 10: 可观测性

**User Story:** 作为运维与算法研发，我希望每个阶段都产出可追踪的 trace（策略、耗时、成功/失败），且不记录敏感信息，以便调优与排障。

#### Acceptance Criteria

1. WHEN 每个阶段执行完成, THE 编排器 SHALL 生成一条 StageTrace，其中包含 `strategy_name`、阶段起始时间戳（精确到毫秒）、阶段结束时间戳（精确到毫秒）以及表示执行结果的 `ok` 布尔字段。
2. WHEN 某阶段执行成功, THE 编排器 SHALL 将该 StageTrace 的 `ok` 置为 true 且 `error` 字段为空。
3. IF 某阶段执行失败, THEN THE 编排器 SHALL 将该 StageTrace 的 `ok` 置为 false，并在 `error` 字段记录不含敏感值的失败原因描述。
4. WHEN 流水线执行结束, THE 编排器 SHALL 在 PipelineResult 中返回本次执行的全部 StageTrace 及实际选用的 `stage_strategies`。
5. THE 编排器 SHALL 为每个 PipelineResult 关联唯一的 `request_id`，以便通过该 `request_id` 检索到对应的全部 StageTrace 与 `stage_strategies`。
6. WHEN 系统写入日志, THE 系统 SHALL 不在日志中记录任何用户查询数据的字段值或行内容。
7. WHEN 系统写入日志, THE 系统 SHALL 不在日志中记录流水线最终结果集的数据内容。

### Requirement 11: 错误处理与结果状态映射

**User Story:** 作为调用方，我希望 PipelineResult 的状态能准确反映实际产出，以便上层据此决定后续处理。

#### Acceptance Criteria

1. WHERE 存在有效的 `final_sql` 或 `selected_sql`（其 `sql` 为去除首尾空白后的非空字符串）, THE 编排器 SHALL 将 `PipelineResult.status` 置为 `OK` 并在 `unvalidated_sql` 中输出对应 SQL。
2. WHERE `final_sql` 与 `selected_sql` 同时存在, THE 编排器 SHALL 优先采用 `final_sql` 作为输出的未校验 SQL。
3. IF 不存在任何候选 SQL（`sql_candidates` 为空或其成员 `sql` 均为去除首尾空白后的空字符串）, THEN THE 编排器 SHALL 将 `status` 置为 `NO_SQL`。
4. IF Query 修复用尽重试（`fix_attempts` 达到 `max_attempts`，其默认值为 `3`）仍未产出 `final_sql`, THEN THE 编排器 SHALL 将 `status` 置为 `FIXER_EXHAUSTED`。
5. IF 某阶段抛出未预期异常, THEN THE 编排器 SHALL 记录一条 `ok=False` 的 StageTrace。
6. IF 某阶段抛出未预期异常, THEN THE 编排器 SHALL 终止流水线的后续阶段。
7. IF 某阶段抛出未预期异常, THEN THE 编排器 SHALL 将 `status` 置为 `ERROR`。

### Requirement 12: 复用既有抽象与许可约束

**User Story:** 作为架构负责人，我希望 Layer 4 复用系统既有能力抽象，并遵守开源许可约束，以降低落地成本并规避许可风险。

#### Acceptance Criteria

1. THE 流水线 SHALL 通过既有抽象 VectorStore（OpenSearch）、Embedder（Bedrock/Cohere）、LlmClient（Bedrock）、DatasourceAdapter（StarRocks）接入外部能力。
2. THE DatasourceAdapter SHALL 提供 `dialect` 能力以标识目标数据源方言。
3. THE DatasourceAdapter SHALL 提供 `dry_run` 能力以对 SQL 进行只读探测执行。
4. THE DatasourceAdapter SHALL 提供 `get_schema_snippet` 能力以返回指定表的 schema 片段。
5. WHEN 通过 `dry_run` 执行探测, THE DatasourceAdapter SHALL 强制只读，并拒绝任何包含写入操作（非 SELECT）的 SQL。
6. THE DatasourceAdapter SHALL 经 Layer 6 连接池路由探测执行，而不直连数据源。
7. IF 探测执行失败或超时, THEN THE DatasourceAdapter SHALL 返回指示探测失败的错误，并释放本次探测占用的连接资源。
8. THE 实现 SHALL 不包含任何从 SQLBot（modified-GPLv3）复制的源码，仅借鉴其分阶段与召回思路。
9. WHERE 未来接入 MDL 策略, THE 系统 SHALL 以 WrenAI（Apache-2.0）为来源，并将其实现为一个可插拔的上下文获取策略。

---

## 附录：设计正确性属性 ↔ 需求 映射（Traceability）

用于将 `design.md` 的 8 条 Correctness Properties 与本文档需求对齐，以便设计中的 “Validates: Requirements X.Y” 引用可被接线。

| 设计属性 | 属性含义 | 对应需求验收标准 |
|----------|----------|------------------|
| Property 1：边界不越权 | 产出 SQL 绝不含权限过滤条件 | 7.1, 7.2, 4.6 |
| Property 2：输入不可变 | 阶段执行后 `input` 不变 | 8.1 |
| Property 3：阶段隔离写入 | 各阶段只写自己的产出字段 | 8.2, 8.3, 8.4 |
| Property 4：顺序不变式 | 固定顺序执行，前置缺失即报错 | 1.2, 1.3, 1.5 |
| Property 5：修复有界 | 修复次数不超过上限 | 6.1 |
| Property 6：探测不绕过隔离 | 试跑 SQL 必经 Layer 5 校验 | 6.2, 6.3, 6.4 |
| Property 7：策略可替换等价性 | 换策略不影响其他阶段契约与上下文结构 | 2.5, 2.6, 9.2 |
| Property 8：结果状态映射 | 状态与实际产出一致 | 11.1, 11.2, 11.3, 11.4, 11.7 |
