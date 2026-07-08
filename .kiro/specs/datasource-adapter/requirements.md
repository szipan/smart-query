# Requirements Document

## Introduction

本文档为智能问数系统 v2 的 **Layer 7 —— datasource-adapter（可插拔数据源适配层 · StarRocks 只读）** 的需求规约，需求由已批准的设计文档 `.kiro/specs/datasource-adapter/design.md` 反向派生而来，并与其正文、数据模型（`DatasourceAdapter` / `ResultSet` / `ResultMeta` / `ResultLimits` / `AdapterEndpoint` / `AdapterConfig` / `AdapterError` 家族）、数据流图（§2.3 三入口数据流、§2.4 execute 时序、§2.5 dry_run 探测时序）以及 §Correctness Properties 中的 10 条正确性属性保持一致，同时与架构文档 `docs/smart-query-architecture-v2.md`、上游契约 `.kiro/specs/query-engine/design.md`（Layer 3 §3.5）、`.kiro/specs/text2sql-pipeline/design.md`（Layer 4）、`.kiro/specs/isolation-guard/design.md`（Layer 5）与下游契约 `.kiro/specs/connection-proxy/design.md`（Layer 6）对齐。

Layer 7 是智能问数系统**最下游的物理执行层**，是一个运行在 EKS 上的 Python 适配层。它的唯一使命是：把上游（Layer 3 执行安全 SQL、Layer 4 只读探测 / Schema Linking）交下来的、**已由 Layer 5 校验通过的安全只读 SQL**，通过标准 MySQL 驱动（PyMySQL）经 Layer 6（ProxySQL frontend `:6033`）送达 StarRocks **只读端点**，并把方言差异、结果整形、schema 读取、错误归一化**封装在实现类内部**，向上层只暴露一个稳定的**抽象契约**。本层定义一个 `DatasourceAdapter` Protocol 与一个按 `datasource_id` 取实现的注册表 / 工厂；当前唯一实现是 **StarRocks（只读）**，但抽象为"新增源只加实现类、不改上层"预留扩展点（预留 MySQL / Spark，当前不实现）。

本层还是 **`ResultSet` 数据模型的 owner**：此前 Layer 3/4/5 各层均以 `"ResultSet"` 前向引用，其字段语义（列名 / 行 / 行数 / 截断标志 / 元数据）的权威定义在本层。

本层边界必须被严格遵守：**只执行不校验、不改写、不放宽、不自建只读 AST 校验**（只读校验是 Layer 5 的确定性职责，本层假定 SQL 已安全）；**不持有连接池、不做健康检查、不限流**（那是 Layer 6）。**StarRocks 只读账号是第二 / 第三道防线**——它与 Layer 5 的确定性只读 AST 校验、Layer 6 的只读路由三道彼此独立，即便前两道有漏，DB 只读账号最终仍拒写。本层只在 Layer 6 提供的连接之上做**方言连接 + 执行 + 结果整形 + 错误归一化**，并对结果施加**最大行数 / 字节上限**（`max_rows=10000`、`max_bytes=32MB`），超限则截断并在 `ResultSet` 元数据标记（这是"结果规模保护"，区别于 Layer 6 的"连接并发保护"）。

所有验收标准遵循 EARS 句式（Ubiquitous / Event-driven / State-driven / Unwanted-event / Optional / Complex），每条标准均为原子、可独立测试的陈述。文档末尾提供追溯附录，将设计文档 §Correctness Properties 中的 10 条正确性属性映射到覆盖它们的验收标准。

## Glossary

