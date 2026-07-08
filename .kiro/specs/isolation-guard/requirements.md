# Requirements Document

## Introduction

本文档为智能问数系统 v2 的 **Layer 5 —— isolation-guard（隔离守卫 · 安全红线）** 的需求规约，需求由已批准的设计文档 `.kiro/specs/isolation-guard/design.md` 反向派生而来，并与架构文档 `docs/smart-query-architecture-v2.md`、上游契约 `.kiro/specs/text2sql-pipeline/design.md`（Layer 4）保持一致。

isolation-guard 的职责是：接收一条**未经安全校验的 SQL**（来自 Layer 4 流水线产出，或来自 Layer 3 语义缓存命中）连同**调用方身份**，以**确定性、硬编码、不可绕过**的方式完成权限解析、隔离注入与只读校验，最终产出**已注入数据隔离且通过只读校验的安全 SQL**，否则**拒绝**。本层不依赖 LLM、不依赖向量库，全程在代码层完成。

所有验收标准遵循 EARS 句式（Ubiquitous / Event-driven / State-driven / Unwanted-event / Optional），每条标准均为原子、可独立测试的陈述。文档末尾提供追溯附录，将设计文档 §Correctness Properties 中的 10 条正确性属性映射到覆盖它们的验收标准，供设计文档中的「Validates: Requirements X.Y」引用对齐。

## Glossary

- **isolation-guard 系统**：Layer 5 整体安全红线子系统；其输出要么是「已隔离 + 只读校验通过的安全 SQL」，要么是「明确的拒绝」，不存在中间态。
- **IsolationGuard**：Layer 5 门面（唯一生产实现）。每请求构造并绑定 `CallerIdentity`，编排三步子流程，对外暴露 `guard(...)` 与 `validate_for_probe(...)` 两个入口。
- **_apply**：IsolationGuard 内部的共享处理内核，两个入口均调用它完成只读校验与隔离注入。
- **PermissionResolver**：权限解析器组件。把可信调用方身份解析为 `DataScope`，抽象阳光云 OAuth / 权限系统，可 mock。不接触 SQL。
- **ReadOnlyValidator**：只读校验器组件。将 SQL 解析为 AST，强制单语句、顶层为查询、树内无写/DDL/命令节点。
- **IsolationInjector**：隔离注入器组件。基于 sqlglot 对查询 AST 做确定性改写，把受控物理表包裹为带隔离谓词的子查询。
- **TablePolicy**：表策略配置。定义受控物理表→隔离列映射、非受控白名单，以及未知表处理策略（默认 fail-closed）。
- **IsolationObserver**：可观测/审计观察者接口，接收 `IsolationDecision` 事件。
- **CallerIdentity**：可信调用方身份，由 Layer 2 认证（阳光云 OAuth）透传。
- **DataScope**：调用方的数据范围，含"可见 ps_id 源"与"可见 org_id 源"两个维度；每个源可为物化 id 集合或权限子查询源（ScopeSource）。
- **scope id**：DataScope 中的单个隔离标识（数值型或字符串型）。
- **ScopeResolver**：PermissionResolver 在本项目的实现，查询阳光云权限表（sys_user_ps_share / sys_user_org / sys_org_all_sub_org / power_station_org / power_station_public_share，与业务数据同库可 join）解析出当前用户可见的 ps_id 与 org_id 范围；只解析、不接触业务 SQL；结果可缓存（per-user，短 TTL）。
- **ScopeSource**：scope 维度的注入源，二选一——SubqueryScopeSource（默认，把权限查询作为子查询嵌入隔离谓词）或 MaterializedScopeSource（退回，物化为 id 字面量列表）。
- **ColumnBinding**：受控表的隔离列绑定，含 dimension（PS / ORG）、column（物理列名）、transform（IDENTITY 或 PS_ID_FROM_PS_KEY）。
- **PS_ID_FROM_PS_KEY**：一种列变换，对只有 ps_key 列的表，用 `split_part(ps_key,'_',1)` 取前缀段还原 ps_id 参与隔离（依据 ps_key 以 ps_id 为前缀的业务事实）。
- **PermissionFilterSpec（占位符）**：授权行过滤谓词的可配置规格，对应权限表标志位（is_active / share_type / is_public_share / is_under_share / is_channel_partner 等）的取舍规则；本期规则**未确定**，以占位形式存在（TODO），待业务给出后填入，不采用臆造默认。
- **受控物理表**：在 TablePolicy 中映射了隔离列的物理表，必须被注入隔离谓词。
- **非受控表**：在 TablePolicy 非受控白名单中的表（如维表/字典表），原样透传。
- **必控未映射表**：既不在受控映射也不在非受控白名单中的表；默认策略下判定为该类并拒绝。
- **CTE 名集合**：查询中所有 CTE 定义的别名集合；这些名字在 AST 中虽表现为表节点，但不是物理表，绝不包裹。
- **隔离谓词**：注入到受控表子查询中的 `<隔离列> IN (...)` 过滤条件。
- **恒假谓词 / deny-all**：空 scope 时注入的 `WHERE FALSE` 过滤条件，使受控表不返回任何行。
- **隔离前 SQL**：尚未注入隔离的 SQL（Layer 3 缓存中存储的即为此形态）。
- **安全 SQL**：已注入隔离且通过只读校验、可交付下游执行的 SQL 字符串。
- **GuardResult**：`guard(...)` 返回的结构化结果，含状态、安全 SQL、审计计数与拒绝信息。
- **IsolationDecision**：每次守卫决策产出的审计事件。
- **方言（dialect）**：SQL 方言字符串，由 `DatasourceAdapter.dialect` 注入（当前为 `starrocks`）。
- **IsolationGuardError**：本层所有自定义异常的基类（含 `ReadOnlyViolation`、`UnmappedControlledTableError`、`PermissionResolutionError`、`ConfigurationError`）。

