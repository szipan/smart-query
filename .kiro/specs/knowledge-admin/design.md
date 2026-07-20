# Design Document — knowledge-admin（术语/实体知识管理后端）

## 1. 概述与目标

`knowledge-admin` 是独立于查询链路的**管理面**服务，供系统管理员维护术语归一化两条通道的基础数据。本期只实现**后端**（FastAPI），前后端分离。

三大设计目标：

- **抽象 / 可测试**：存储、向量化、向量库、鉴权全部以 Protocol 注入，单测用内存/mock 替换，不连真实外部服务（遵守测试约定）。
- **权威 + 派生一致**：DynamoDB 为权威存储；软通道写入需同步投影到向量库（OpenSearch `glossary` 命名空间），失败则整体失败、不留不一致（Req 4.4，强一致优先）。
- **借鉴不抄码**：接口与批量导入工程做法借鉴 SQLBot `apps/terminology`（GPLv3），clean-room 重写；Web 框架同为 FastAPI。

### 1.1 关键决策（已与用户确认）

| 项 | 决策 |
|---|---|
| 归属 | 独立模块 `src/knowledge_admin/`，管理面，不改查询链路 |
| 权威存储 | DynamoDB（Term_Store / Entity_Store / Alias_Index） |
| 软通道同步 | 写入 → embed → upsert 向量库；删除/停用 → 删索引；失败整体回滚 |
| 硬通道 | 纯手动维护；**存储形态 A**（一个实体一行 + 别名列表）；**别名跨实体全局唯一**（本期即校验）；运行时匹配器（Aho-Corasick）为未来工作，加载时展开为扁平「别名→实体」 |
| 业务键 | 软通道 = `term`；硬通道 = `(entity_type, entity_id)` |
| entity_type | 自由文本（本期不设枚举） |
| 批量导入 | Excel(.xlsx) + CSV；模板下载；逐行 upsert；逐行成功/失败报告 |
| 鉴权 | 占位实现，预留 admin 角色校验扩展点 |
| 交付 | 代码 + 单测（+ mock 集成测试）；本期不部署、无前端 |

## 2. 模块结构

```
src/knowledge_admin/
  __init__.py
  models.py         # GlossaryTerm / EntityEntry 数据模型 + 校验
  errors.py         # AdminError 体系（映射 HTTP 状态）
  deps.py           # Protocol：TermStore / EntityStore / AliasIndex / Embedder / VectorStore / GlossarySync / AuthGuard / AdminObserver
  service.py        # GlossaryService / EntityService（业务逻辑，DI）
  sync.py           # GlossaryIndexSync（embed + 向量库 upsert/delete）
  importer.py       # 批量导入解析（Excel/CSV）+ 模板生成 + 导出
  auth.py           # AuthGuard 占位实现
  observer.py       # AdminObserver 审计（内存/日志）
  dynamodb_stores.py# DynamoDbTermStore / DynamoDbEntityStore / DynamoDbAliasIndex
  api.py            # FastAPI 路由（glossary + entities）
  app.py            # FastAPI 应用组装 + 依赖装配（wiring）

scripts/
  create_knowledge_tables.py   # 建 DynamoDB 表（Term / Entity / AliasIndex）

tests/knowledge_admin/
  fakes.py                     # 内存 TermStore/EntityStore/AliasIndex + FakeEmbedder/FakeVectorStore
  test_glossary_service.py
  test_entity_service.py       # 含别名唯一性
  test_sync.py
  test_importer.py             # Excel/CSV 解析 + 逐行报告
  test_auth.py
  test_api.py                  # FastAPI TestClient（mock 依赖）
  test_properties.py           # 唯一性/一致性属性测试
```

> `Embedder` / `VectorStore` 复用 `src/adapters/`（Bedrock / OpenSearch）的真实实现；`knowledge_admin/deps.py` 只声明本服务所需的最小 Protocol，避免与 L3/L4 强耦合。

## 3. 数据模型（`models.py`）

```python
@dataclass
class GlossaryTerm:
    term: str                      # 标准词（软通道 Business_Key）
    synonyms: list[str]            # 同义词
    definition: str                # 业务口径定义
    enabled: bool = True
    term_id: Optional[str] = None  # 服务端生成（uuid）
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

@dataclass
class EntityEntry:
    canonical: str                 # 标准名（必填）
    entity_type: str               # 实体类型，自由文本（必填）
    entity_id: str                 # 标准 id（必填）；(entity_type, entity_id) = Business_Key
    aliases: list[str]             # 别名/同义词（可空）
    enabled: bool = True
    entry_id: Optional[str] = None # 服务端生成（uuid）
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
```

**校验规则（`models.py` 内）**：

