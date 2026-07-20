# Implementation Plan — knowledge-admin

> 遵守测试约定：每个模块配套单元测试（mock 注入，不连真实服务）；相邻能力间配集成测试（mock AWS client）；设计中的 Correctness Properties 配属性测试（hypothesis）。测试不过不算完成。
> 遵守许可约定：借鉴 SQLBot `apps/terminology` 思路，clean-room 重写，不复制 GPLv3 源码。

- [ ] 1. 数据模型与错误类型
  - `models.py`：`GlossaryTerm` / `EntityEntry` dataclass + 校验（必填、去重去空白、`normalize()` 归一化）
  - `errors.py`：`ValidationError / DuplicateKeyError / AliasConflictError / NotFoundError / Unauthorized / Forbidden / IndexSyncError`（各带 HTTP 状态）
  - 单测：模型校验正常/边界（空字段、别名内部重复、归一化）、错误类型状态映射
  - _Requirements: 8.2, 8.3, 5.6, 9.3_

- [ ] 2. 能力抽象接口（Protocol）
  - `deps.py`：`TermStore`、`EntityStore`、`AliasIndex`、`NameGuard`、`Embedder`、`VectorStore`、`GlossarySync`、`AuthGuard`、`AdminObserver`
  - 单测：Protocol 可被 mock 实现、契约签名
  - _Requirements: 8.4, 1.4_

- [ ] 3. 内存 fake 与测试基座
  - `tests/knowledge_admin/fakes.py`：内存版 TermStore/EntityStore/AliasIndex/NameGuard + FakeEmbedder/FakeVectorStore/FakeObserver
  - _Requirements: 8.4_

- [ ] 4. 软通道向量同步 `GlossaryIndexSync`（`sync.py`）
  - `sync_upsert(term)`：embed(term+synonyms+definition) → VectorStore.upsert（entry_id=term_id 可回溯）
  - `sync_delete(term_id)`：VectorStore.delete
  - 单测：upsert/删除调用正确、被索引文本组装、embed/upsert 失败向上抛（供 Service 回滚）
  - _Requirements: 4.1, 4.2, 4.3_

- [ ] 5. 软通道服务 `GlossaryService`（`service.py`）
- [ ] 5.1 CRUD + 唯一性
  - create（无 term_id）/ update（有 term_id）/ delete（id 列表）/ enable / 分页搜索
  - term 唯一性经 NameGuard 条件写；重复 → `DuplicateKeyError`
  - 单测：增改删启用、term 重复拒绝、更新不存在 → NotFound、分页与关键词过滤
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_
- [ ] 5.2 写入 → 同步的强一致与回滚
  - create/update（enabled）：先 embed → 写 Term_Store → 同步向量库；同步失败 → 回滚权威 + 释放名占用 → 抛 `IndexSyncError`
  - delete/停用：先删向量库 → 再改/删权威
  - 单测：同步失败触发回滚（权威与索引均不变）；删除/停用后向量库删除被调用
  - _Requirements: 4.1, 4.2, 4.4_

- [ ] 6. 硬通道 DynamoDB 事务写与别名唯一性
- [ ] 6.1 `DynamoDbEntityStore` + `DynamoDbAliasIndex`（`dynamodb_stores.py`）
  - Entity 表 PK=`{entity_type}#{entity_id}`；Alias_Index 表 PK=`alias_normalized`
  - TransactWriteItems：实体项条件写 + 别名项条件写（`attribute_not_exists OR owner=self`）+ 移除别名 Delete
  - 单测：用 fake DynamoDB client 模拟条件失败 → 映射 Duplicate/AliasConflict；更新时别名增删差集正确
  - _Requirements: 5.1, 5.2, 5.7, 5.8, 5.9, 8.1_
- [ ] 6.2 硬通道服务 `EntityService`（`service.py`）
  - CRUD + enable + 分页搜索（canonical/alias 关键词、entity_type 过滤）
  - 校验必填（canonical/entity_type/entity_id）；别名列表内去重；跨实体别名冲突 → `AliasConflictError`（指明冲突别名与归属）
  - **不投影运行时**（Req 5.10）
  - 单测：增改删启用、业务键重复、别名跨实体冲突、别名内部重复、搜索过滤
  - _Requirements: 5.1–5.10_

- [ ] 7. 批量导入 / 导出 / 模板（`importer.py`）
- [ ] 7.1 解析器
  - `.xlsx`（openpyxl 只读）/ `.csv`（标准库，UTF-8 兼容 BOM）解析为条目列表；列结构校验
  - 单测：正常解析、列不匹配拒绝、空行跳过、别名/同义词逗号拆分与去空白
  - _Requirements: 6.1, 6.7_