- **Datasource_Adapter（Protocol）**：Layer 7 对外稳定抽象 `DatasourceAdapter`，上层唯一依赖面；暴露 `dialect` / `execute(safe_sql) -> ResultSet` / `dry_run(sql) -> None` / `get_schema_snippet(table_names) -> list[str]` 四个成员。当前唯一实现为 StarRocks（只读）。
- **Adapter_Registry / 工厂**：按 `datasource_id` 解析到唯一实现实例的注册表 / 工厂（`AdapterRegistry` / `DefaultAdapterRegistry`），暴露 `register(datasource_id, adapter)` 与 `get_adapter(datasource_id)`；当前只注册 `"starrocks" → StarRocksAdapter`。
- **StarRocks_Adapter**：`DatasourceAdapter` 的当前唯一实现（`StarRocksAdapter`），只读；内部封装方言连接、结果整形、schema 读取、错误归一化，`dialect == "starrocks"`。
- **ResultSet**：数据源执行结果集，本层为其 owner；含 `columns`（列名）/ `rows`（数据行）/ `row_count`（实际行数）/ `truncated`（是否截断）/ `meta`（`ResultMeta`）。承载于 Layer 3 `Answer.rows`。
- **ResultMeta**：`ResultSet` 元数据（可观测，不含任何凭证 / 明文口令）；含 `datasource_id` / `dialect` / `truncated` / `truncate_reason` / `applied_max_rows` / `applied_max_bytes` / `approx_bytes` / `elapsed_ms`。
- **ResultLimits（max_rows / max_bytes）**：结果规模保护配置；`max_rows`（默认 10000）为最大行数上限，`max_bytes`（默认 32MB = 32×1024×1024）为最大近似字节上限。
- **AdapterEndpoint（:6033）**：本层连 Layer 6 frontend 的**数据面**端点契约；`port` 默认 `6033`（数据面），`username` 默认只读账号 `sq_ro`，`connect_timeout_ms` 默认 3000；口令不入结构，经 `password_secret_ref` 引用由 `Secret_Resolver` 注入；**绝不使用 Admin `:6032`**。
- **Connection_Provider**：连接提供者契约（`ConnectionProvider` / `ProxyConnectionProvider`），封装"如何连 Layer 6 `:6033`"；暴露 `acquire()` / `release(conn)`；不自建连接池、不健康检查、不限流。
- **Result_Shaper**：结果整形器契约（`ResultShaper`），把驱动游标整形为 `ResultSet`，并施加最大行数 / 字节上限、超限截断并标记 `truncate_reason`。
- **Schema_Reader**：schema 读取器契约（`SchemaReader`），`get_schema_snippet` 内部委派，读取指定表的 schema 片段字符串列表。
- **Error_Normalizer**：错误归一化器契约（`ErrorNormalizer` / `DefaultErrorNormalizer`），把底层驱动 / 网络 / 超时 / 语法异常归一化为 `AdapterError` 家族；剥离驱动栈与凭证。
- **Secret_Resolver**：密钥解析契约（`SecretResolver`），经 Secrets Manager / AgentCore Identity + IRSA 将只读账号凭证引用解析为运行期值；解析后的明文绝不写入日志 / 指标 / 错误信息 / 元数据。
- **AdapterError 家族**：本层归一化后的统一错误类型；基类 `AdapterError`，子类含 `UnknownDatasourceError`（未注册 datasource_id）/ `ConnectionError`（经 Layer 6 取连接失败）/ `QueryExecutionError`（数据源返回语法 / 权限 / 运行期错误，含只读账号拒写）/ `QueryTimeoutError`（执行 / 探测超时）/ `SchemaReadError`（schema 读取失败）。
- **dialect**：数据源方言标识字段，当前实现恒为 `"starrocks"`；供 Layer 5 只读校验 / 注入读取（Layer 5 只读取此字段）。
- **safe_sql**：来自 Layer 3、已由 Layer 5 校验通过的安全只读 SQL 文本，交由 `execute` 逐字下发。
- **dry_run**：Layer 4 Query Fixer 的只读探测入口 `dry_run(sql) -> None`；探测 SQL 同样已过 Layer 5；无副作用，成功返回 `None`、失败抛 `AdapterError`。
- **get_schema_snippet**：Layer 4 Schema Linking 取 schema 片段入口 `get_schema_snippet(table_names) -> list[str]`。
- **datasource_id**：数据源标识（如 `"starrocks"`），`Adapter_Registry` 据此解析实现实例。
- **只读账号**：StarRocks 只读账号（frontend `sq_ro`，映射只读 hostgroup），本层所有连接恒使用之；是三道独立只读防线的最终兜底。
- **IRSA**：IAM Roles for Service Accounts；EKS Pod 据此从 Secrets Manager / AgentCore Identity 获取只读账号凭证。
- **Layer6 ProxySQL（:6033 / :6032）**：下游连接代理层；`:6033` 为数据面 frontend（本层经此连接），`:6032` 为 Admin 配置面（本层绝不接触）；连接池 / 复用 / 限流 / 健康检查全归 Layer 6。
- **Layer5 IsolationGuard**：间接上游安全红线层；本层只读取 `dialect`；`execute` / `dry_run` 的入参都已过其校验，本层只执行不校验。
- **Layer3 QueryOrchestrator / Layer4 Pipeline**：上游调用方；Layer 3 调 `execute`，Layer 4 调 `dry_run` / `get_schema_snippet` 并读取 `dialect`。

