"""OpenSearchVectorStore 单测（mock opensearch client，不连真实服务）。"""

from __future__ import annotations

from adapters.opensearch import (
    OpenSearchVectorStore,
    _l2_score_to_cosine,
    build_index_mapping,
)


class FakeOsClient:
    def __init__(self, hits=None) -> None:
        self._hits = hits or []
        self.indexed: list[dict] = []
        self.searches: list[dict] = []
        self.deletes: list[dict] = []

    def search(self, index, body):
        self.searches.append({"index": index, "body": body})
        return {"hits": {"hits": self._hits}}

    def index(self, index, body):
        self.indexed.append({"index": index, "body": body})
        return {"result": "created", "_id": "auto-id"}

    def delete_by_query(self, index, body):
        self.deletes.append({"index": index, "body": body})
        return {"deleted": 1}


def _hit(source, score, _id="x"):
    return {"_id": _id, "_score": score, "_source": source}


def test_similarity_search_builds_filtered_knn_query() -> None:
    client = FakeOsClient(hits=[_hit({"text": "CREATE TABLE t"}, 1.0)])
    vs = OpenSearchVectorStore(client=client, index="idx")
    out = vs.similarity_search([0.1, 0.2], top_k=3, namespace="schema")

    q = client.searches[0]["body"]
    assert client.searches[0]["index"] == "idx"
    assert q["size"] == 3
    knn = q["query"]["knn"]["vector"]
    assert knn["k"] == 3
    assert knn["vector"] == [0.1, 0.2]
    assert knn["filter"] == {"term": {"namespace": "schema"}}
    assert q["_source"]["excludes"] == ["vector"]
    # 结果注入 score/similarity/entry_id
    assert out[0]["text"] == "CREATE TABLE t"
    assert out[0]["score"] == 1.0
    assert out[0]["similarity"] == 1.0  # 完美命中 -> 余弦 1.0
    assert out[0]["entry_id"] == "x"


def test_similarity_search_maps_examples_shape() -> None:
    client = FakeOsClient(hits=[_hit({"question": "发电量?", "sql": "SELECT 1"}, 0.9)])
    out = OpenSearchVectorStore(client=client).similarity_search([0.0], 1, "examples")
    assert out[0]["question"] == "发电量?"
    assert out[0]["sql"] == "SELECT 1"


def test_upsert_sets_namespace_and_entry_id() -> None:
    client = FakeOsClient()
    OpenSearchVectorStore(client=client).upsert("semantic_cache", {
        "vector": [0.1], "question": "q", "pre_isolation_sql": "SELECT 1",
    })
    doc = client.indexed[0]["body"]
    assert doc["namespace"] == "semantic_cache"
    assert doc["pre_isolation_sql"] == "SELECT 1"
    assert doc["entry_id"]  # 自动生成


def test_upsert_preserves_explicit_entry_id() -> None:
    client = FakeOsClient()
    OpenSearchVectorStore(client=client).upsert("schema", {
        "vector": [0.1], "text": "t", "entry_id": "fixed-1",
    })
    assert client.indexed[0]["body"]["entry_id"] == "fixed-1"


def test_delete_by_namespace_and_entry_id() -> None:
    client = FakeOsClient()
    OpenSearchVectorStore(client=client).delete("semantic_cache", "e1")
    filt = client.deletes[0]["body"]["query"]["bool"]["filter"]
    assert {"term": {"namespace": "semantic_cache"}} in filt
    assert {"term": {"entry_id": "e1"}} in filt


def test_score_to_cosine_monotonic() -> None:
    # 完美命中 score=1 -> cos=1；越小越低
    assert _l2_score_to_cosine(1.0) == 1.0
    assert _l2_score_to_cosine(0.5) < 1.0
    assert _l2_score_to_cosine(0.0) == -1.0


def test_index_mapping_dimension_and_engine() -> None:
    m = build_index_mapping(1024)
    v = m["mappings"]["properties"]["vector"]
    assert v["dimension"] == 1024
    assert v["method"]["engine"] == "faiss"
    assert m["settings"]["index"]["knn"] is True