- [ ] 7.2 导入执行（逐行 upsert + 报告）
  - 逐行按 Business_Key 调 Service create/update；单行失败记入 `Import_Report`，不中断；软通道成功行触发同步
  - 单测：部分失败逐行报告、成功/失败/重复计数与实际写入一致、软通道导入触发同步
  - _Requirements: 6.3, 6.4, 6.5, 6.6_
- [ ] 7.3 模板下载 + 导出
  - 生成同列结构 Excel 模板；导出（可带关键词过滤），列结构与模板一致
  - 单测：模板列头正确、导出内容可回填、过滤生效
  - _Requirements: 6.2, 7.1, 7.2, 7.3_

- [ ] 8. 鉴权占位 `AuthGuard`（`auth.py`）
  - `PlaceholderAuthGuard.require_admin(request)`：占位放行/读占位角色；非管理员抛 Forbidden/Unauthorized；预留真实 OAuth 扩展点
  - 单测：管理员放行、非管理员被拒
  - _Requirements: 2.1, 2.2, 2.3_

- [ ] 9. 可观测 / 审计 `AdminObserver`（`observer.py`）
  - 记录写操作（类型/业务键/操作者/时间）；日志脱敏
  - 单测：各写操作产出审计记录、脱敏
  - _Requirements: 9.1, 9.2_

- [ ] 10. FastAPI 路由与应用装配（`api.py` / `app.py`）
- [ ] 10.1 路由
  - glossary 与 entities 两组路由（§5 端点表）；写操作依赖 AuthGuard；统一响应信封 + 异常处理器（错误→信封）
  - _Requirements: 1.2, 9.3_
- [ ] 10.2 装配 `build_admin_app`（DI，可注入 mock）
  - 未注入用真实实现（DynamoDB/Bedrock/OpenSearch）；测试注入 fake
  - _Requirements: 8.4, 10.2, 10.3_
- [ ] 10.3 API 测试（FastAPI TestClient + mock 依赖）
  - 各端点状态码/信封、鉴权拦截、分页、导入返回报告
  - _Requirements: 1.1, 1.2, 2.1_

- [ ] 11. 建表脚本 `scripts/create_knowledge_tables.py`
  - 创建 `knowledge_glossary` / `knowledge_glossary_name` / `knowledge_entity` / `knowledge_alias_index`（PAY_PER_REQUEST）
  - 幂等；不在 pytest 套件内
  - _Requirements: 8.1_

- [ ] 12. 属性测试（hypothesis）`tests/knowledge_admin/test_properties.py`
  - P1 业务键唯一、P2 别名全局唯一、P5 导入逐行独立且计数一致
  - _Requirements: 3.6, 5.7, 5.9, 6.4, 6.5_

- [ ] 13. 集成测试（mock AWS）`tests/knowledge_admin/test_integration.py`
  - `build_admin_app` + fake DynamoDB/Bedrock/OpenSearch，串「导入 → 软通道同步 → 查询」「实体导入 → 别名冲突」
  - _Requirements: 4.1, 6.6, 5.9_

- [ ] 14. 依赖与配置
  - pyproject 增加 `admin` extra：`fastapi`、`openpyxl`、（测试）`httpx`（TestClient）
  - `AwsSettings` 增加知识表名配置项（表名可环境变量覆盖）
  - `.env.example` 增补知识表名
  - _Requirements: 8.1, 10.3_

- [ ] 15. spec 联动更新（非本模块代码）
  - 更新 `.kiro/specs/query-engine/`：注明软/硬通道数据来源与管理面同步（软通道 glossary 命名空间由 knowledge-admin 权威写入；硬通道 EntityDictionary 数据由 knowledge-admin 维护，运行时匹配器为未来工作）
  - 更新架构图 `docs/`：新增"知识管理面（knowledge-admin）"组件及其与 DynamoDB/OpenSearch、L3 的连线
  - _Requirements: 附录 A_

## 未来工作（本期不做，记录在案）

- 实体从业务库/数仓自动同步（管理员在其上维护别名）。
- 管理数据的模糊搜索改为 OpenSearch 支撑（替换 Scan+内存过滤）。
- 真实管理员鉴权（阳光云 OAuth admin 角色/scope）。
- 数据源级作用域细分；entity_type 受控枚举。
- 前端管理 UI。