## Requirements

### Requirement 1: DatasourceAdapter 稳定抽象契约（Stable Protocol Contract）

**User Story:** 作为上游调用方（Layer 3 / Layer 4），我希望只依赖一个稳定的 `DatasourceAdapter` 抽象而非某个具体数据库，以便在不改上层的前提下替换或新增数据源实现。

#### Acceptance Criteria

1. THE Datasource_Adapter SHALL 暴露一个只读方言标识字段 `dialect`（类型为字符串）。
2. THE Datasource_Adapter SHALL 暴露 `execute(safe_sql: str) -> ResultSet` 方法作为 Layer 3 的安全 SQL 执行入口。
3. THE Datasource_Adapter SHALL 暴露 `dry_run(sql: str) -> None` 方法作为 Layer 4 Query Fixer 的只读探测入口。
4. THE Datasource_Adapter SHALL 暴露 `get_schema_snippet(table_names: list[str]) -> list[str]` 方法作为 Layer 4 Schema Linking 的取 schema 片段入口。
5. THE Datasource_Adapter SHALL 使上层仅依赖该抽象，而不感知底层驱动（PyMySQL）与 StarRocks 方言细节。
6. THE Datasource_Adapter SHALL 不在其契约中暴露 SQL 生成、隔离注入或只读 AST 校验能力。

### Requirement 2: 按 datasource_id 取实现的注册表 / 工厂（Registry Resolution）

**User Story:** 作为上游调用方，我希望通过 `datasource_id` 从注册表拿到对应实现实例，以便按标识解析数据源而不在上层 `new` 具体实现。

#### Acceptance Criteria

1. THE Adapter_Registry SHALL 暴露 `register(datasource_id: str, adapter: DatasourceAdapter) -> None` 用于登记实现实例。
2. THE Adapter_Registry SHALL 暴露 `get_adapter(datasource_id: str) -> DatasourceAdapter` 用于按标识解析实现实例。
3. THE Adapter_Registry SHALL 在装配期注册 `"starrocks"` 到 `StarRocksAdapter` 实例。
4. WHEN `get_adapter` 收到一个已注册的 `datasource_id`，THE Adapter_Registry SHALL 返回该 `datasource_id` 对应的实现实例。
5. WHILE 某 `datasource_id` 已注册且未被重新注册，THE Adapter_Registry SHALL 对该 `datasource_id` 的多次 `get_adapter` 调用恒返回同一实现实例。
6. IF `get_adapter` 收到一个未注册的 `datasource_id`，THEN THE Adapter_Registry SHALL 抛出 `UnknownDatasourceError` 并不返回任何实现实例。

### Requirement 3: 只读账号执行（Read-Only Account for All Operations）

**User Story:** 作为安全平台工程师，我希望本层任何数据源操作都使用 StarRocks 只读账号，以便即便前两道防线（Layer 5 校验、Layer 6 只读路由）失效，数据库只读账号仍最终拒写。

#### Acceptance Criteria

