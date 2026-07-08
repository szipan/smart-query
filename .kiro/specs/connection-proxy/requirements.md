# Requirements Document

## Introduction

本文档为智能问数系统 v2 的 **Layer 6 —— connection-proxy（连接代理层 · ProxySQL 常驻 EKS）** 的需求规约，需求由已批准的设计文档 `.kiro/specs/connection-proxy/design.md` 反向派生而来，并与其正文、数据模型（`MysqlServerSpec` / `MysqlUserSpec` / `QueryRuleSpec` / `GlobalVarsSpec` / `ProxySqlDesiredConfig`）、数据流图（§2.4 标准查询流、§2.5 故障隔离时序、§2.6 复用/限流示意）以及 §Correctness Properties 中的 11 条正确性属性保持一致，同时与架构文档 `docs/smart-query-architecture-v2.md`、上游契约 `.kiro/specs/query-engine/design.md`（Layer 3 §3.5 `DatasourceAdapter`）、`.kiro/specs/isolation-guard/design.md`（Layer 5）对齐。

connection-proxy 的形态是一条**设计红线**：它**不是业务 Python 代码，而是「一个成熟的基础设施组件 ProxySQL + 一套配置面与运维契约」**。它承接上游 Layer 7 `DatasourceAdapter`（经 MySQL 驱动）发来的、**已由 Layer 5 校验通过的安全只读 SQL**，通过一个受控的**后端连接池**安全、高效、可观测地送达 StarRocks 只读端点并回传结果。它只做四件事外加承接 frontend 连接：**连接池 / 连接复用（multiplexing）、限流 / 并发控制、故障隔离、只读路由**。

本层的边界必须被严格遵守：它**不做数据隔离、不解析权限、不生成或改写 SQL 语义、不做只读 AST 校验**——这些确定性安全职责全部归属 Layer 5。ProxySQL 的 `mysql_query_rules` 至多做**粗粒度纵深防御**（整条拒绝或原样放行），是**冗余的第二/第三道防线**，**绝不替代**且**绝不改变**系统安全正确性——安全正确性的唯一权威始终是 Layer 5（确定性只读校验）与 Layer 7（只读账号）。

所有验收标准遵循 EARS 句式（Ubiquitous / Event-driven / State-driven / Unwanted-event / Optional / Complex），每条标准均为原子、可独立测试的陈述。文档末尾提供追溯附录，将设计文档 §Correctness Properties 中的 11 条正确性属性映射到覆盖它们的验收标准。

## Glossary