## Requirements

### Requirement 1: 权限解析（PermissionResolver）

**User Story:** 作为安全平台工程师，我希望由可信身份确定性地解析出数据范围，以便隔离注入始终使用当前调用方的授权范围，并在身份不可解析时安全失败。

#### Acceptance Criteria

1. WHEN PermissionResolver（ScopeResolver）收到一个可信 CallerIdentity，THE PermissionResolver SHALL 返回一个包含"可见 ps_id 源"与"可见 org_id 源"两个维度的 DataScope（每个源为物化 id 集合或权限子查询源）。
2. IF 身份因 token 失效而无法解析，THEN THE PermissionResolver SHALL 抛出 PermissionResolutionError。
3. IF 权限系统不可达，THEN THE PermissionResolver SHALL 抛出 PermissionResolutionError。
4. WHEN PermissionResolver 抛出 PermissionResolutionError，THE IsolationGuard SHALL 在解析或改写任何 SQL 之前拒绝该请求。
5. WHEN PermissionResolver 返回的 DataScope 的 org_ids 与 ps_ids 均为空集合，THE IsolationGuard SHALL 将该空 DataScope 视为合法结果而非错误。
6. THE PermissionResolver SHALL 不读取、解析或改写任何 SQL 文本。
7. WHEN 同一请求内多次需要 DataScope，THE IsolationGuard SHALL 复用首次解析并缓存的同一 DataScope。

### Requirement 2: 只读强制校验（ReadOnlyValidator）

**User Story:** 作为安全平台工程师，我希望在代码层强制只读，以便任何写操作、DDL、命令或多语句在触达数据库之前即被拒绝。

#### Acceptance Criteria

1. WHEN ReadOnlyValidator 收到 SQL 文本，THE ReadOnlyValidator SHALL 将其解析为语句列表并强制其非空语句数量恰为 1。
2. IF 解析后的非空语句数量不等于 1，THEN THE ReadOnlyValidator SHALL 抛出 ReadOnlyViolation。
3. IF SQL 文本为空或仅包含空白字符，THEN THE ReadOnlyValidator SHALL 抛出 ReadOnlyViolation。
4. IF SQL 文本因语法非法而解析失败，THEN THE ReadOnlyValidator SHALL 抛出 ReadOnlyViolation。
5. IF 语句顶层节点不是 Select 也不是 Union，THEN THE ReadOnlyValidator SHALL 抛出 ReadOnlyViolation。
6. IF 语句 AST 的任意位置包含 Insert、Update、Delete、Drop、Create、Alter、TruncateTable、Command 或 Into 中的任一节点，THEN THE ReadOnlyValidator SHALL 抛出 ReadOnlyViolation。
7. WHEN 语句顶层为 Select 或 Union 且树内不含任何被禁节点，THE ReadOnlyValidator SHALL 正常返回并视为通过校验。
8. THE ReadOnlyValidator SHALL 在校验过程中不执行 SQL 且不连接数据库。
9. WHEN IsolationInjector 完成隔离注入，THE IsolationGuard SHALL 对注入后的 AST 再次执行只读校验作为复核。