1. WHEN THE StarRocks_Adapter 执行 `execute`，THE StarRocks_Adapter SHALL 使用 StarRocks 只读账号建立的连接下发 SQL。
2. WHEN THE StarRocks_Adapter 执行 `dry_run`，THE StarRocks_Adapter SHALL 使用 StarRocks 只读账号建立的连接进行探测。
3. WHEN THE StarRocks_Adapter 执行 `get_schema_snippet`，THE StarRocks_Adapter SHALL 使用 StarRocks 只读账号建立的连接读取 schema。
4. THE StarRocks_Adapter SHALL 不存在使用可写账号或可写端点的代码路径。
5. WHERE 上游经只读账号提交了写语句（前两道防线均被绕过），THE StarRocks_Adapter SHALL 依赖 StarRocks 只读账号在数据库侧拒写并将其归一化为 `QueryExecutionError`。

### Requirement 4: 只执行不改写不放宽（Execute As-Is, No Rewrite, No Relaxation）

**User Story:** 作为 Layer 5 与上游调用方，我希望本层逐字下发我交付的安全 SQL，以便"交付什么就在 StarRocks 上执行什么"，且安全校验权威唯一归属 Layer 5。

#### Acceptance Criteria

1. WHEN THE StarRocks_Adapter 执行 `execute(safe_sql)`，THE StarRocks_Adapter SHALL 向数据源下发与入参 `safe_sql` 逐字节相同的语句。
2. WHEN THE StarRocks_Adapter 执行 `dry_run(sql)`，THE StarRocks_Adapter SHALL 向数据源下发与入参 `sql` 逐字节相同的语句。
3. THE StarRocks_Adapter SHALL 不对下发的 SQL 进行改写、注入、放宽或语义变更。
4. THE StarRocks_Adapter SHALL 不对入参 SQL 施加只读 AST 校验、隔离注入或权限过滤。
5. THE StarRocks_Adapter SHALL 假定所收到的 SQL 已由 Layer 5 校验为安全只读 SQL。

### Requirement 5: 结果规模上限与截断标记（Result Bounded and Truncation Flagged）

**User Story:** 作为平台工程师，我希望执行结果受最大行数 / 字节上限约束并在超限时被截断标记，以便海量结果集不会撑爆内存或回包。

#### Acceptance Criteria

1. THE StarRocks_Adapter SHALL 依据 `ResultLimits` 施加最大行数上限 `max_rows`（默认 10000）与最大近似字节上限 `max_bytes`（默认 32×1024×1024 字节，即 32MB）。
2. WHEN THE StarRocks_Adapter 执行 `execute` 并逐行读取游标，THE Result_Shaper SHALL 边读边累计已读行数与近似字节数。
3. WHILE 已读行数达到 `max_rows`，THE Result_Shaper SHALL 停止读取、将 `ResultSet.truncated` 置为 True 并将 `ResultMeta.truncate_reason` 置为 `"max_rows"`。
4. WHILE 加入下一行会使累计近似字节数超过 `max_bytes`，THE Result_Shaper SHALL 不加入该行、停止读取、将 `ResultSet.truncated` 置为 True 并将 `ResultMeta.truncate_reason` 置为 `"max_bytes"`。
5. THE StarRocks_Adapter SHALL 使任意返回的 `ResultSet` 满足 `row_count ≤ max_rows` 且 `meta.approx_bytes ≤ max_bytes`。
6. THE StarRocks_Adapter SHALL 使 `ResultSet.truncated` 为 True 当且仅当触达 `max_rows` 或 `max_bytes` 任一上限。
7. WHEN 未触达任何上限，THE StarRocks_Adapter SHALL 使 `ResultSet.truncated` 为 False 且 `ResultMeta.truncate_reason` 为 None。
8. WHEN 结果因上限被截断，THE StarRocks_Adapter SHALL 返回正常的（截断的）`ResultSet` 而不抛出错误。
9. THE StarRocks_Adapter SHALL 使 `ResultSet.row_count` 等于 `ResultSet.rows` 的长度。

### Requirement 6: 所有已注册 adapter 满足同一 Protocol（Uniform Protocol Conformance）

**User Story:** 作为系统架构师，我希望注册表中每个实现都完整满足 `DatasourceAdapter` 契约，以便上层只依赖抽象即可无差别替换实现。

#### Acceptance Criteria

