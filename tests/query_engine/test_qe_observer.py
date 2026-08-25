"""Task 12 单测：可观测（EngineObserver 收到各节点 trace；request_id 贯穿）。Requirements 14。"""

from query_engine.observer import InMemoryEngineObserver
from query_engine.orchestrator import QueryOrchestrator


def test_observer_receives_node_traces(deps, request_obj) -> None:
    obs = InMemoryEngineObserver()
    QueryOrchestrator(deps, observer=obs).run(request_obj)
    nodes = [t.node for t in obs.traces]
    # 本层节点 + 下游委派均产出 trace
    for expected in ["context_rewrite", "term_normalization", "semantic_cache_lookup",
                     "sql_generator", "security_guard"]:
        assert expected in nodes


def test_request_id_propagated_to_answer(deps, request_obj) -> None:
    ans = QueryOrchestrator(deps).run(request_obj)
    assert ans.request_id == request_obj.request_id