### Requirement 3: 隔离注入（IsolationInjector · sqlglot 确定性 AST 改写）

**User Story:** 作为安全平台工程师，我希望以确定性 AST 改写将受控表包裹为带过滤子查询，以便隔离在任意查询结构下都局部、可组合且无歧义地生效。

#### Acceptance Criteria

1. WHEN IsolationInjector 在查询 AST 中遇到受控物理表引用，THE IsolationInjector SHALL 将该表替换为形如 `(SELECT * FROM T WHERE <隔离谓词>)` 的子查询。
2. WHEN IsolationInjector 包裹一个受控物理表，THE IsolationInjector SHALL 在其子查询中注入 `<隔离列> IN (...)` 隔离谓词。
3. WHEN 受控物理表引用带有别名，THE IsolationInjector SHALL 在生成的子查询上保留其原别名。
4. WHEN 受控物理表引用没有别名，THE IsolationInjector SHALL 使用原表名作为子查询别名。
5. WHEN IsolationInjector 遇到非受控表，THE IsolationInjector SHALL 保持该表节点原样透传。
6. WHEN 一个表名属于当前查询的 CTE 名集合，THE IsolationInjector SHALL 将其视为 CTE 引用并保持原样而不进行包裹。
7. WHERE 查询包含 JOIN、自 JOIN、子查询、CTE 或 UNION 结构，THE IsolationInjector SHALL 对其中每一个受控物理表引用各自独立包裹。
8. THE IsolationInjector SHALL 不改变非 Table 节点的语义结构，包括投影列、聚合、连接条件、分组与排序。
9. THE IsolationInjector SHALL 不调用 LLM 且不访问数据库。

### Requirement 4: 表策略配置与 fail-closed 分类（TablePolicy）

**User Story:** 作为安全平台工程师，我希望通过可维护配置定义受控表与隔离列，并对未映射的必控表默认拒绝，以便新表在未登记前不会被放行。

#### Acceptance Criteria

1. WHEN TablePolicy 对一个位于受控映射中的表名分类，THE TablePolicy SHALL 返回 kind 为 controlled 并携带对应隔离列名。
2. WHEN TablePolicy 对一个位于非受控白名单中的表名分类，THE TablePolicy SHALL 返回 kind 为 uncontrolled。
3. IF 一个表既不在受控映射也不在非受控白名单中，且 unknown_table_policy 为 FAIL_CLOSED，THEN THE TablePolicy SHALL 返回 kind 为 must_control_unmapped。
4. IF IsolationInjector 遇到分类为 must_control_unmapped 的表，THEN THE IsolationInjector SHALL 抛出 UnmappedControlledTableError。
5. WHERE unknown_table_policy 为 PASS_THROUGH，THE TablePolicy SHALL 将未知表分类为 uncontrolled。
6. WHERE TablePolicy 的 case_insensitive 为真，THE TablePolicy SHALL 在表名匹配时忽略大小写。

### Requirement 5: 空 scope 语义为 deny-all

**User Story:** 作为安全平台工程师，我希望在调用方对某维度无授权时返回零行而非报错，以便空授权成为可预期的正常业务态。

#### Acceptance Criteria

1. WHEN 某受控表对应隔离列的 scope id 集合为空，THE IsolationInjector SHALL 将该表包裹为使用恒假谓词（WHERE FALSE）的子查询。
2. THE IsolationInjector SHALL 不使用 `IN (NULL)` 形式表达 deny-all。
3. WHEN 因空 scope 注入了恒假谓词，THE IsolationGuard SHALL 在 GuardResult 中将 deny_all 标记为 True。
4. WHEN 空 scope 触发 deny-all，THE IsolationGuard SHALL 返回状态为 ALLOWED 的结果而非抛出异常。

### Requirement 6: 注入安全（类型化字面量渲染，防注入）

**User Story:** 作为安全平台工程师，我希望所有 scope id 以类型化字面量渲染而非字符串拼接，以便任意 id 取值都无法改变 SQL 结构。