1. THE Adapter_Registry SHALL 使其登记的每一个实现都具备 `dialect` 字段与 `execute` / `dry_run` / `get_schema_snippet` 三个方法。
2. THE Adapter_Registry SHALL 使其登记的每一个实现的上述成员签名与 `DatasourceAdapter` Protocol 一致。
3. WHERE 未来向注册表新增任何实现，THE Adapter_Registry SHALL 要求该实现满足 `DatasourceAdapter` 的全部成员与签名后方可登记。

### Requirement 7: dialect 与真实源方言一致（Dialect Matches Real Source）

**User Story:** 作为 Layer 5 与 Layer 4，我希望 adapter 暴露的 `dialect` 与其底层真实数据源方言一致，以便据此正确进行只读校验与 SQL 生成。

#### Acceptance Criteria

1. THE StarRocks_Adapter SHALL 使其 `dialect` 字段值等于 `"starrocks"`。
2. THE StarRocks_Adapter SHALL 使其 `dialect` 与其实际连接的底层数据源方言（StarRocks）一致。
3. THE StarRocks_Adapter SHALL 使 `ResultMeta.dialect` 与自身 `dialect` 字段一致。

### Requirement 8: dry_run 无副作用（Dry Run Has No Side Effects）

**User Story:** 作为 Layer 4 Query Fixer，我希望 `dry_run` 只做只读探测而不改变数据源状态，以便安全地验证候选 SQL 是否可执行。

#### Acceptance Criteria

1. WHEN THE StarRocks_Adapter 执行 `dry_run(sql)`，THE StarRocks_Adapter SHALL 不改变数据源的任何持久状态。
2. WHEN `dry_run(sql)` 探测成功，THE StarRocks_Adapter SHALL 返回 `None`。
3. IF `dry_run(sql)` 探测失败，THEN THE StarRocks_Adapter SHALL 抛出 `AdapterError` 家族的错误。
4. THE StarRocks_Adapter SHALL 依据 `dry_run_timeout_ms`（默认 10000 毫秒）对探测施加超时上界。

### Requirement 9: 连接经 Layer 6、不直连 / 不自建池（Connect via Layer 6, No Direct/Own Pool）

**User Story:** 作为平台工程师，我希望本层所有连接都经 Layer 6 frontend `:6033`，以便连接池 / 复用 / 限流 / 健康检查职责唯一归属 Layer 6 而本层不重复承担。

#### Acceptance Criteria

1. THE Connection_Provider SHALL 使用标准 MySQL 驱动 PyMySQL 连接 Layer 6 ProxySQL frontend 数据面端口 `:6033`。
2. THE Connection_Provider SHALL 使 `AdapterEndpoint.port` 恒为 `6033`。
3. THE Connection_Provider SHALL 不连接 Layer 6 Admin 配置面端口 `:6032`。
4. THE Connection_Provider SHALL 不直连 StarRocks 后端端点。
5. THE Connection_Provider SHALL 不自建连接池、不做后端健康检查、不做限流。
6. WHEN THE StarRocks_Adapter 完成一次操作，THE Connection_Provider SHALL 经 `release(conn)` 将连接归还 Layer 6 而不由本层复用管理。
7. THE StarRocks_Adapter SHALL 依据 `execute_timeout_ms`（默认 60000 毫秒，不超过 Layer 6 `default_query_timeout`）对执行施加超时上界。

### Requirement 10: 错误归一化且不泄露栈 / 凭证（Errors Normalized, No Stack/Secret Leak）

**User Story:** 作为上游调用方与安全平台工程师，我希望本层向上抛出的错误都是统一的适配层错误且不含驱动栈或凭证，以便错误可被稳定处理且不因错误信息泄露敏感数据。

#### Acceptance Criteria

