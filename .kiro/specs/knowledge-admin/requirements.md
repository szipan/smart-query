# Requirements Document

## Introduction

本文档定义智能问数系统 v2 中 **知识管理后端（knowledge-admin，术语/实体知识管理面）** 的需求。

该服务面向**系统管理员**，提供对**术语归一化两条通道**基础数据的管理能力：

- **软通道（术语库 / Glossary）**：标准词 + 同义词 + 业务口径定义，供 Layer 3 的 `Soft_Glossary_Resolver` 经向量检索使用。
- **硬通道（实体字典 / Entity Dictionary）**：关键业务实体（电站 / 指标 / 组织等）的标准 id + 类型 + 别名（同义词），供 Layer 3 的 `Hard_Entity_Resolver` 确定性匹配使用（**本期硬通道运行时匹配器不实现**，仅管理其数据）。

管理能力包括：增删改查、启用/停用、分页搜索、**批量导入（Excel + CSV，带模板下载与逐行成功/失败报告）**、导出。

**架构定位**：本服务是独立于查询链路（Layer 2 `/v1/query`）的**管理面（control plane）**，前后端分离，**本期只实现后端接口**（不做前端）。它是知识数据的**权威写入入口**；写入后需把软通道数据同步投影到运行时的向量库，供 Layer 3 查询链路读取。

**设计参考与许可**：接口与批量导入的工程做法**借鉴 SQLBot（GPLv3）`apps/terminology` 的思路**（分页搜索、合并的增改、批量导入 + 逐行错误报告、模板/导出），但**不复制其 GPLv3 源码**，clean-room 重写。Web 框架与 SQLBot 保持一致，采用 **FastAPI**。

**本期范围内的查询面组件**：硬通道运行时匹配器（**Aho-Corasick 多模式串匹配**，实现 Layer 3 `Entity_Dictionary` 契约，见 Requirement 11）纳入本期实现。

**本期非目标（Out of Scope）**：前端 UI；从业务库/数仓自动同步实体（本期硬通道**纯手动维护**）；服务的云端部署（本期只交付代码 + 测试）；数据源级作用域细分（本期单数据源 StarRocks，作用域字段预留但不细分）。

## Glossary

- **Knowledge_Admin_Service**：知识管理后端服务整体，本文档所描述系统的总称。
- **Admin_User**：系统管理员，本服务的唯一授权使用者。
- **Auth_Guard**：管理员鉴权组件，校验请求者具备管理员角色；**本期为占位实现**（预留接口，默认放行或读取占位角色），未来接阳光云 OAuth 的 admin 角色/scope。
- **Glossary_Term**：软通道条目。字段：`term_id`、`term`（标准词）、`synonyms`（同义词列表）、`definition`（业务口径定义）、`enabled`、`created_at`、`updated_at`。
- **Entity_Entry**：硬通道条目。字段：`entry_id`、`canonical`（标准名）、`entity_type`（实体类型，自由文本）、`entity_id`（映射到的标准 id）、`aliases`（别名/同义词列表）、`enabled`、`created_at`、`updated_at`。
- **Term_Store**：软通道条目的权威存储（DynamoDB）。
- **Entity_Store**：硬通道条目的权威存储（DynamoDB）。
- **Glossary_Index_Sync**：软通道写入后的索引同步组件，负责把 Glossary_Term 经 Embedder 向量化后投影（upsert / delete）到 Vector_Store 的 Glossary_Namespace。
- **Embedder**：文本向量化能力（当前 Bedrock Titan），与 Layer 3/4 共用抽象。
- **Vector_Store**：可插拔向量存储（当前 OpenSearch），与 Layer 3/4 共用抽象；软通道检索使用其 Glossary_Namespace。
- **Glossary_Namespace**：软术语库在向量库中的命名空间，取值 `glossary`。
- **Batch_Import**：批量导入能力，接受 Excel（.xlsx）或 CSV 文件，逐行创建/更新条目。
- **Import_Template**：批量导入模板文件（Excel / CSV），供管理员下载后填写。
- **Import_Report**：批量导入结果报告，含成功数、失败数、重复数，以及每条失败记录及其错误原因。
- **Business_Key**：条目去重与 upsert 的业务键——软通道为 `term`，硬通道为 `(entity_type, entity_id)`。
- **Admin_Observer**：可观测/审计组件，记录管理员的写操作（create/update/delete/enable/import）。
- **Entity_Matcher**：硬通道运行时匹配器（查询面），基于 Aho-Corasick 从 Alias_Index 加载别名→实体映射，实现 Layer 3 的 `Entity_Dictionary` 契约。
- **Response_Envelope**：统一响应信封（含状态、数据、错误信息）。
- **Page_Result**：分页查询结果，含 `current_page`、`page_size`、`total_count`、`total_pages`、`data`。