#### Acceptance Criteria

1. WHEN IsolationInjector 渲染任一 scope id，THE IsolationInjector SHALL 使用类型化 sqlglot 字面量节点，数值 id 渲染为 number 字面量，字符串 id 渲染为 string 字面量。
2. THE IsolationInjector SHALL 不通过字符串插值构造隔离谓词。
3. WHERE 某 scope id 为字符串且包含引号、分号等特殊字符，THE IsolationInjector SHALL 使生成 SQL 的语法结构保持不变而仅字面量取值不同。
4. IF 某 scope id 为布尔值，THEN THE IsolationInjector SHALL 抛出 ConfigurationError。

### Requirement 7: 双入口同源与不可绕过

**User Story:** 作为安全平台工程师，我希望所有入口共享同一处理内核，以便不存在任何返回未校验或未注入 SQL 的绕过路径。

#### Acceptance Criteria

1. WHEN 调用方通过 `guard(sql, request_id)` 入口，THE IsolationGuard SHALL 通过共享内核 _apply 完成只读校验与隔离注入。
2. WHEN 调用方通过 `validate_for_probe(sql, request_id)` 入口，THE IsolationGuard SHALL 通过同一共享内核 _apply 完成只读校验与隔离注入。
3. WHEN 相同的 (sql, identity) 分别经 guard 与 validate_for_probe 处理且均放行，THE IsolationGuard SHALL 产出逐字符一致的安全 SQL。
4. THE IsolationGuard SHALL 不提供任何返回未经只读校验或未经隔离注入 SQL 的出口路径。

### Requirement 8: 缓存命中按当前用户重注隔离

**User Story:** 作为安全平台工程师，我希望缓存命中路径按当前用户重注隔离且从不缓存成品 SQL，以便杜绝跨用户越权泄露。

#### Acceptance Criteria

1. WHEN 缓存命中路径调用 `guard(pre_isolation_sql, request_id)`，THE IsolationGuard SHALL 使用当前 CallerIdentity 解析出的 DataScope 注入隔离谓词。
2. THE IsolationGuard SHALL 不缓存或持久化注入后的成品 SQL。
3. WHEN 同一隔离前 SQL 先后由两个 DataScope 不同的调用方经过守卫，THE IsolationGuard SHALL 为各调用方分别产出使用其各自 DataScope 的隔离谓词。

### Requirement 9: validate_for_probe 契约（供 Layer 4 探测）

**User Story:** 作为 Layer 4 Query Fixer，我希望在探测执行前获得已隔离且只读校验通过的 SQL，以便「探测所见即执行所得」，并在不合规时感知异常。

#### Acceptance Criteria

1. WHEN Layer 4 Query Fixer 调用 `validate_for_probe(sql, request_id)` 且该 SQL 合规，THE IsolationGuard SHALL 返回一条已注入隔离且通过只读校验的安全 SQL 字符串。
2. IF `validate_for_probe` 收到的 SQL 不合规，THEN THE IsolationGuard SHALL 抛出 IsolationGuardError 的子类异常。
3. THE IsolationGuard SHALL 允许在不再次传入 identity 的情况下调用 `validate_for_probe`，因为身份已在门面构造时绑定。

### Requirement 10: 确定性

**User Story:** 作为安全平台工程师，我希望守卫输出对相同输入完全可复现，以便隔离结果可审计、可测试、可回归。

#### Acceptance Criteria

1. WHEN 使用相同的 (sql, scope, policy, dialect) 多次执行守卫，THE IsolationGuard SHALL 产出逐字符相同的输出。
2. WHEN IsolationInjector 渲染隔离谓词的 IN 列表，THE IsolationInjector SHALL 对 scope ids 使用稳定排序以保证确定性输出。
3. THE IsolationGuard SHALL 在整个守卫过程中不调用 LLM、不依赖随机性且不依赖外部可变状态。

### Requirement 11: 方言可插拔

**User Story:** 作为平台工程师，我希望方言由数据源适配层注入，以便切换数据源时隔离与校验逻辑无需改动。

#### Acceptance Criteria

