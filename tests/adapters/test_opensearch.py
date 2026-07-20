"""OpenSearchVectorStore 单测（内存 mock client；AOSS 兼容的 index+search+delete-by-_id）。"""

from __future__ import annotations

from adapters.opensearch import (
    OpenSearchVectorStore,
    _l2_score_to_cosine,
    build_index_mapping,
)


class FakeOsClient:
    """内存模拟 AOSS：index 自动 _id、bool-filter search 找 _id、delete by _id、knn 返回预置命中。"""

    def __init__(self, knn_hits=None) -> None:
        self._knn_hits = knn_hits or []
        self._docs: dict[str, dict] = {}
        self._n = 0
        self.index_calls: list[dict] = []
        self.deleted_ids: list[str] = []

    def index(self, index, body):
        self._n += 1
        _id = f"auto-{self._n}"
        self._docs[_id] = dict(body)
        self.index_calls.append(dict(body))
        return {"result": "created", "_id": _id}

    def search(self, index, body):
        q = body.get("query", {})
        if "knn" in q:
            return {"hits": {"hits": self._knn_hits}}
        ns = eid = None
        for f in q.get("bool", {}).get("filter", []):
            t = f.get("term", {})
            ns = t.get("namespace", ns)
            eid = t.get("entry_id", eid)
        hits = [{"_id": _id} for _id, d in self._docs.items()
                if d.get("namespace") == ns and d.get("entry_id") == eid]
        return {"hits": {"hits": hits}}

    def delete(self, index, id):
        self.deleted_ids.append(id)
        self._docs.pop(id, None)
        return {"result": "deleted"}

    # 便于断言
    def docs_with_entry_id(self, entry_id):
        return [d for d in self._docs.values() if d.get("entry_id") == entry_id]


def _hit(source, score, _id="x"):
    return {"_id": _id, "_score": score, "_source": source}


def test_similarity_search_builds_filtered_knn_query() -> None:
    client = FakeOsClient(knn_hits=[_hit({"text": "CREATE TABLE t"}, 1.0)])
    vs = OpenSearchVectorStore(client=client, index="idx")
    out = vs.similarity_search([0.1, 0.2], top_k=3, namespace="schema")
    assert out[0]["text"] == "CREATE TABLE t"
    assert out[0]["score"] == 1.0 and out[0]["similarity"] == 1.0
    assert out[0]["entry_id"] == "x"


def test_similarity_search_maps_examples_shape() -> None:
    client = FakeOsClient(knn_hits=[_hit({"question": "发电量?", "sql": "SELECT 1"}, 0.9)])
    out = OpenSearchVectorStore(client=client).similarity_search([0.0], 1, "examples")
    assert out[0]["question"] == "发电量?" and out[0]["sql"] == "SELECT 1"


def test_upsert_sets_namespace_and_entry_id() -> None:
    client = FakeOsClient()
    OpenSearchVectorStore(client=client).upsert("semantic_cache", {
        "vector": [0.1], "question": "q", "pre_isolation_sql": "SELECT 1"})
    doc = client.index_calls[0]
    assert doc["namespace"] == "semantic_cache" and doc["pre_isolation_sql"] == "SELECT 1"
    assert doc["entry_id"]


def test_upsert_preserves_explicit_entry_id() -> None:
    client = FakeOsClient()
    OpenSearchVectorStore(client=client).upsert("schema", {
        "vector": [0.1], "text": "t", "entry_id": "fixed-1"})
    assert client.index_calls[0]["entry_id"] == "fixed-1"


def test_upsert_overwrites_same_entry_id() -> None:
    # 同 entry_id 再次 upsert 应先删旧文档，最终仅 1 条
    client = FakeOsClient()
    vs = OpenSearchVectorStore(client=client)
    vs.upsert("glossary", {"vector": [0.1], "text": "v1", "entry_id": "t1"})
    vs.upsert("glossary", {"vector": [0.2], "text": "v2", "entry_id": "t1"})
    docs = client.docs_with_entry_id("t1")
    assert len(docs) == 1 and docs[0]["text"] == "v2"
    assert client.deleted_ids  # 旧文档被删


def test_delete_finds_and_deletes_by_auto_id() -> None:
    client = FakeOsClient()
    vs = OpenSearchVectorStore(client=client)
    vs.upsert("glossary", {"vector": [0.1], "text": "t", "entry_id": "t1"})
    vs.delete("glossary", "t1")
    assert client.docs_with_entry_id("t1") == []


def test_delete_absent_is_noop() -> None:
    client = FakeOsClient()
    OpenSearchVectorStore(client=client).delete("glossary", "nope")  # 不抛
    assert client.deleted_ids == []


def test_score_to_cosine_monotonic() -> None:
    assert _l2_score_to_cosine(1.0) == 1.0
    assert _l2_score_to_cosine(0.5) < 1.0
    assert _l2_score_to_cosine(0.0) == -1.0


def test_index_mapping_dimension_and_engine() -> None:
    m = build_index_mapping(1024)
    v = m["mappings"]["properties"]["vector"]
    assert v["dimension"] == 1024 and v["method"]["engine"] == "faiss"
    assert m["settings"]["index"]["knn"] is True