- **Connection_Proxy 系统 / ProxySQL**：Layer 6 整体连接代理子系统；以官方 ProxySQL 镜像作为独立基础设施进程常驻现有 EKS，对外表现为「一个行为等价于 StarRocks 只读端点的 MySQL 端点」。
- **Frontend**：ProxySQL 的 MySQL 协议前端，监听数据面端口 `:6033`，承接来自 Layer 7 的连接。
- **Backend**：ProxySQL 向下连接的目标，当前恒为 StarRocks 只读端点（MySQL 协议 `:9030`）。
- **Readonly_Hostgroup**：唯一的只读 hostgroup（`READONLY_HOSTGROUP=10`），所有 `mysql_servers` 归入其中；配置面不存在任何可写 hostgroup。
- **Query_Processor**：ProxySQL 内建模块，对每条 SQL 应用 `mysql_query_rules`（粗粒度拒绝/放行），不改写语义。
- **Query_Rule（DENY / ALLOW）**：`mysql_query_rules` 中的规则；动作只有两种终态——DENY（命中即整条拒绝）或 ALLOW（命中即放行），结构上不含 `replace_pattern`。
- **Connection_Pool**：ProxySQL 后端连接池，复用少量 backend 连接服务大量 frontend 连接，并施加并发上限与排队。
- **Multiplexing**：多路复用；frontend 连接在事务边界处归还 backend 连接，使 frontend N 连接复用 backend M 连接（M ≤ 上限），由 `mysql-multiplexing=true` 开启。
- **Monitor**：ProxySQL 健康监控模块，用独立 monitor 账号周期性 ping / connect 探测 backend，执行剔除与恢复。
- **Shunning（SHUNNED / ONLINE）**：后端健康状态机；连续探测失败达阈值置为 SHUNNED（剔除、停止接新连接），探测恢复后置回 ONLINE（重新纳入流量）。
- **Max_Connections**：后端并发硬上限；`mysql_servers.max_connections`（每 backend，默认 200）与 `mysql-max_connections`（frontend 全局，默认 2048）共同约束。
- **Datasource_Adapter（Layer 7）**：上游 `DatasourceAdapter`，经 MySQL 驱动连接本层 frontend `:6033`，在其上执行 `execute(safe_sql) -> ResultSet` / `dry_run(sql) -> None`；ResultSet 语义与方言差异归 Layer 7。
- **Isolation_Guard（Layer 5）**：间接上游安全红线层；本层收到的 SQL 已由其校验为安全只读 SQL，本层不感知隔离语义。
- **Secret_Resolver**：密钥解析契约（`SecretResolver`），经 Secrets Manager / AgentCore Identity + IRSA 将密钥引用解析为运行期值；解析后的明文绝不落日志。
- **Admin_Plane（:6032） / Data_Plane（:6033）**：配置面 vs 数据面。Admin `:6032`（MySQL 协议）由运维/初始化脚本操作四张核心表并 `LOAD/SAVE ... TO RUNTIME/DISK`；数据面 `:6033` 承接 Layer 7 查询连接。
- **Metrics / stats_mysql_***：ProxySQL 内建统计表（`stats_mysql_connection_pool` / `stats_mysql_query_rules` / `stats_mysql_global` / `stats_mysql_query_digest`），经 Prometheus exporter 暴露。
- **ProxySqlDesiredConfig**：一份声明式的完整期望配置（servers / users / query_rules / globals 聚合），由初始化脚本据此生成 Admin SQL 并 LOAD/SAVE；含 `assert_readonly_only` 与 `assert_no_rewrite_rules` 自检。
- **ProxySqlConfigurator**：配置面初始化器，把 `ProxySqlDesiredConfig` 渲染为 Admin SQL 并注入密钥引用；仅在部署/引导时运行，非数据面业务代码。
- **safe_sql**：来自 Layer 7、已由 Layer 5 校验通过的安全只读 SQL 文本。
- **IRSA**：IAM Roles for Service Accounts；EKS Pod 据此从 Secrets Manager 获取只读账号 / monitor 账号凭证。

## Requirements

### Requirement 1: 只读路由不可绕过（Read-Only Routing）

**User Story:** 作为安全平台工程师，我希望所有后端连接在拓扑上只能指向 StarRocks 只读端点，以便即使查询规则全部关闭，流量也无法被路由到任何可写目标。

#### Acceptance Criteria

1. THE Connection_Proxy 系统 SHALL 使配置面仅定义一个只读 hostgroup（`READONLY_HOSTGROUP=10`）。
2. THE Connection_Proxy 系统 SHALL 使 `mysql_servers` 中的每一个 backend 目标的 `hostgroup_id` 均等于 `READONLY_HOSTGROUP`。
3. THE Connection_Proxy 系统 SHALL 使 `mysql_users` 中每个 frontend 账号的 `default_hostgroup` 指向 `READONLY_HOSTGROUP`。
4. THE Connection_Proxy 系统 SHALL 在配置面不存在任何指向可写目标的 server、hostgroup 或路由规则。
5. WHEN 一条查询经 Frontend 进入，THE Connection_Proxy 系统 SHALL 仅将其路由至只读 hostgroup 中的 backend。
6. WHERE `mysql_query_rules` 为空，THE Connection_Proxy 系统 SHALL 仍仅将全部流量送往只读端点。
7. WHEN `ProxySqlConfigurator` 应用一份 `ProxySqlDesiredConfig`，THE Connection_Proxy 系统 SHALL 先执行 `assert_readonly_only` 自检，确认所有 server 归入只读 hostgroup。

### Requirement 2: 连接复用（Multiplexing）

**User Story:** 作为平台工程师，我希望 frontend 的大量连接复用少量 backend 连接，以便在高并发问数场景下不为每个查询新建 StarRocks 连接。