## Requirements

### Requirement 1: 服务形态与层边界

**User Story:** 作为系统架构师，我希望知识管理是独立的管理面、不干扰查询链路，以便读写职责分离、可独立演进与部署。

#### Acceptance Criteria

1. THE Knowledge_Admin_Service SHALL 作为独立模块存在，不改动 Layer 2/3/4/5 的查询链路核心逻辑。
2. THE Knowledge_Admin_Service SHALL 使用 FastAPI 暴露 REST 接口。
3. THE Knowledge_Admin_Service SHALL NOT 执行自然语言问数、SQL 生成、隔离注入或查询执行（这些属于 Layer 2/3/4/5）。
4. THE Knowledge_Admin_Service SHALL 作为软/硬通道知识数据的权威写入入口，其他组件不得绕过本服务直接写权威存储。

### Requirement 2: 管理员鉴权（本期占位）

**User Story:** 作为安全负责人，我希望只有管理员能管理知识数据，以便防止未授权修改。

#### Acceptance Criteria

1. WHEN THE Knowledge_Admin_Service 收到任一写操作请求（create/update/delete/enable/import）, THE Auth_Guard SHALL 先校验请求者具备管理员角色再放行。
2. IF THE Auth_Guard 判定请求者不具备管理员角色, THEN THE Knowledge_Admin_Service SHALL 拒绝该请求并返回未授权错误，不改动任何数据。
3. THE Auth_Guard SHALL 以占位方式实现且暴露清晰的接口/扩展点，以便未来替换为阳光云 OAuth 的 admin 角色/scope 校验而不改动业务代码。

### Requirement 3: 软通道术语——增删改查

**User Story:** 作为管理员，我希望增删改查术语及其同义词与定义，以便维护软通道的业务口径。

#### Acceptance Criteria

1. WHEN THE Admin_User 提交一条不含 term_id 的术语, THE Knowledge_Admin_Service SHALL 在 Term_Store 中新建一条 Glossary_Term 并生成 term_id 与时间戳。
2. WHEN THE Admin_User 提交一条含 term_id 的术语, THE Knowledge_Admin_Service SHALL 更新 Term_Store 中对应的 Glossary_Term 并刷新 updated_at。
3. WHEN THE Admin_User 请求删除一组 term_id, THE Knowledge_Admin_Service SHALL 从 Term_Store 删除对应 Glossary_Term。
4. WHEN THE Admin_User 请求启用或停用某条术语, THE Knowledge_Admin_Service SHALL 更新其 enabled 字段而不删除该条目。
5. THE Knowledge_Admin_Service SHALL 提供按 term 关键词模糊搜索的分页查询，返回 Page_Result。
6. IF 新建或更新导致 term 与已存在条目重复（违反 Business_Key）, THEN THE Knowledge_Admin_Service SHALL 拒绝该操作并返回重复冲突错误。

### Requirement 4: 软通道写入的向量库同步

**User Story:** 作为查询链路的运营方，我希望管理员对术语的改动能及时反映到检索层，以便 Layer 3 软通道能召回最新术语。

#### Acceptance Criteria

1. WHEN 一条 Glossary_Term 被新建或更新且其 enabled 为真, THE Glossary_Index_Sync SHALL 将该术语（标准词 + 同义词 + 定义）经 Embedder 向量化后 upsert 到 Vector_Store 的 Glossary_Namespace。
2. WHEN 一条 Glossary_Term 被删除或被停用, THE Glossary_Index_Sync SHALL 从 Vector_Store 的 Glossary_Namespace 删除该术语对应的索引条目。
3. THE Glossary_Index_Sync SHALL 使 Vector_Store 中的索引条目携带可回溯到 Term_Store 权威条目的标识（term_id）。
4. IF THE Glossary_Index_Sync 的向量化或索引写入失败, THEN THE Knowledge_Admin_Service SHALL 使该次写操作整体失败并向调用方报告错误，不留下权威存储已改而索引未改的不一致（写入顺序与失败处理在设计中定义）。

### Requirement 5: 硬通道实体——增删改查（手动维护）

**User Story:** 作为管理员，我希望手动维护实体的标准 id、类型与别名，以便硬通道能确定性地把别名映射到标准实体。

#### Acceptance Criteria