1. WHEN 构造 IsolationGuard，THE IsolationGuard SHALL 从 `DatasourceAdapter.dialect` 接收方言字符串，当前为 `starrocks`。
2. WHEN 注入的方言字符串发生变化，THE IsolationGuard SHALL 保持隔离注入与只读校验逻辑不变。
3. WHEN IsolationGuard 解析与序列化 AST，THE IsolationGuard SHALL 使用被注入的方言。

### Requirement 12: 错误处理与 fail-closed 兜底

**User Story:** 作为安全平台工程师，我希望所有失败路径统一为拒绝，以便任何无法证明安全的情况都不会放行。

#### Acceptance Criteria

1. THE IsolationGuard SHALL 使本层定义的所有自定义异常类型继承自 IsolationGuardError。
2. WHEN _apply 抛出任一 IsolationGuardError 子类，THE IsolationGuard SHALL 在 `guard()` 入口将其转换为 status 为 REJECTED 的结构化 GuardResult 并记录对应 rejection_kind。
3. IF `guard()` 处理过程中发生任意未预期异常，THEN THE IsolationGuard SHALL 将该请求按拒绝处理并将 rejection_kind 记为 UnexpectedError。
4. WHEN _apply 抛出异常且调用方为 `validate_for_probe()`，THE IsolationGuard SHALL 保持异常向上抛出的语义。
5. THE IsolationGuard SHALL 不吞掉 _apply 内发生的异常。

### Requirement 13: 可观测与审计

**User Story:** 作为安全审计人员，我希望每次隔离决策都有脱敏留痕，以便事后验证隔离生效而不泄露敏感信息。

#### Acceptance Criteria

1. WHEN IsolationGuard 完成一次守卫决策，THE IsolationGuard SHALL 通过 IsolationObserver 产出一个 IsolationDecision 事件。
2. THE IsolationDecision SHALL 包含 request_id、user_id、status、tables_seen、tables_injected、scope_org_count、scope_ps_count、deny_all、dialect、rejection_kind 与 timestamp 字段。
3. THE IsolationDecision SHALL 仅记录 scope 的计数而不记录任何具体 scope id。
4. WHEN IsolationGuard 拒绝一个请求，THE IsolationGuard SHALL 在对外拒绝信息中不回显具体 scope ids 或敏感 SQL 明文。
5. WHEN IsolationGuard 拒绝一个请求，THE IsolationGuard SHALL 对外返回稳定的错误类别与 request_id。

### Requirement 14: 边界与第二道防线

**User Story:** 作为系统架构师，我希望明确 Layer 5 的边界，以便只读强制拥有数据库层的第二道防线，且本层不越权持有连接。

#### Acceptance Criteria

1. THE IsolationGuard SHALL 不持有数据库连接，仅产出安全 SQL 字符串交由下游 Layer 6 / Layer 7 执行。
2. THE isolation-guard 系统 SHALL 依赖 Layer 7 的数据库只读账号作为只读强制的第二道防线。

### Requirement 15: 阳光云权限表 scope 解析（ScopeResolver）

**User Story:** 作为安全平台工程师，我希望由查询阳光云权限表来解析当前用户可见的电站与组织范围，以便隔离基于真实授权关系而非静态假设。

#### Acceptance Criteria

1. WHEN ScopeResolver 解析可见 ps_id 集合，THE ScopeResolver SHALL 取"直接分享（sys_user_ps_share）"、"组织下辖电站（sys_user_org → sys_org_all_sub_org → power_station_org）"与"公开分享（power_station_public_share）"的并集。
2. WHEN ScopeResolver 展开组织下辖电站，THE ScopeResolver SHALL 使用 sys_org_all_sub_org 闭包表纳入用户所属组织的全部下级组织。
3. WHEN ScopeResolver 解析可见 org_id 集合，THE ScopeResolver SHALL 返回用户所属组织经 sys_org_all_sub_org 展开后的组织集合。
4. THE ScopeResolver SHALL 仅查询权限表、不读取或改写业务查询 SQL。
5. WHERE 权限表与业务数据表位于同一可 join 的数据库，THE ScopeResolver SHALL 允许以子查询源（SubqueryScopeSource）形式提供 scope 而不物化 id 列表。
6. WHEN 同一请求内多次需要 scope，THE ScopeResolver 的解析结果 SHALL 在该请求内保持一致（复用缓存）。

### Requirement 16: ps_key 前缀隔离（PS_ID_FROM_PS_KEY）