- 软通道：`term` 非空；`synonyms` 去空白去重；`definition` 可空。
- 硬通道：`canonical` / `entity_type` / `entity_id` 非空；`aliases` 去空白、**列表内去重**；别名不得与本条 `canonical` 相同（避免自指冲突，可选规则，设计中确认）。
- 归一化：别名/术语做统一的 `normalize()`（去首尾空白、大小写与全半角归一，供唯一性判断），原文与归一化值都保留（原文展示、归一化值查重/匹配）。

## 4. 存储设计（DynamoDB）

### 4.1 Term_Store（软通道）

- 表名：`knowledge_glossary`（可配置）
- 主键：`PK = term_id`（分区键）
- 业务键唯一：`term` 唯一。用 **Alias/Name 索引方式**保证——引入一个"名字占用"条件：以 `GSI: term_normalized` 或在写入时用一条 `NameGuard` 记录（PK=`TERM#{term_normalized}`）做 `attribute_not_exists` 条件写，保证 term 不重复。设计采用**同表哨兵项**或独立轻量表二选一；本设计采用**独立 `knowledge_glossary_name` 名占用表**（PK=`term_normalized`, owner=term_id），与硬通道 Alias_Index 同构，逻辑统一。

### 4.2 Entity_Store（硬通道）

- 表名：`knowledge_entity`（可配置）
- 主键：`PK = f"{entity_type}#{entity_id}"`（即 Business_Key，天然唯一；创建用 `attribute_not_exists(PK)` 条件写防重）
- 另存 `entry_id`（uuid）与全部字段；`entry_id` 仅作对外稳定标识与审计。

### 4.3 Alias_Index（硬通道别名全局唯一 + 未来匹配加载源）

- 表名：`knowledge_alias_index`（可配置）
- 主键：`PK = alias_normalized`（全局唯一）
- 属性：`owner_pk`（= `{entity_type}#{entity_id}`）、`entity_type`、`entity_id`、`canonical`、`alias_original`
- 作用：① 写入时以 `attribute_not_exists(PK) OR owner_pk = 本实体` 条件写，实现**别名跨实体全局唯一**；② 未来 Aho-Corasick 匹配器可**直接扫此表**构建扁平「别名→实体」自动机（无需展开列表）。

### 4.4 写入原子性（硬通道）

用 DynamoDB **TransactWriteItems** 一次性完成，保证要么全成、要么全不改：

- 新建：`Put(entity item, ConditionExpression=attribute_not_exists(PK))` + 每个别名 `Put(alias_index, ConditionExpression=attribute_not_exists OR owner=self)`。
- 更新：对比新旧别名集合 → 新增别名做条件 Put、移除别名做 Delete、更新 entity item；全部放进一个事务。
- 事务因条件失败 → 映射为「业务键重复」或「别名冲突」错误，指明冲突项。

> 说明：DynamoDB 单事务最多 100 项，别名极多的实体需分批（设计中记为边界，本期单实体别名数以百计足够）。

### 4.5 列表/搜索的取舍（诚实说明）

DynamoDB 不支持 `LIKE` 模糊搜索。本期数据为**人工维护、量级小**，故：

- 列表/关键词搜索 = **Scan + 应用层子串过滤 + 内存分页**（对小规模可接受）。
- **明确记为可扩展点**：数据量增大后，改为把管理数据也投影到 OpenSearch（管理检索索引）或引入 DynamoDB + 搜索服务；不影响接口契约。

## 5. API 设计（`api.py`，FastAPI）

统一响应信封 `Response_Envelope`：成功 `{status:"ok", data:...}`；失败 `{status:"error", error:{code, message, detail?}}`。

### 5.1 软通道 `/admin/glossary`

| 方法 & 路径 | 说明 |
|---|---|
| `GET /admin/glossary?word=&current_page=&page_size=` | 分页 + 关键词模糊搜索 → Page_Result |
| `PUT /admin/glossary` | 合并的增改：body 无 term_id → 新建；有 → 更新 |
| `DELETE /admin/glossary`（body: id 列表） | 批量删除 |
| `POST /admin/glossary/{term_id}/enable/{enabled}` | 启用/停用 |
| `POST /admin/glossary/import`（multipart file） | 批量导入（Excel/CSV）→ Import_Report |
| `GET /admin/glossary/export?word=` | 导出 Excel |
| `GET /admin/glossary/template` | 下载导入模板 |

### 5.2 硬通道 `/admin/entities`

| 方法 & 路径 | 说明 |
|---|---|
| `GET /admin/entities?keyword=&entity_type=&current_page=&page_size=` | 分页 + 关键词（canonical/alias）搜索 + 类型过滤 |
| `PUT /admin/entities` | 合并的增改（无 entry_id 新建 / 有则更新）|
| `DELETE /admin/entities`（body: id 列表） | 批量删除 |
| `POST /admin/entities/{entry_id}/enable/{enabled}` | 启用/停用 |
| `POST /admin/entities/import`（multipart file） | 批量导入 → Import_Report |
| `GET /admin/entities/export?keyword=&entity_type=` | 导出 Excel |
| `GET /admin/entities/template` | 下载导入模板 |