1. WHEN THE StarRocks_Adapter 捕获底层驱动 / 网络 / 超时 / 语法异常，THE Error_Normalizer SHALL 将其归一化为 `AdapterError` 家族的错误。
2. THE StarRocks_Adapter SHALL 使其向上抛出的任何异常均为 `AdapterError` 或其子类。
3. WHEN 底层异常为超时，THE Error_Normalizer SHALL 归一化为 `QueryTimeoutError`。
4. WHEN 无法经 Layer 6 取得连接，THE Error_Normalizer SHALL 归一化为 `ConnectionError`。
5. WHEN 数据源返回语法 / 权限 / 运行期错误（含只读账号拒写），THE Error_Normalizer SHALL 归一化为 `QueryExecutionError`。
6. IF `get_schema_snippet` 读取 schema 失败，THEN THE StarRocks_Adapter SHALL 抛出 `SchemaReadError`。
7. THE Error_Normalizer SHALL 使归一化后错误的消息不含底层驱动栈细节。
8. THE Error_Normalizer SHALL 使归一化后错误的消息不含任何凭证或明文口令。
9. THE StarRocks_Adapter SHALL 不静默吞掉任何错误。

### Requirement 11: 凭证不落明文日志（Secrets Never in Plaintext）

**User Story:** 作为安全平台工程师，我希望只读账号凭证只经 Secrets / Identity + IRSA 在运行期解析使用，以便凭证不因日志、指标或元数据而泄露。

#### Acceptance Criteria

1. THE AdapterEndpoint SHALL 仅保存凭证引用 `password_secret_ref` 而不保存明文口令。
2. WHEN THE Connection_Provider 建立连接，THE Secret_Resolver SHALL 在运行期经 Secrets Manager / AgentCore Identity + IRSA 将凭证引用解析为运行期值。
3. THE StarRocks_Adapter SHALL 不将解析后的明文口令写入任何日志。
4. THE StarRocks_Adapter SHALL 不将凭证或明文口令写入任何指标或可观测记录。
5. THE StarRocks_Adapter SHALL 不将凭证或明文口令写入任何错误信息。
6. THE StarRocks_Adapter SHALL 不将凭证或明文口令写入 `ResultSet` 或 `ResultMeta`。
7. THE StarRocks_Adapter SHALL 不将凭证写入容器镜像。

### Requirement 12: ResultSet 数据模型（Result Data Model · Owner）

**User Story:** 作为 `ResultSet` 的 owner，我希望本层给出结果集的权威字段定义，以便上游各层（此前仅前向引用）有一致的结果契约。

#### Acceptance Criteria

1. THE ResultSet SHALL 含 `columns`（列名，按查询列序）、`rows`（数据行，每行与 `columns` 对齐）、`row_count`（实际返回行数）、`truncated`（是否因上限被截断）、`meta`（`ResultMeta`）字段。
2. THE ResultSet SHALL 为不可变（frozen）结构。
3. THE ResultMeta SHALL 含 `datasource_id` / `dialect` / `truncated` / `truncate_reason` / `applied_max_rows` / `applied_max_bytes` / `approx_bytes` / `elapsed_ms` 字段。
4. THE ResultMeta SHALL 使 `truncate_reason` 取值为 `"max_rows"`、`"max_bytes"` 或 None 之一。
5. WHEN THE StarRocks_Adapter 组装 `ResultSet`，THE StarRocks_Adapter SHALL 使 `ResultMeta.truncated` 与 `ResultSet.truncated` 一致。
6. THE StarRocks_Adapter SHALL 使 `execute` 成功返回的 `ResultSet` 可承载于 Layer 3 `Answer.rows`。

### Requirement 13: Schema 片段读取（Schema Snippet Reading）

**User Story:** 作为 Layer 4 Schema Linking，我希望通过 `get_schema_snippet` 拿到指定表的 schema 描述，以便在生成 SQL 时对齐真实表结构。

#### Acceptance Criteria

1. WHEN THE StarRocks_Adapter 执行 `get_schema_snippet(table_names)`，THE Schema_Reader SHALL 读取各指定表的 schema 元信息并返回 schema 描述字符串列表。
2. THE StarRocks_Adapter SHALL 使用只读账号连接执行 schema 读取。
3. IF schema 读取过程失败，THEN THE StarRocks_Adapter SHALL 抛出归一化后的 `SchemaReadError`。

### Requirement 14: 错误处理（Error Handling · fail-fast / fail-closed）

**User Story:** 作为平台工程师，我希望本层所有错误都快速、显式、脱敏、可归类，以便失败不被静默吞掉且凭证不在错误信息中回显。

