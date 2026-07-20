"""批量录入术语/实体（含同义词/别名）到真实 DynamoDB/OpenSearch —— 无需起服务。

软通道（glossary）：术语 + 同义词 + 定义；写 DynamoDB 并向量化同步到 OpenSearch glossary。
硬通道（entity）：实体（标准名/类型/标准id）+ 别名（同义词）；写 DynamoDB（别名全局唯一）。

用法：
  # 1) 先下载模板，填好数据
  python scripts/import_knowledge.py --channel glossary --template terms.xlsx
  python scripts/import_knowledge.py --channel entity   --template entities.xlsx

  # 2) 导入（按业务键 upsert：同键则更新，同义词列填完整新集合即可补/改）
  python scripts/import_knowledge.py --channel glossary --file terms.xlsx
  python scripts/import_knowledge.py --channel entity   --file entities.xlsx

环境变量：AWS_REGION、SMARTQUERY_OPENSEARCH_ENDPOINT（软通道必需）、知识表名（默认见 .env.example）。
支持 .xlsx 与 .csv。列结构：
  glossary: 标准词 | 同义词(逗号分隔) | 定义 | 启用(Y/N)
  entity:   标准名 | 实体类型 | 标准id | 别名(逗号分隔) | 启用(Y/N)
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from adapters.settings import AwsSettings
from knowledge_admin import importer


def _write_template(channel: str, path: str) -> None:
    data = importer.glossary_template() if channel == "glossary" else importer.entity_template()
    with open(path, "wb") as f:
        f.write(data)
    print(f"[ok] 模板已写入 {path}")


def _build_glossary_service(s: AwsSettings):
    import boto3
    from adapters.bedrock import BedrockEmbedder
    from adapters.opensearch import OpenSearchVectorStore, make_opensearch_client
    from knowledge_admin.dynamodb_stores import DynamoDbTermStore
    from knowledge_admin.observer import LoggingAdminObserver
    from knowledge_admin.service import GlossaryService
    from knowledge_admin.sync import GlossaryIndexSync

    if not s.opensearch_endpoint:
        raise SystemExit("软通道需要 SMARTQUERY_OPENSEARCH_ENDPOINT")
    ddb = boto3.client("dynamodb", region_name=s.region)
    term_store = DynamoDbTermStore(ddb, s.glossary_table, s.glossary_name_table)
    embedder = BedrockEmbedder(region=s.region, model_id=s.embed_model_id, dimensions=s.embed_dimensions)
    vs = OpenSearchVectorStore(client=make_opensearch_client(endpoint=s.opensearch_endpoint, region=s.region),
                               index=s.opensearch_index)
    return GlossaryService(term_store, GlossaryIndexSync(embedder, vs), LoggingAdminObserver())


def _build_entity_service(s: AwsSettings):
    import boto3
    from knowledge_admin.dynamodb_stores import DynamoDbEntityStore
    from knowledge_admin.observer import LoggingAdminObserver
    from knowledge_admin.service import EntityService

    ddb = boto3.client("dynamodb", region_name=s.region)
    return EntityService(DynamoDbEntityStore(ddb, s.entity_table, s.alias_table), LoggingAdminObserver())


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--channel", required=True, choices=["glossary", "entity"])
    p.add_argument("--file", help="要导入的 .xlsx / .csv 文件")
    p.add_argument("--template", help="写出导入模板到该路径（不导入）")
    p.add_argument("--actor", default="cli-admin")
    args = p.parse_args()

    if args.template:
        _write_template(args.channel, args.template)
        return 0
    if not args.file:
        print("请提供 --file（或用 --template 先下载模板）", file=sys.stderr)
        return 2

    s = AwsSettings.from_env()
    print(f"[config] region={s.region} channel={args.channel} file={args.file}")
    with open(args.file, "rb") as f:
        data = f.read()

    if args.channel == "glossary":
        svc = _build_glossary_service(s)
        report = importer.import_glossary(data, os.path.basename(args.file), svc, args.actor)
    else:
        svc = _build_entity_service(s)
        report = importer.import_entities(data, os.path.basename(args.file), svc, args.actor)

    print("[report]")
    print(json.dumps({k: v for k, v in report.items() if k != "failures"}, ensure_ascii=False, indent=2))
    if report["failures"]:
        print(f"失败 {len(report['failures'])} 行：")
        for fr in report["failures"]:
            print(f"  行{fr['row']}: {fr['error']} | {fr['data']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