#### Acceptance Criteria

1. THE Connection_Proxy 系统 SHALL 将全局变量 `mysql-multiplexing` 置为 true 以开启多路复用。
2. WHEN 一条 frontend 连接在事务边界处变为空闲，THE Connection_Proxy 系统 SHALL 将其占用的 backend 连接归还连接池以供其他 frontend 连接复用。
3. THE Connection_Proxy 系统 SHALL 不为每一条 frontend 连接独占一条 backend 连接。
4. WHILE 系统持续服务查询，THE Connection_Proxy 系统 SHALL 使任意时刻活跃的 backend 连接数不超过所配置的 backend 连接池上限。
5. WHERE frontend 账号的 `transaction_persistent` 为 false 且查询为只读，THE Connection_Proxy 系统 SHALL 允许该连接参与多路复用。
6. IF 一条连接处于显式事务、`SET` 会话变量、临时表或 `LOCK` 等会破坏复用的状态，THEN THE Connection_Proxy 系统 SHALL 在该状态期间保持 backend 连接绑定而不归还。

### Requirement 3: 后端并发上限与排队（Throttling）

**User Story:** 作为平台工程师，我希望后端并发有硬上限且超限请求排队，以便 StarRocks 不会被无限扩张的连接打垮。

#### Acceptance Criteria

1. THE Connection_Proxy 系统 SHALL 为每个 backend 配置 `mysql_servers.max_connections`（默认 200）作为后端连接上限。
2. THE Connection_Proxy 系统 SHALL 配置全局 `mysql-max_connections`（默认 2048）作为 frontend 连接总上限。
3. WHILE 指向某 backend 的活跃连接数已达其 `max_connections`，THE Connection_Proxy 系统 SHALL 将新到请求置入等待队列而非新建超限连接。
4. WHEN 连接池出现空闲 backend 连接，THE Connection_Proxy 系统 SHALL 从等待队列中取出请求并分配该空闲连接。
5. IF 排队请求在 `mysql-default_query_timeout`（默认 60000 毫秒）内仍未获得可用连接，THEN THE Connection_Proxy 系统 SHALL 返回超时错误。
6. THE Connection_Proxy 系统 SHALL 使任意时刻指向任一 backend 的活跃连接数不超过该 backend 的 `max_connections`。

### Requirement 4: 故障隔离——剔除、fail-fast 与恢复（Fault Isolation）

**User Story:** 作为平台工程师，我希望不健康的后端被快速剔除且请求 fail-fast，以便坏后端不会导致请求堆积形成雪崩。

#### Acceptance Criteria

1. THE Connection_Proxy 系统 SHALL 将 `mysql-monitor_enabled` 置为 true 并使用独立 monitor 账号周期性 ping / connect 探测每个 backend。
2. THE Connection_Proxy 系统 SHALL 按 `mysql-monitor_ping_interval`（默认 2000 毫秒）与 `mysql-monitor_connect_interval`（默认 2000 毫秒）执行探测。
3. IF 某 backend 连续探测失败达到 `shun_on_failures`（默认 3 次），THEN THE Connection_Proxy 系统 SHALL 将该 backend 标记为 SHUNNED 并停止向其分配新连接。
4. WHILE 某 backend 处于 SHUNNED 状态，THE Connection_Proxy 系统 SHALL 不向该 backend 转发新查询。
5. WHEN 一个被剔除的 backend 经 `shun_recovery_time`（默认 10 秒）后探测成功，THE Connection_Proxy 系统 SHALL 将其重新标记为 ONLINE 并恢复向其分配流量。
6. IF 所有 backend 均不可用，THEN THE Connection_Proxy 系统 SHALL 在超时上界内对新请求 fail-fast 返回错误而非无限等待。

### Requirement 5: 粗粒度规则只拒绝或放行（Query Rules Only Deny or Allow）

**User Story:** 作为安全平台工程师，我希望粗粒度查询规则只能整条拒绝或原样放行，以便本层永远不会注入隔离、改写权限或重写 SQL。

#### Acceptance Criteria