1. WHEN THE Admin_User 提交一条不含 entry_id 的实体条目, THE Knowledge_Admin_Service SHALL 在 Entity_Store 中新建一条 Entity_Entry 并生成 entry_id 与时间戳。
2. WHEN THE Admin_User 提交一条含 entry_id 的实体条目, THE Knowledge_Admin_Service SHALL 更新 Entity_Store 中对应的 Entity_Entry 并刷新 updated_at。
3. WHEN THE Admin_User 请求删除一组 entry_id, THE Knowledge_Admin_Service SHALL 从 Entity_Store 删除对应 Entity_Entry。
4. WHEN THE Admin_User 请求启用或停用某条实体条目, THE Knowledge_Admin_Service SHALL 更新其 enabled 字段而不删除该条目。
5. THE Knowledge_Admin_Service SHALL 提供按 canonical / alias 关键词模糊搜索、并可按 entity_type 过滤的分页查询，返回 Page_Result。
6. THE Knowledge_Admin_Service SHALL 要求 Entity_Entry 的 canonical、entity_type、entity_id 为必填，aliases 可为空列表。
7. IF 新建或更新导致 (entity_type, entity_id) 与已存在条目重复（违反 Business_Key）, THEN THE Knowledge_Admin_Service SHALL 拒绝该操作并返回重复冲突错误。
8. WHEN 新建或更新 Entity_Entry, THE Knowledge_Admin_Service SHALL 校验其 aliases 列表内部无重复、且其中任一别名未被其他 Entity_Entry（不同 Business_Key）占用。
9. IF 存在跨实体别名冲突（某别名已属于另一个 Entity_Entry）, THEN THE Knowledge_Admin_Service SHALL 拒绝该操作并返回别名冲突错误，指明冲突的别名与其归属实体。
10. THE Knowledge_Admin_Service SHALL 将 Entity_Entry 仅写入 Entity_Store 与 Alias_Index，且不直接推送到运行时匹配器（匹配器从 Alias_Index 拉取数据，见 Requirement 11）。

### Requirement 11: 硬通道运行时匹配器（Aho-Corasick）

**User Story:** 作为查询链路，我希望把用户问题中出现的实体别名确定性地识别并映射到标准实体，以便 Layer 3 硬通道产出正确的 Resolved_Entity。

> 说明：本需求描述的是**查询面**组件（实现 Layer 3 的 `Entity_Dictionary` 契约），与管理面同属本 spec 范围。它是 Alias_Index 数据的运行时消费者。

#### Acceptance Criteria

1. THE Entity_Matcher SHALL 从 Alias_Index 加载「别名→实体」映射，构建 Aho-Corasick 自动机用于多模式串匹配。
2. WHEN THE Entity_Matcher 处理一个问题字符串, THE Entity_Matcher SHALL 扫描出其中出现的所有已登记别名，并为每个命中产出携带 canonical / entity_type / entity_id 的 Resolved_Entity。
3. WHEN 多个命中在文本中重叠, THE Entity_Matcher SHALL 采用最长匹配、非重叠、从左到右的消解策略。
4. THE Entity_Matcher SHALL 支持最小别名长度阈值，低于阈值的别名不参与匹配以降低误命中。
5. THE Entity_Matcher SHALL 使用与写入 Alias_Index 时相同的 normalize() 对问题文本与别名做一致归一化。
6. THE Entity_Matcher SHALL 在启动时加载并按 TTL 周期性重建自动机，且暴露显式 refresh 方法。
7. IF THE Alias_Index 不可用或加载失败, THEN THE Entity_Matcher SHALL 降级为空匹配（返回空结果、不抛出以阻断查询链路），行为与当前未接字典（None）等价。
8. THE Entity_Matcher SHALL 实现 Layer 3 的 Entity_Dictionary 契约（`match(question) -> list`），并可经 wiring 注入替换现有的 None。

### Requirement 6: 批量导入（Excel + CSV）

**User Story:** 作为管理员，我希望用 Excel 或 CSV 批量导入术语/实体并拿到逐行结果，以便高效录入并快速修正错误行。

#### Acceptance Criteria

