"""OpenSearch (Serverless) 向量库适配器（实现 L3/L4 ``VectorStore`` Protocol）。

设计（参考 SQLBot「向量召回 + 阈值」思路，clean-room 重写；SQLBot 用 pgvector，这里用
OpenSearch k-NN）：
- **单索引 + namespace 字段**（fewest resources）：schema / examples / semantic_cache /
  glossary 共用一个 k-NN 索引，用 ``namespace`` keyword 过滤，避免多索引/多集合。
- 向量字段 ``vector``（knn_vector，维度 1024，faiss/HNSW，space_type l2）。Titan v2 归一化
  向量下，L2 与余弦单调对应；``similarity`` 由 ``_score`` 反解为余弦（供缓存阈值比较）。
- 底层 opensearch-py client 依赖注入，单测用 mock，不连真实服务。
- AOSS 认证为 SigV4（service 名 ``aoss``），无匿名访问、无密码留空。

契约方法：``similarity_search`` / ``upsert`` / ``delete``。
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

VECTOR_FIELD = "vector"
NAMESPACE_FIELD = "namespace"
ENTRY_ID_FIELD = "entry_id"
DEFAULT_INDEX = "smartquery-vectors"
DEFAULT_DIMENSION = 1024


def build_index_mapping(dimension: int = DEFAULT_DIMENSION) -> dict[str, Any]:
    """单个 k-NN 索引的 mapping（AOSS 向量集合可用 faiss/HNSW）。"""
    return {
        "settings": {"index": {"knn": True}},
        "mappings": {
            "properties": {
                VECTOR_FIELD: {
                    "type": "knn_vector",
                    "dimension": dimension,
                    "method": {
                        "name": "hnsw",
                        "engine": "faiss",
                        "space_type": "l2",
                    },
                },
                NAMESPACE_FIELD: {"type": "keyword"},
                ENTRY_ID_FIELD: {"type": "keyword"},
                "text": {"type": "text"},
                "question": {"type": "text"},
                "sql": {"type": "text"},
                "pre_isolation_sql": {"type": "text"},
            }
        },
    }


def _l2_score_to_cosine(score: float) -> float:
    """OpenSearch l2 knn: score = 1/(1+d)（d 为平方 L2）。

    归一化向量下 d = 2(1-cos) => cos = 1 - d/2。做区间裁剪，防浮点越界。
    """
    if score <= 0:
        return -1.0
    d = (1.0 / score) - 1.0
    cos = 1.0 - d / 2.0
    return max(-1.0, min(1.0, cos))


class OpenSearchVectorStore:
    def __init__(self, *, client: Any, index: str = DEFAULT_INDEX) -> None:
        self._client = client
        self._index = index

    # ---------- 检索 ----------
    def similarity_search(self, vector: list[float], top_k: int,
                          namespace: str) -> list[dict[str, Any]]:
        body = {
            "size": top_k,
            "query": {
                "knn": {
                    VECTOR_FIELD: {
                        "vector": list(vector),
                        "k": top_k,
                        "filter": {"term": {NAMESPACE_FIELD: namespace}},
                    }
                }
            },
            "_source": {"excludes": [VECTOR_FIELD]},
        }
        resp = self._client.search(index=self._index, body=body)
        hits = resp.get("hits", {}).get("hits", [])
        results: list[dict[str, Any]] = []
        for h in hits:
            src = dict(h.get("_source", {}))
            score = float(h.get("_score", 0.0))
            src["score"] = score
            src["similarity"] = _l2_score_to_cosine(score)
            src.setdefault(ENTRY_ID_FIELD, h.get("_id"))
            results.append(src)
        return results

    # ---------- 写入 ----------
    # AOSS VECTORSEARCH 限制：不支持自定义 _id、不支持按外部 id 删除、不支持 delete_by_query。
    # 因此：index 用自动 _id；删除/覆盖时先按 (namespace, entry_id) search 出自动 _id，再按 _id 删。
    # 注意：AOSS 为最终一致，刚写入的文档短时间内可能搜不到，故「写后立即删/覆盖」为尽力而为。
    def _find_ids(self, namespace: str, entry_id: str) -> list[str]:
        body = {
            "size": 100,
            "query": {"bool": {"filter": [
                {"term": {NAMESPACE_FIELD: namespace}},
                {"term": {ENTRY_ID_FIELD: entry_id}},
            ]}},
            "_source": False,
        }
        resp = self._client.search(index=self._index, body=body)
        return [h["_id"] for h in resp.get("hits", {}).get("hits", [])]

    def upsert(self, namespace: str, entry: dict[str, Any]) -> None:
        doc = dict(entry)
        doc[NAMESPACE_FIELD] = namespace
        entry_id = doc.get(ENTRY_ID_FIELD) or uuid.uuid4().hex
        doc[ENTRY_ID_FIELD] = entry_id
        # 先删同 entry_id 旧文档，避免更新产生重复（受最终一致影响，尽力而为）
        for _id in self._find_ids(namespace, entry_id):
            try:
                self._client.delete(index=self._index, id=_id)
            except Exception:  # noqa: BLE001 —— 旧文档清理失败不阻断写入
                pass
        self._client.index(index=self._index, body=doc)

    def delete(self, namespace: str, entry_id: str) -> None:
        for _id in self._find_ids(namespace, entry_id):
            self._client.delete(index=self._index, id=_id)


def make_opensearch_client(*, endpoint: str, region: str, service: str = "aoss"):
    """用 SigV4 构造 opensearch-py client（延迟导入依赖）。

    endpoint 形如 ``xxxx.us-west-2.aoss.amazonaws.com``（不含 https://）。
    """
    import boto3
    from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection

    host = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    credentials = boto3.Session(region_name=region).get_credentials()
    auth = AWSV4SignerAuth(credentials, region, service)
    return OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        pool_maxsize=20,
    )