1. THE Connection_Proxy 系统 SHALL 使每条 `mysql_query_rules` 的动作仅为 DENY 或 ALLOW 之一。
2. THE Connection_Proxy 系统 SHALL 不在任何 query rule 中配置 `replace_pattern` 或任何 SQL 改写/重写语义。
3. WHEN Query_Processor 对一条 SQL 匹配到 DENY 规则（例如明显的非 SELECT 语句 `INSERT`/`UPDATE`/`DELETE`/`REPLACE`/`DROP`/`ALTER`/`CREATE`/`TRUNCATE`/`GRANT`/`SET`），THE Connection_Proxy 系统 SHALL 整条拒绝该 SQL 并返回携带 `error_msg` 的 MySQL 错误，且不向任何 backend 转发。
4. WHEN Query_Processor 对一条 SQL 未匹配任何 DENY 规则，THE Connection_Proxy 系统 SHALL 放行该 SQL 进入只读路由。
5. WHEN `ProxySqlConfigurator` 应用一份 `ProxySqlDesiredConfig`，THE Connection_Proxy 系统 SHALL 先执行 `assert_no_rewrite_rules` 自检，确认所有规则动作仅为 DENY 或 ALLOW。

### Requirement 6: 不改写 SQL 语义（SQL Semantics Preserved）

**User Story:** 作为 Layer 7 适配器，我希望本层逐字节转发我发出的安全 SQL，以便「我发出什么就在 StarRocks 上执行什么」，不产生任何隐式改写。

#### Acceptance Criteria

1. WHEN 一条 SQL 被放行至只读路由，THE Connection_Proxy 系统 SHALL 向 backend 转发与从 Frontend 收到的 SQL 逐字节相同的语句。
2. THE Connection_Proxy 系统 SHALL 不对被转发的 SQL 进行注入、重写或语义改写。
3. IF 一条 SQL 命中显式 DENY 规则，THEN THE Connection_Proxy 系统 SHALL 不向任何 backend 转发该 SQL 的任何片段。
4. THE Connection_Proxy 系统 SHALL 不感知、不解析、不校验被转发 SQL 的隔离语义或权限语义。

### Requirement 7: 密钥不落明文日志（Secrets Never in Plaintext）

**User Story:** 作为安全平台工程师，我希望只读账号与 monitor 账号的密码在任何日志、指标、配置导出中都不以明文出现，以便凭证不因可观测性而泄露。

#### Acceptance Criteria

1. THE Connection_Proxy 系统 SHALL 在配置模型（`MysqlUserSpec` / `GlobalVarsSpec`）中仅保存密钥引用（如 Secrets ARN / key 名）而不保存明文密码。
2. WHEN `ProxySqlConfigurator` 应用配置，THE Secret_Resolver SHALL 在运行期经 Secrets Manager / AgentCore Identity + IRSA 将密钥引用解析为明文值。
3. WHEN 解析后的明文密码被使用，THE Connection_Proxy 系统 SHALL 仅将其置入下发到 Admin 接口的 SQL 中而不写入任何日志。
4. THE Connection_Proxy 系统 SHALL 不在任何日志、指标或配置导出记录中包含明文密码。
5. THE Connection_Proxy 系统 SHALL 在 `stats_mysql_query_digest` 中仅存参数化 digest 模板而非含值的完整 SQL。
6. THE Connection_Proxy 系统 SHALL 不将凭证写入容器镜像。

### Requirement 8: 单一后端 = StarRocks（Single, Extensible Backend）

**User Story:** 作为平台工程师，我希望当前后端集合恰为唯一的 StarRocks 只读端点且未来新增后端只能进入只读 hostgroup，以便可扩展性不破坏只读拓扑。

#### Acceptance Criteria

1. THE Connection_Proxy 系统 SHALL 使当前 `mysql_servers` 的 backend 集合恰为一个 StarRocks 只读端点。
2. THE Connection_Proxy 系统 SHALL 使该 StarRocks backend 指向只读 FE 端点（默认 `starrocks-ro.internal:9030`）与只读账号。
3. WHERE 未来向配置面新增任何 backend，THE Connection_Proxy 系统 SHALL 使新增 backend 的 `hostgroup_id` 等于 `READONLY_HOSTGROUP`。
4. THE Connection_Proxy 系统 SHALL 不在配置面存在任何非只读 hostgroup 的 backend。