**User Story:** 作为安全平台工程师，我希望对只有 ps_key 列的表用前缀反解出 ps_id 做隔离，以便无 ps_id 列的表也能被电站维度隔离。

#### Acceptance Criteria

1. WHEN 受控表的 ColumnBinding 的 transform 为 PS_ID_FROM_PS_KEY，THE IsolationInjector SHALL 以 `split_part(ps_key,'_',1)` 取前缀段作为该表的 ps_id 隔离键。
2. WHEN 生成 ps_key 前缀隔离谓词，THE IsolationInjector SHALL 将前缀段 CAST 为与 ps_id 一致的类型后再与可见 ps_id 源比较。
3. THE IsolationInjector SHALL 对 transform 为 IDENTITY 的 ps_id 列直接使用列值比较，不做前缀提取。

### Requirement 17: 隔离注入源策略（子查询默认 · 物化退回）

**User Story:** 作为安全平台工程师，我希望隔离注入默认使用权限子查询半连接，以便在可见集合很大时仍保持 SQL 紧凑且权限新鲜。

#### Acceptance Criteria

1. WHERE scope 以 SubqueryScopeSource 提供，THE IsolationInjector SHALL 将隔离谓词右侧注入为权限子查询（`col IN (<权限子查询>)`）。
2. WHERE scope 以 MaterializedScopeSource 提供，THE IsolationInjector SHALL 将隔离谓词右侧注入为类型化字面量列表（沿用 Requirement 6）。
3. WHEN 采用子查询源且权限子查询结果为空，THE 注入结果 SHALL 使受控表不返回任何行（deny-all 自动成立），且 IsolationGuard SHALL 将 deny_all 标记为 True。
4. WHEN 采用子查询源，THE IsolationInjector SHALL 对相同 (sql, user_id, policy, dialect) 产出逐字符相同的输出（子查询模板固定 + user_id 类型化字面量）。

### Requirement 18: 授权行过滤规则占位（PermissionFilterSpec）

**User Story:** 作为安全平台工程师，我希望权限标志位的过滤规则以可配置占位形式存在，以便规则未定时不臆造默认、规则确定后可无缝填入。

#### Acceptance Criteria

1. THE ScopeResolver SHALL 将权限表标志位（is_active / share_type / is_public_share / is_under_share / is_channel_partner 等）的过滤规则实现为可配置的 PermissionFilterSpec，而非硬编码。
2. WHILE PermissionFilterSpec 规则尚未由业务确定，THE ScopeResolver SHALL 以占位（TODO）形式保留过滤条件，且 SHALL NOT 采用默认放行全部授权行的行为。
3. WHEN 业务提供了标志位过滤规则，THE ScopeResolver SHALL 支持在不改动隔离注入与只读校验逻辑的前提下填入该规则。

## Appendix A: 正确性属性追溯（Correctness Properties Traceability）

下表将设计文档 `design.md` §Correctness Properties 中的 10 条正确性属性映射到本文档中覆盖它们的验收标准，供设计文档「Validates: Requirements X.Y」引用对齐。

| 设计属性 | 属性标题 | 覆盖的验收标准 |
|---------|---------|---------------|
| Property 1 | 确定性（Determinism） | 10.1, 10.2, 10.3, 7.3 |
| Property 2 | 受控表必被注入（Controlled Tables Always Injected） | 3.1, 3.2, 3.7 |
| Property 3 | 不可绕过（Non-Bypassable） | 7.1, 7.2, 7.4, 2.9 |
| Property 4 | 只读强制（Read-Only Enforced） | 2.5, 2.6, 2.7, 2.9 |
| Property 5 | 空 scope → 零行（Empty Scope Denies All） | 5.1, 5.2, 5.3, 5.4 |
| Property 6 | 缓存命中按当前用户重注（Re-inject Per Current User） | 8.1, 8.2, 8.3 |
| Property 7 | 语义受限等价（Semantically Restricted Output） | 3.1, 3.5, 3.8 |
| Property 8 | fail-closed（Fail Closed on Unmapped） | 4.3, 4.4, 12.3 |
| Property 9 | 注入安全（Injection-Safe Rendering） | 6.1, 6.2, 6.3, 6.4 |
| Property 10 | 身份不可解析即拒绝（Deny on Unresolvable Identity） | 1.2, 1.3, 1.4, 1.6 |