1. THE Knowledge_Admin_Service SHALL 接受 Excel（.xlsx）与 CSV 两种格式的批量导入文件，分别用于软通道与硬通道。
2. THE Knowledge_Admin_Service SHALL 提供软通道与硬通道各自的 Import_Template 下载接口。
3. WHEN THE Admin_User 上传批量导入文件, THE Batch_Import SHALL 逐行解析并按 Business_Key 执行 upsert（存在则更新、不存在则新建）。
4. WHEN 某一行数据校验不通过, THE Batch_Import SHALL 跳过该行并记录其错误原因，且继续处理其余行不中断整批。
5. WHEN 批量导入完成, THE Knowledge_Admin_Service SHALL 返回 Import_Report，含成功数、失败数、重复数与每条失败记录及原因。
6. WHEN 软通道批量导入使某些 Glossary_Term 被新建或更新, THE Glossary_Index_Sync SHALL 对这些成功条目执行向量库同步（依 Requirement 4）。
7. IF 上传文件格式不受支持或列结构与模板不匹配, THEN THE Knowledge_Admin_Service SHALL 拒绝该文件并返回格式错误，不写入任何条目。

### Requirement 7: 导出

**User Story:** 作为管理员，我希望导出现有术语/实体，以便备份、离线编辑或迁移。

#### Acceptance Criteria

1. THE Knowledge_Admin_Service SHALL 提供软通道与硬通道各自的导出接口，导出为 Excel 文件。
2. THE Knowledge_Admin_Service SHALL 使导出的列结构与对应的 Import_Template 一致，以便导出结果可回填后再导入。
3. WHEN THE Admin_User 请求导出且带关键词过滤, THE Knowledge_Admin_Service SHALL 仅导出匹配过滤条件的条目。

### Requirement 8: 权威存储与数据模型

**User Story:** 作为系统架构师，我希望知识数据有清晰的权威存储与数据模型，以便一致地读写与投影。

#### Acceptance Criteria

1. THE Knowledge_Admin_Service SHALL 以 DynamoDB 作为 Term_Store 与 Entity_Store 的权威存储。
2. THE Term_Store SHALL 持久化 Glossary_Term 的全部字段，Entity_Store SHALL 持久化 Entity_Entry 的全部字段。
3. THE Knowledge_Admin_Service SHALL 以 term 作为软通道 Business_Key、以 (entity_type, entity_id) 作为硬通道 Business_Key，保证同一业务键唯一。
4. THE Knowledge_Admin_Service SHALL 通过依赖注入接入 Term_Store / Entity_Store / Embedder / Vector_Store，以便单元测试用 mock 替换、不依赖真实外部服务。

### Requirement 9: 可观测与审计

**User Story:** 作为安全与运维负责人，我希望管理员的写操作可审计、接口有可观测记录，以便追责与排障。

#### Acceptance Criteria

1. WHEN THE Admin_User 执行任一写操作（create/update/delete/enable/import）, THE Admin_Observer SHALL 记录一条包含操作类型、目标业务键、操作者标识与时间的审计记录。
2. THE Admin_Observer SHALL 对日志脱敏，不落敏感明文凭证。
3. THE Knowledge_Admin_Service SHALL 对失败请求返回结构化 Response_Envelope，含明确的错误码与信息。

### Requirement 10: 复用与许可约束

**User Story:** 作为技术负责人，我希望本服务的复用与许可边界清晰，以便合规借鉴第三方思路并复用既有系统抽象。

#### Acceptance Criteria

1. THE Knowledge_Admin_Service SHALL 借鉴 SQLBot（GPLv3）`apps/terminology` 的接口与批量导入思路重新实现，且不复制其 GPLv3 源码。
2. THE Knowledge_Admin_Service SHALL 复用系统既有的 Embedder 与 Vector_Store 抽象，与 Layer 3/4 保持一致。
3. THE Knowledge_Admin_Service SHALL 采用 FastAPI 作为 Web 框架（与 SQLBot 一致）。

## Appendix A：与现有系统的衔接点

- **软通道消费端**：Layer 3 `Soft_Glossary_Resolver` 从 Vector_Store 的 `glossary` 命名空间检索；本服务是该命名空间数据的权威写入与同步来源，取代现有 `scripts/ingest_opensearch.py` 中手写的术语种子。
- **硬通道消费端**：Layer 3 `Hard_Entity_Resolver` 依赖 `EntityDictionary`（当前 wiring 中为 `None`）；本服务负责维护其数据（Entity_Store），运行时匹配器（Aho-Corasick）为未来工作，届时从 Entity_Store 加载条目构建自动机。
- **共用抽象**：`Embedder`（`adapters/bedrock.py`）、`Vector_Store`（`adapters/opensearch.py`）、`AwsSettings`（`adapters/settings.py`）。
- **待业务确认项（不阻塞本期）**：entity_type 的受控枚举（本期自由文本）；数据源级作用域细分（本期单数据源）；管理员角色的最终判定方式（本期占位）。