### Requirement 9: 可观测指标（Observability）

**User Story:** 作为系统运维人员，我希望连接池使用率、后端健康状态与规则拒绝计数可被采集，以便随时排查工作流执行问题。

#### Acceptance Criteria

1. THE Connection_Proxy 系统 SHALL 经 `stats_mysql_connection_pool` 暴露连接池使用率指标（backend 活跃/空闲连接数、frontend 连接数）。
2. THE Connection_Proxy 系统 SHALL 经 `stats_mysql_connection_pool.status` 与 `mysql_servers` 暴露每个 backend 的健康状态（ONLINE / SHUNNED / OFFLINE）。
3. THE Connection_Proxy 系统 SHALL 经 `stats_mysql_query_rules` 暴露 query_rules 的命中/拒绝计数。
4. THE Connection_Proxy 系统 SHALL 经 `stats_mysql_global` 与 `stats_mysql_query_digest` 暴露查询错误、超时与慢查询计数。
5. THE Connection_Proxy 系统 SHALL 经 `stats_mysql_global` 暴露连接排队等待相关计数。
6. WHEN Prometheus exporter 抓取本层，THE Connection_Proxy 系统 SHALL 使连接池使用率、后端健康状态与规则拒绝计数在任意时刻均可被读取。

### Requirement 10: 安全权威归属 Layer 5（Redundant Defense Only）

**User Story:** 作为安全平台工程师，我希望本层的粗粒度规则仅作为冗余纵深防御，以便关闭它也不改变系统的安全正确性。

#### Acceptance Criteria

1. THE Connection_Proxy 系统 SHALL 将其粗粒度 `mysql_query_rules` 定位为冗余纵深防御而非安全校验权威。
2. WHERE 本层的粗粒度 query_rules 被关闭或删除，THE Connection_Proxy 系统 SHALL 仍由 Layer 5 确定性只读校验与 Layer 7 只读账号保证系统安全正确性。
3. THE Connection_Proxy 系统 SHALL 依赖 StarRocks 只读账号作为独立于本层规则的最终只读兜底防线。
4. THE Connection_Proxy 系统 SHALL 假定所收到的 SQL 已由 Layer 5 校验为安全只读 SQL，且不对其重复施加确定性只读 AST 校验作为安全权威。

### Requirement 11: 配置面与数据面分离（Control Plane vs Data Plane）

**User Story:** 作为平台工程师，我希望配置面与数据面严格分离，以便业务代码只接触数据面而配置由运维契约独占管理。

#### Acceptance Criteria

1. THE Connection_Proxy 系统 SHALL 在 Admin 端口 `:6032` 暴露配置面，供运维/初始化脚本操作 `mysql_servers` / `mysql_users` / `mysql_query_rules` / `global_variables` 四张核心表。
2. THE Connection_Proxy 系统 SHALL 在 Frontend 端口 `:6033` 暴露数据面，供 Layer 7 `DatasourceAdapter` 承接查询连接。
3. THE Datasource_Adapter（Layer 7）SHALL 仅连接数据面 `:6033` 而不接触配置面 `:6032`。
4. WHEN 运维/初始化脚本变更配置，THE Connection_Proxy 系统 SHALL 通过 `LOAD ... TO RUNTIME` 使其生效并通过 `SAVE ... TO DISK` 持久化。
5. THE Connection_Proxy 系统 SHALL 不向业务代码暴露 Admin 端口 `:6032`。

### Requirement 12: EKS 部署与 IRSA 凭证（Deployment）

**User Story:** 作为平台工程师，我希望 ProxySQL 以常驻组件部署于现有 EKS 并经 IRSA 获取凭证，以便高可用运行且凭证注入受控。

#### Acceptance Criteria