每个写操作先过 `AuthGuard`（FastAPI 依赖），再进 Service。

## 6. 软通道向量库同步（`sync.py` · GlossaryIndexSync）

复用 `adapters` 的 `Embedder`（Titan）与 `VectorStore`（OpenSearch，`glossary` 命名空间）。索引条目 `entry_id` 字段承载 `term_id`（可回溯，Req 4.3）。被索引文本 = `term + " " + synonyms + " " + definition`。

**写入（create/update 且 enabled）顺序（强一致，Req 4.4）**：

1. 校验 + term 唯一性检查（名占用表条件写）。
2. `embed(text)`（无副作用；失败 → 直接报错，未写任何存储）。
3. `Term_Store.put`（权威）。
4. `VectorStore.upsert(glossary, {entry_id=term_id, term, synonyms, definition, vector})`。
5. IF 步骤 4 失败 → **补偿**：回滚步骤 3（新建则删除该项；更新则恢复旧值）+ 释放名占用（若新建）→ 返回错误。

**删除 / 停用**：先 `VectorStore.delete(glossary, term_id)`，成功后再改/删 `Term_Store`；若向量删除失败 → 中止（权威未变，仍一致）。残留边界：向量已删而权威删除失败 → 表现为"存在但不召回"（偏向不召回，安全侧），记为已知边界并记 warning。

> 硬通道**不做**向量同步（Req 5.10）。

## 7. 批量导入 / 导出 / 模板（`importer.py`）

- **解析**：`.xlsx` 用 `openpyxl`（只读模式）；`.csv` 用标准库 `csv`（UTF-8，兼容 BOM）。
- **列结构**：
  - 软通道：`标准词 | 同义词(逗号分隔) | 定义 | 启用(Y/N)`
  - 硬通道：`标准名 | 实体类型 | 标准id | 别名(逗号分隔) | 启用(Y/N)`
- **语义**：逐行解析 → 逐行校验 → 按 Business_Key `upsert`（复用 Service 的 create/update）；单行失败记入 `Import_Report.failed`（含行号、原始值、错误原因），不中断。
- **Import_Report**：`{success_count, updated_count, failed_count, duplicate_count, failures:[{row, data, error}]}`。
- **模板 / 导出**：生成同列结构的 Excel（`openpyxl` 写）；导出可带关键词过滤。
- 软通道导入成功行触发 `GlossaryIndexSync`（逐行或批量）。

> 依赖：`openpyxl`（.xlsx 读写）。CSV 用标准库。加入 pyproject 的 `aws`/新增 `admin` extra。

## 8. 鉴权占位（`auth.py` · AuthGuard）

```python
class AuthGuard(Protocol):
    def require_admin(self, request) -> AdminPrincipal: ...   # 非管理员 -> 抛 Unauthorized/Forbidden

class PlaceholderAuthGuard:
    """本期占位：从 header 读取占位管理员标识；预留未来接阳光云 OAuth admin 角色的扩展点。"""
```

- 作为 FastAPI 依赖注入到所有写操作路由。
- 占位策略与真实策略同接口，未来替换不改路由与 Service。
- 单测覆盖：非管理员被拒且不触达 Service。

## 9. 错误处理（`errors.py`）

| 错误 | HTTP | 触发 |
|---|---|---|
| `ValidationError` | 400 | 字段校验失败 / 文件格式或列不匹配 |
| `DuplicateKeyError` | 409 | 业务键重复（term / (entity_type,entity_id)）|
| `AliasConflictError` | 409 | 别名被其他实体占用（含冲突别名与归属）|
| `NotFoundError` | 404 | 更新/删除/启用不存在的条目 |
| `Unauthorized` / `Forbidden` | 401 / 403 | 鉴权失败 |
| `IndexSyncError` | 502 | 向量化/索引写入失败（写操作整体失败）|

统一由 FastAPI 异常处理器转为 `Response_Envelope`。

## 10. 依赖注入与装配（`app.py`）

```python
def build_admin_app(settings: AwsSettings, *, term_store=None, entity_store=None,
                    alias_index=None, embedder=None, vector_store=None,
                    auth_guard=None, observer=None) -> FastAPI:
    # 未注入则用真实实现（DynamoDB / Bedrock / OpenSearch）；测试注入 fake
```

- 与 `src/wiring.py` 同风格（可注入 mock，便于测试）。
- Service 组合：`GlossaryService(term_store, name_guard, GlossaryIndexSync(embedder, vector_store), observer)`、`EntityService(entity_store, alias_index, observer)`。

