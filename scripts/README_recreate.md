# 资源重建指南（Recreation Guide）

本文档说明如何在执行 `teardown_all.py` 删除全部 AWS 测试资源后，重新创建它们。

## 前提条件

1. AWS 凭证已配置（`~/.aws/credentials` 或环境变量），具有 us-west-2 区域的以下权限：
   - `aoss:*`（OpenSearch Serverless）
   - `dynamodb:CreateTable / DeleteTable / DescribeTable / UpdateTimeToLive`
   - `bedrock-agentcore-control:*`（AgentCore Memory）
   - `sts:GetCallerIdentity`
   - `ec2:AuthorizeSecurityGroupIngress`（可选，仅 VPC-only 模式）
2. Python 虚拟环境已激活，依赖已安装：
   ```powershell
   .\.venv\Scripts\activate
   pip install -e ".[dev,aws,admin]"
   ```
3. 环境变量 `AWS_REGION=us-west-2` 已设置。

## 重建步骤（按顺序执行）

### Step 1: 创建 OpenSearch Serverless 集合

```powershell
python scripts/create_aoss_collection.py --name smart-query --region us-west-2
```

输出示例：
```
SMARTQUERY_OPENSEARCH_ENDPOINT=https://xxxxx.us-west-2.aoss.amazonaws.com
```

将输出的 endpoint 更新到 `.env` 文件的 `SMARTQUERY_OPENSEARCH_ENDPOINT`。

### Step 2:（可选）切换为 VPC-only 网络策略

如需通过 VPC 端点访问（企业网络环境）：

```powershell
python scripts/set_aoss_network.py --vpce vpce-0a0e0d10ba02c9bda --name smart-query
```

### Step 3:（可选）放行安全组入站 443

```powershell
python scripts/allow_aoss_sg.py --sg sg-0ac149b2678da1aa1 --cidr 172.31.0.0/16
```

### Step 4: 创建 DynamoDB 限流表

```powershell
python scripts/create_dynamodb_table.py --table api_gateway_rate_limit --region us-west-2
```

### Step 5: 创建知识管理表（4 张）

```powershell
python scripts/create_knowledge_tables.py --region us-west-2
```

### Step 6: 创建 AgentCore Memory

```powershell
python scripts/create_agentcore_memory.py --name smart_query_memory --region us-west-2
```

输出示例：
```
SMARTQUERY_MEMORY_ID=smart_query_memory-xxxxxxxx
```

将输出的 memory id 更新到 `.env` 文件的 `SMARTQUERY_MEMORY_ID`。

### Step 7: 灌注 OpenSearch 演示数据

```powershell
python scripts/ingest_opensearch.py
```

> 注意：执行前确保 `.env` 中的 `SMARTQUERY_OPENSEARCH_ENDPOINT` 已更新为 Step 1 的输出值。

## 一键重建（快速版）

如果不需要 VPC-only 模式，可按以下顺序一次执行：

```powershell
$env:AWS_REGION="us-west-2"

python scripts/create_aoss_collection.py --name smart-query
# ↑ 记录输出的 endpoint，更新 .env

python scripts/create_dynamodb_table.py
python scripts/create_knowledge_tables.py
python scripts/create_agentcore_memory.py
# ↑ 记录输出的 memory_id，更新 .env

python scripts/ingest_opensearch.py
```

## 需要手动更新的配置

重建后以下 `.env` 值会变化（资源 ID/endpoint 是新的）：

| 环境变量 | 来源 |
|---------|------|
| `SMARTQUERY_OPENSEARCH_ENDPOINT` | `create_aoss_collection.py` 输出 |
| `SMARTQUERY_MEMORY_ID` | `create_agentcore_memory.py` 输出 |

其余变量（表名、模型 ID、索引名等）保持不变，无需修改。

## 删除资源

```powershell
python scripts/teardown_all.py          # 交互确认
python scripts/teardown_all.py --yes    # 跳过确认直接删除
```

## 注意事项

- 所有创建脚本均幂等（已存在则跳过），重复执行安全。
- AOSS 集合创建需 1-3 分钟等待 ACTIVE 状态。
- Bedrock 模型（Claude / Titan）无需创建，属于账号级按需服务。
- 知识表中已灌入的业务数据（术语、实体）需重新导入（通过 knowledge-admin API 或手动脚本）。
- OpenSearch 中的向量数据由 `ingest_opensearch.py` 补回演示数据；如有额外业务数据需另行恢复。