1. THE Connection_Proxy 系统 SHALL 以 Deployment（或 StatefulSet）加 Service 的形态常驻现有 EKS 的 `smart-query` 命名空间。
2. THE Connection_Proxy 系统 SHALL 经 Service 对集群内暴露数据面 `:6033` 供 Layer 7 访问。
3. THE Connection_Proxy 系统 SHALL 以多副本加反亲和方式部署，且各副本从磁盘/初始化脚本加载同一份期望配置。
4. WHEN 一个 ProxySQL Pod 启动，THE Connection_Proxy 系统 SHALL 经 IRSA 从 Secrets Manager 获取只读账号与 monitor 账号凭证。
5. THE Connection_Proxy 系统 SHALL 不使用 ECS Fargate 或 AgentCore Runtime 作为运行载体。

### Requirement 13: 错误处理（Error Handling · fail-fast / fail-closed）

**User Story:** 作为平台工程师，我希望本层所有错误都快速、显式、可观测，以便失败不被静默吞掉且凭证不在错误信息中回显。

#### Acceptance Criteria

1. WHEN 一条 SQL 命中粗粒度拒绝规则，THE Connection_Proxy 系统 SHALL 不转发该 SQL 并向 Layer 7 返回携带 `error_msg` 的 MySQL 错误。
2. IF 所有 backend 均处于 SHUNNED 或不可用状态，THEN THE Connection_Proxy 系统 SHALL 在超时上界内 fail-fast 返回连接/查询错误而非无限排队。
3. IF 某 backend 连接池打满，THEN THE Connection_Proxy 系统 SHALL 使请求排队至 `mysql-default_query_timeout` 上界，超时后返回超时错误。
4. IF 单条查询超过 `mysql-default_query_timeout`，THEN THE Connection_Proxy 系统 SHALL 中断该查询并返回超时错误。
5. IF 启动期密钥解析失败，THEN THE ProxySqlConfigurator SHALL fail-closed 拒绝启动并触发部署失败告警。
6. THE Connection_Proxy 系统 SHALL 不静默吞掉任何错误，且不在任何错误信息中回显密钥明文。
7. WHEN 本层返回任一错误，THE Connection_Proxy 系统 SHALL 使该错误在 §9 指标中可见（计入 error / rejected / timeout 计数）。

## Appendix A：正确性属性 → 验收标准 可追溯性映射

下表将设计文档 `design.md` §Correctness Properties 中的 11 条正确性属性映射到本文档中覆盖它们的验收标准，供设计文档「Validates: Requirements X.Y」引用对齐。每条 Property 至少被一条验收标准覆盖。

| 设计属性 | 属性标题 | 覆盖的验收标准 |
|---------|---------|---------------|
| Property 1 | 只读路由不可绕过（Read-Only Routing Cannot Be Bypassed） | 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7 |
| Property 2 | 连接复用（Connections Are Multiplexed, Not 1:1） | 2.1, 2.2, 2.3, 2.4, 2.5 |
| Property 3 | 后端并发不超上限（Backend Concurrency Is Bounded） | 3.1, 3.2, 3.3, 3.6 |
| Property 4 | 不健康后端被剔除且恢复后重新纳入（Unhealthy Backend Shunned, Recovered Rejoined） | 4.1, 4.2, 4.3, 4.4, 4.5 |
| Property 5 | 不改写 SQL 语义（SQL Semantics Are Not Rewritten） | 6.1, 6.2, 6.3, 6.4 |
| Property 6 | 粗粒度规则只拒绝或放行（Query Rules Only Deny or Allow） | 5.1, 5.2, 5.3, 5.4, 5.5 |
| Property 7 | 故障 fail-fast 且可观测（Failures Fail Fast and Are Observable） | 4.6, 13.2, 13.3, 13.4, 13.7 |
| Property 8 | 密钥不落明文日志（Secrets Never Appear in Plaintext Logs） | 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 13.6 |
| Property 9 | 单一后端 = StarRocks（Single Backend, Extensible but Currently Sole） | 8.1, 8.2, 8.3, 8.4 |
| Property 10 | 关键运维指标可观测（Operational Metrics Are Observable） | 9.1, 9.2, 9.3, 9.4, 9.5, 9.6 |
| Property 11 | 安全权威归属 Layer 5（Security Authority Remains at Layer 5） | 10.1, 10.2, 10.3, 10.4 |