## 11. 正确性属性（Correctness Properties）

- **P1 业务键唯一**：任意写入序列后，Term_Store 中 term 唯一、Entity_Store 中 (entity_type, entity_id) 唯一。
- **P2 别名全局唯一**：任意写入序列后，任一 alias_normalized 至多归属一个实体。
- **P3 软通道读写一致**：一条 enabled 的 Glossary_Term 成功写入后，向量库 `glossary` 存在可回溯该 term_id 的条目；被删/停用后，向量库不再含该条目；写操作失败则权威与索引均不改变。
- **P4 硬通道不投影**：硬通道写入只改 Entity_Store / Alias_Index，不触达向量库。
- **P5 批量导入逐行独立**：任一行失败不影响其他行的成功写入，且 Import_Report 计数与实际写入一致。
- **P6 鉴权前置**：非管理员的写操作被拒且不产生任何数据变更。
- **P7 层边界**：本服务不产生 SQL、不做隔离/执行。

## 12. 测试策略

- **单元测试**：每个 Service/importer/sync/auth 用内存 fake（`fakes.py`）注入，覆盖正常/边界/失败路径（重复键、别名冲突、同步失败回滚、导入部分失败、鉴权拒绝）。
- **属性测试（hypothesis）**：P1/P2/P5（随机写入序列验证唯一性与导入计数一致）。
- **API 测试**：FastAPI `TestClient` + mock 依赖，验证路由、状态码、信封。
- **集成测试（mock AWS）**：`build_admin_app` + fake DynamoDB/Bedrock/OpenSearch client，串起「导入 → 同步 → 查询」。
- 真实 AWS 冒烟：单独脚本（不进 pytest 套件），可选。

## 12A. 硬通道运行时匹配器 Entity_Matcher（`src/adapters/entity_dictionary.py`）

查询面组件，实现 Layer 3 的 `EntityDictionary` 契约（`match(question) -> list[dict]`），是 Alias_Index 的运行时消费者。**纯 Python 手写 Aho-Corasick**（零第三方依赖、可移植、易测）。

### 12A.1 数据加载

- 从 `knowledge_alias_index` 扫描全部 `{alias_normalized, canonical, entity_type, entity_id, alias_original}`，构建 Aho-Corasick 自动机。
- 归一化：匹配前对问题文本用**与写入 Alias_Index 相同的 `normalize()`**（共享 `knowledge_admin.models.normalize`，避免 admin 与 adapters 各写一份 → 该函数下沉为可被两侧 import 的公共工具）。
- **最小别名长度阈值**（默认 2）：低于阈值的别名不入自动机，降低短词误命中。

### 12A.2 匹配与消解

- 自动机对归一化问题串扫描，得到所有命中区间 `(start, end, entry)`。
- **消解：最长匹配、非重叠、从左到右**——按起点升序、同起点取最长；已覆盖区间不再重叠命中。
- 每个最终命中产出 `{surface: alias_original, canonical, entity_type, entity_id}`（与 `HardEntityResolver.resolve` 读取的字段对齐）。

### 12A.3 刷新（TTL 重建）

- 启动时加载一次；持有 `loaded_at`，`match()` 前若超过 TTL（默认 300s）则后台/惰性重建。
- 暴露显式 `refresh()` 方法（供管理端触发或运维调用）。
- 重建失败保留上一份可用自动机（不因刷新失败而清空）。

### 12A.4 降级

- 构造/加载时若 DynamoDB 不可用或 Alias_Index 为空 → 自动机为空，`match()` 返回 `[]`，**不抛出**（与当前 wiring 中 `entity_dictionary=None`、硬通道恒空的行为等价，不阻断查询）。

### 12A.5 L3 接线

- `wiring.build_engine` 将 `entity_dictionary` 从 `None` 换为 `EntityMatcher`（可注入，测试用 fake / 空自动机）。
- L3 `HardEntityResolver` 无需改动（它已按 `EntityDictionary.match` 契约调用）。

### 12A.6 复杂度

- 构建 O(∑别名长度)；单次 `match` O(问题长度 + 命中数)。规模（人工维护、至多数十万别名）完全可承受；内存自动机。

## 13. 与现有系统的衔接 / 非目标

- **衔接**：软通道写入的向量库命名空间 `glossary` 即 Layer 3 `SoftGlossaryResolver` 读取处，本服务取代 `scripts/ingest_opensearch.py` 中的术语手写种子；复用 `adapters` 的 Embedder/VectorStore/AwsSettings。
- **本期范围内**：硬通道运行时匹配器 Entity_Matcher（Aho-Corasick，§12A）并接入 L3 wiring。
- **非目标**：前端 UI；实体从业务库自动同步；云端部署；数据源级作用域细分；entity_type 受控枚举；管理数据的模糊搜索改 OpenSearch。
