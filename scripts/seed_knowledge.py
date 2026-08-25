"""填充 soft channel (术语库) 和 hard channel (实体字典) 的测试数据。

基于 sungrow sql ddl.docx 中的真实表结构和字段注释。
用法：  python scripts/seed_knowledge.py [--region us-west-2]
依赖：  需 SMARTQUERY_OPENSEARCH_ENDPOINT 已设（向量同步）。
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import boto3
from adapters.bedrock import BedrockEmbedder
from adapters.opensearch import OpenSearchVectorStore, make_opensearch_client
from adapters.settings import AwsSettings


# ─── Soft Channel: 术语库 (knowledge_glossary) ────────────────────────────────
# 每条术语会写入 DynamoDB + 向量化后写入 OpenSearch glossary namespace

GLOSSARY_TERMS = [
    {
        "term": "发电量",
        "definition": "电站KPI表核心指标。p83022=日发电量, p83037=月发电量, p83038=年发电量, p83024=累计发电量，单位kWh。月表 fact_power_station_kpi_month，年表 fact_power_station_kpi_year。",
        "synonyms": ["产电量", "发电", "总发电", "generation", "日发电量"],
    },
    {
        "term": "装机容量",
        "definition": "fact_organization_kpi_month/year 表字段 p83202，表示电站额定装机容量，单位kWp。",
        "synonyms": ["容量", "额定容量", "总容量", "installed capacity"],
    },
    {
        "term": "等效小时",
        "definition": "字段 p83025，电站日发电量等效小时数，反映电站利用率。满发小时数越高说明发电效率越好。",
        "synonyms": ["满发小时", "利用小时数", "等效利用小时", "full load hours"],
    },
    {
        "term": "收益",
        "definition": "profit=月收入, profit_sum=累计收入。尖峰谷平表中 total_profit=总收益(用电折扣后收益+补贴收益+上网收益), net_power_profit=上网收益, use_power_profit=用电收益。",
        "synonyms": ["收入", "电费收入", "赚了多少", "月收入", "总收益"],
    },
    {
        "term": "上网电量",
        "definition": "卖给电网的电量。尖峰谷平表 net_power_quantity_total=上网电量合计(尖+峰+谷+平)。储能表 p83072/p83073/p83074 为储能逆变器上网电量。",
        "synonyms": ["卖电", "上网", "馈网电量", "上网度数"],
    },
    {
        "term": "用电量",
        "definition": "从电网取电量。尖峰谷平表 use_power_quantity_total=用电量合计。p83102=日电网取电电量, p83103=月电网取电电量, p83104=年电网取电电量。p83011=电表日用电量。",
        "synonyms": ["耗电", "消耗", "电网取电", "购电量", "买电"],
    },
    {
        "term": "充电量",
        "definition": "储能电池充电量。p83088=电池日充电量, p83243=日充电量, p83314=电站日总充电量, p83312=电站总充电量。",
        "synonyms": ["充电", "储能充电", "电池充电"],
    },
    {
        "term": "放电量",
        "definition": "储能电池放电量。p83089=电池日放电量, p83244=日放电量, p83091=电池月放电量, p83093=电池年放电量。",
        "synonyms": ["放电", "储能放电", "电池放电"],
    },
    {
        "term": "自发自用率",
        "definition": "p83121，自己发的电自己用掉的比例。越高说明光伏自消纳越好。",
        "synonyms": ["自用率", "自消纳率"],
    },
    {
        "term": "自给自足率",
        "definition": "p83122，用电中由光伏自供给的比例。越高说明对电网依赖越少。",
        "synonyms": ["自足率"],
    },
    {
        "term": "碳减排",
        "definition": "p83034=累计减排, co2_reduce=月/年二氧化碳节能减排(单位g), co2_reduce_sum=累计。",
        "synonyms": ["CO2减排", "二氧化碳减排", "减排", "节能减排", "碳排放"],
    },
    {
        "term": "功率",
        "definition": "p83033=电站功率(kW), p83002=逆变器交流功率, p83032=电表交流功率。p83001=逆变器交流功率归一化。",
        "synonyms": ["电站功率", "当前功率", "实时功率", "交流功率"],
    },
    {
        "term": "PR",
        "definition": "Performance Ratio 系统效率。p83023=电站PR, p83010=逆变器PR, p83007=电表PR。越高说明系统损耗越小。",
        "synonyms": ["系统效率", "性能比", "performance ratio"],
    },
    {
        "term": "辐照",
        "definition": "太阳辐射量。p83012=平面瞬时辐照, p83013=平面日辐射量, p83014=斜面瞬时辐照, p83015=斜面日辐射量, p83036=平面总辐照。",
        "synonyms": ["辐射量", "日照", "太阳能辐射", "irradiance"],
    },
]


# ─── Hard Channel: 实体字典 (knowledge_entity) ────────────────────────────────
# 确定性 Aho-Corasick 匹配：用户说别名 → 系统立即解析为 canonical

ENTITY_ENTRIES = [
    # metric 类：指标代码
    {"entity_type": "metric", "entity_id": "p83022", "canonical": "日发电量",
     "aliases": ["发电量", "日发电量", "当日发电", "今天发电"]},
    {"entity_type": "metric", "entity_id": "p83037", "canonical": "月发电量",
     "aliases": ["月发电量", "本月发电量", "这个月发电"]},
    {"entity_type": "metric", "entity_id": "p83038", "canonical": "年发电量",
     "aliases": ["年发电量", "本年发电量", "今年发电"]},
    {"entity_type": "metric", "entity_id": "p83024", "canonical": "累计发电量",
     "aliases": ["累计发电量", "总发电量", "历史总发电"]},
    {"entity_type": "metric", "entity_id": "p83025", "canonical": "等效小时",
     "aliases": ["等效小时", "满发小时", "利用小时数", "等效利用小时数"]},
    {"entity_type": "metric", "entity_id": "p83202", "canonical": "装机容量",
     "aliases": ["装机容量", "容量", "额定容量", "装机"]},
    {"entity_type": "metric", "entity_id": "p83033", "canonical": "电站功率",
     "aliases": ["功率", "电站功率", "当前功率", "实时功率"]},
    {"entity_type": "metric", "entity_id": "profit", "canonical": "月收入",
     "aliases": ["收入", "月收入", "收益", "月收益"]},
    {"entity_type": "metric", "entity_id": "total_profit", "canonical": "总收益",
     "aliases": ["总收益", "总收入", "累计收益"]},
    {"entity_type": "metric", "entity_id": "p83121", "canonical": "自发自用率",
     "aliases": ["自发自用率", "自用率", "自消纳率"]},
    {"entity_type": "metric", "entity_id": "p83122", "canonical": "自给自足率",
     "aliases": ["自给自足率", "自足率"]},
    {"entity_type": "metric", "entity_id": "co2_reduce", "canonical": "碳减排",
     "aliases": ["碳减排", "CO2减排", "二氧化碳减排", "减排"]},

    # table 类：表名
    {"entity_type": "table", "entity_id": "fact_power_station_kpi_month", "canonical": "电站月度KPI表",
     "aliases": ["电站月表", "月度KPI", "电站月度表"]},
    {"entity_type": "table", "entity_id": "fact_power_station_kpi_year", "canonical": "电站年度KPI表",
     "aliases": ["电站年表", "年度KPI", "电站年度表"]},
    {"entity_type": "table", "entity_id": "fact_organization_kpi_month", "canonical": "集团月度KPI表",
     "aliases": ["组织月表", "集团月表", "组织月度表"]},
    {"entity_type": "table", "entity_id": "fact_organization_kpi_year", "canonical": "集团年度KPI表",
     "aliases": ["组织年表", "集团年表", "组织年度表"]},
    {"entity_type": "table", "entity_id": "fact_power_cusp_peak_valley_flat_report_month",
     "canonical": "尖峰谷平月报表",
     "aliases": ["尖峰谷平月表", "电费月表", "分时电价月表", "峰谷月表"]},
    {"entity_type": "table", "entity_id": "fact_power_cusp_peak_valley_flat_report_year",
     "canonical": "尖峰谷平年报表",
     "aliases": ["尖峰谷平年表", "电费年表", "分时电价年表", "峰谷年表"]},
    {"entity_type": "table", "entity_id": "power_station", "canonical": "电站基础信息表",
     "aliases": ["电站表", "电站信息", "电站基本信息"]},

    # product_line 类：电站类型
    {"entity_type": "product_line", "entity_id": "1", "canonical": "户用",
     "aliases": ["户用", "家用", "家庭", "residential", "户用电站", "家庭电站"]},
    {"entity_type": "product_line", "entity_id": "2", "canonical": "工商业",
     "aliases": ["工商业", "工业", "商业", "C&I", "工商业电站"]},
]


# ─── 写入逻辑 ─────────────────────────────────────────────────────────────────

def seed_glossary(ddb, table_name: str, name_table: str, embedder, vector_store):
    """写入 soft channel 术语到 DynamoDB + 向量同步到 OpenSearch。"""
    print("\n[Soft Channel] 写入术语库...")
    for entry in GLOSSARY_TERMS:
        term_id = str(uuid.uuid4())
        item = {
            "pk": {"S": term_id},
            "term": {"S": entry["term"]},
            "definition": {"S": entry["definition"]},
            "synonyms": {"L": [{"S": s} for s in entry["synonyms"]]},
            "enabled": {"BOOL": True},
        }
        # 写入主表
        ddb.put_item(TableName=table_name, Item=item)
        # 写入 name 唯一性表
        ddb.put_item(TableName=name_table, Item={
            "name_key": {"S": entry["term"].lower()},
            "term_id": {"S": term_id},
        })
        # 向量同步到 OpenSearch (glossary namespace)
        text_for_embed = entry["term"] + " " + entry["definition"]
        vec = embedder.embed([text_for_embed])[0]
        vector_store.upsert("glossary", {
            "vector": vec, "text": entry["definition"], "term": entry["term"],
        })
        print(f"  [ok] {entry['term']} (synonyms: {entry['synonyms']})")

    print(f"  共写入 {len(GLOSSARY_TERMS)} 条术语")


def seed_entities(ddb, entity_table: str, alias_table: str):
    """写入 hard channel 实体到 DynamoDB。"""
    print("\n[Hard Channel] 写入实体字典...")
    for entry in ENTITY_ENTRIES:
        business_key = f"{entry['entity_type']}#{entry['entity_id']}"
        item = {
            "pk": {"S": business_key},
            "entity_type": {"S": entry["entity_type"]},
            "entity_id": {"S": entry["entity_id"]},
            "canonical": {"S": entry["canonical"]},
            "aliases": {"L": [{"S": a} for a in entry["aliases"]]},
            "enabled": {"BOOL": True},
        }
        ddb.put_item(TableName=entity_table, Item=item)

        # 写入别名唯一性索引
        for alias in entry["aliases"]:
            ddb.put_item(TableName=alias_table, Item={
                "alias_key": {"S": f"{entry['entity_type']}#{alias}"},
                "entity_pk": {"S": business_key},
                "canonical": {"S": entry["canonical"]},
            })

        print(f"  [ok] {entry['entity_type']}/{entry['entity_id']} → {entry['canonical']} "
              f"(aliases: {entry['aliases']})")

    print(f"  共写入 {len(ENTITY_ENTRIES)} 条实体")


def main() -> int:
    p = argparse.ArgumentParser(description="填充 soft/hard channel 测试数据")
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    args = p.parse_args()

    s = AwsSettings.from_env()
    if not s.opensearch_endpoint:
        print("ERROR: 未设置 SMARTQUERY_OPENSEARCH_ENDPOINT", file=sys.stderr)
        return 2

    ddb = boto3.client("dynamodb", region_name=args.region)
    embedder = BedrockEmbedder(region=args.region, model_id=s.embed_model_id, dimensions=s.embed_dimensions)
    os_client = make_opensearch_client(endpoint=s.opensearch_endpoint, region=args.region)
    vector_store = OpenSearchVectorStore(client=os_client, index=s.opensearch_index)

    seed_glossary(ddb, s.glossary_table, s.glossary_name_table, embedder, vector_store)
    seed_entities(ddb, s.entity_table, s.alias_table)

    print("\n" + "=" * 60)
    print("全部 seed 完成。")
    print(f"  Soft channel (术语库): {len(GLOSSARY_TERMS)} 条")
    print(f"  Hard channel (实体字典): {len(ENTITY_ENTRIES)} 条")
    print("  向量已同步到 OpenSearch glossary namespace。")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
