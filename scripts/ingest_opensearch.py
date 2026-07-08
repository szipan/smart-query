"""向 OpenSearch 灌注演示数据：schema 片段 / few-shot 示例 / 术语。

- 建索引（若不存在）：单个 k-NN 索引 + namespace 字段。
- 用 Bedrock Titan 向量化后 upsert 到对应 namespace（schema/examples/glossary）。
- 数据以阳光云真实表结构为蓝本（与 L5 隔离策略登记表对齐，便于生成可隔离的 SQL）。

用法：  python scripts/ingest_opensearch.py
环境：  需 SMARTQUERY_OPENSEARCH_ENDPOINT（见 .env.example）。
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from adapters.bedrock import BedrockEmbedder
from adapters.opensearch import OpenSearchVectorStore, build_index_mapping, make_opensearch_client
from adapters.settings import AwsSettings

# —— schema 片段（与 sungrow_policy 登记的受控表对齐）——
SCHEMA_SNIPPETS = [
    "CREATE TABLE fact_power_station_kpi_month (ps_id INT COMMENT '电站ID', date_id INT COMMENT '统计月 yyyyMMdd', "
    "p83022 DECIMAL(20,3) COMMENT '月发电量kWh', p83025 DECIMAL(20,3) COMMENT '月满发小时') COMMENT '电站月度KPI';",
    "CREATE TABLE fact_power_station_kpi_year (ps_id INT COMMENT '电站ID', date_id INT COMMENT '统计年', "
    "p83022 DECIMAL(20,3) COMMENT '年发电量kWh') COMMENT '电站年度KPI';",
    "CREATE TABLE power_station (ps_id INT COMMENT '电站ID', ps_name VARCHAR(255) COMMENT '电站名称', "
    "install_capacity DECIMAL(20,3) COMMENT '装机容量kWp', build_date INT COMMENT '建站日期') COMMENT '电站基础信息';",
    "CREATE TABLE fact_organization_kpi_month (org_id INT COMMENT '组织ID', date_id INT COMMENT '统计月', "
    "total_power DECIMAL(20,3) COMMENT '组织月发电量kWh') COMMENT '组织月度KPI';",
    "CREATE TABLE bus_points_day (ps_key VARCHAR(64) COMMENT '设备key(前缀为ps_id)', date_id INT, "
    "power DECIMAL(20,3) COMMENT '日发电量kWh') COMMENT '设备日指标';",
]

# —— few-shot 示例（Q -> SQL，均不含权限过滤，隔离交给 L5）——
EXAMPLES = [
    {"question": "本月各电站发电量",
     "sql": "SELECT ps_id, p83022 AS month_power FROM fact_power_station_kpi_month WHERE date_id = 20260701"},
    {"question": "今年各电站总发电量排名",
     "sql": "SELECT ps_id, p83022 AS year_power FROM fact_power_station_kpi_year WHERE date_id = 2026 ORDER BY year_power DESC"},
    {"question": "查询所有电站的装机容量",
     "sql": "SELECT ps_id, ps_name, install_capacity FROM power_station"},
    {"question": "各组织本月发电量",
     "sql": "SELECT org_id, total_power FROM fact_organization_kpi_month WHERE date_id = 20260701"},
]

# —— 术语 ——
GLOSSARY = [
    {"term": "发电量", "definition": "指标字段 p83022，单位 kWh；月表在 fact_power_station_kpi_month，年表在 fact_power_station_kpi_year。"},
    {"term": "装机容量", "definition": "power_station.install_capacity，单位 kWp。"},
]


def main() -> int:
    s = AwsSettings.from_env()
    if not s.opensearch_endpoint:
        print("ERROR: 未设置 SMARTQUERY_OPENSEARCH_ENDPOINT", file=sys.stderr)
        return 2

    client = make_opensearch_client(endpoint=s.opensearch_endpoint, region=s.region)
    embedder = BedrockEmbedder(region=s.region, model_id=s.embed_model_id, dimensions=s.embed_dimensions)
    vs = OpenSearchVectorStore(client=client, index=s.opensearch_index)

    if not client.indices.exists(index=s.opensearch_index):
        client.indices.create(index=s.opensearch_index, body=build_index_mapping(s.embed_dimensions))
        print(f"[create] index {s.opensearch_index}")
        time.sleep(5)  # AOSS 索引就绪有短暂延迟
    else:
        print(f"[skip] index {s.opensearch_index} 已存在")

    n = 0
    for text in SCHEMA_SNIPPETS:
        vs.upsert("schema", {"vector": embedder.embed([text])[0], "text": text})
        n += 1
    for ex in EXAMPLES:
        vec = embedder.embed([ex["question"]])[0]
        vs.upsert("examples", {"vector": vec, "question": ex["question"], "sql": ex["sql"]})
        n += 1
    for g in GLOSSARY:
        vec = embedder.embed([g["term"] + " " + g["definition"]])[0]
        vs.upsert("glossary", {"vector": vec, "text": g["definition"], "term": g["term"]})
        n += 1

    print(f"[ok] 已灌注 {n} 条（schema={len(SCHEMA_SNIPPETS)}, examples={len(EXAMPLES)}, glossary={len(GLOSSARY)}）")
    print("[note] AOSS 为最终一致，检索可见性可能有数秒延迟。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