#### Acceptance Criteria

1. WHEN `get_adapter` 收到未注册的 `datasource_id`，THE Adapter_Registry SHALL 快速失败并返回 `UnknownDatasourceError`。
2. IF 无法经 Layer 6 取得连接，THEN THE StarRocks_Adapter SHALL 不自建连接重试并返回归一化后的 `ConnectionError`。
3. WHEN 数据源在 `execute` / `dry_run` 返回错误（语法 / 运行期 / 只读账号拒写），THE StarRocks_Adapter SHALL 返回脱敏后的 `QueryExecutionError`。
4. IF 执行或探测超过其超时上界，THEN THE StarRocks_Adapter SHALL 中断并返回 `QueryTimeoutError`。
5. WHEN 结果超过行 / 字节上限，THE StarRocks_Adapter SHALL 将其作为正常路径处理（截断并标记）而不作为错误。
6. IF `get_schema_snippet` 读取失败，THEN THE StarRocks_Adapter SHALL 返回 `SchemaReadError`。
7. IF 凭证解析失败（启动期或运行期），THEN THE StarRocks_Adapter SHALL fail-closed 而不降级为无凭证连接，并返回脱敏后的 `ConnectionError`。

### Requirement 15: EKS 部署与 IRSA 凭证（Deployment）

**User Story:** 作为平台工程师，我希望本层以常驻 Python 服务部署于现有 EKS 并经 IRSA 获取凭证，以便与 query-engine / connection-proxy 同集群运行且凭证注入受控。

#### Acceptance Criteria

1. THE StarRocks_Adapter SHALL 以常驻 Python 服务形态部署于现有 EKS 集群。
2. WHEN 一个 Pod 启动或运行期需要凭证，THE StarRocks_Adapter SHALL 经 IRSA 从 Secrets Manager / AgentCore Identity 获取只读账号凭证。
3. THE StarRocks_Adapter SHALL 不将凭证硬编码于代码或写入容器镜像。
4. THE StarRocks_Adapter SHALL 复用标准 MySQL 驱动 PyMySQL 而不自研连接池。

## Appendix A：正确性属性 → 验收标准 可追溯性映射

下表将设计文档 `design.md` §Correctness Properties 中的 10 条正确性属性（Property 1 ~ Property 10）映射到本文档中覆盖它们的验收标准，供设计文档「Validates: Requirements X.Y」引用对齐。每条 Property 至少被一条验收标准覆盖。

| 设计属性 | 属性标题 | 覆盖的验收标准 |
|---------|---------|---------------|
| Property 1 | 只读账号执行（All Execution Uses Read-Only Account） | 3.1, 3.2, 3.3, 3.4, 3.5, 13.2 |
| Property 2 | 只执行不放宽 / 不改写（Execute As-Is, No Relaxation, No Rewrite） | 4.1, 4.2, 4.3, 4.4, 4.5 |
| Property 3 | 结果规模受上限约束并正确标记截断（Result Bounded and Truncation Flagged） | 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9 |
| Property 4 | 所有已注册 adapter 满足同一 Protocol（Uniform Protocol Conformance） | 1.1, 1.2, 1.3, 1.4, 6.1, 6.2, 6.3 |
| Property 5 | `dialect` 与真实源方言一致（Dialect Matches Real Source） | 7.1, 7.2, 7.3 |
| Property 6 | `dry_run` 无副作用（Dry Run Has No Side Effects） | 8.1, 8.2, 8.3, 8.4 |
| Property 7 | 错误归一化且不泄露栈 / 凭证（Errors Normalized, No Stack/Secret Leak） | 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 10.9 |
| Property 8 | 连接经 Layer 6、不直连 / 不自建池（Connect via Layer 6, No Direct/Own Pool） | 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 3.4 |
| Property 9 | 凭证不落明文日志（Secrets Never in Plaintext Logs） | 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7 |
| Property 10 | 同一 `datasource_id` 恒解析到同一实现（Stable Resolution by datasource_id） | 2.4, 2.5, 2.6, 14.1 |
