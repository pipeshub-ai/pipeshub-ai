"""search_semantic: knowledge-search hits on code, answered as symbol addresses."""
import pytest

from app.agents.actions.code_graph.semantic import search_semantic_impl

from .conftest import CONN, ORG, USER, FakeGraphProvider

pytestmark = pytest.mark.asyncio


class FakeRetrieval:
    def __init__(self, results: list[dict], status: str = "success") -> None:
        self.results = results
        self.status = status
        self.calls: list[dict] = []

    async def search_with_filters(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {"searchResults": self.results, "status": self.status, "message": "why"}


def _hit(record_id: str, index: int, score: float, **extra) -> dict:
    return {"content": "…", "score": score,
            "metadata": {"recordId": record_id, "blockIndex": index, **extra}}


def _graph(**kwargs) -> FakeGraphProvider:
    graph = FakeGraphProvider(**kwargs)
    for key, index in (("k_caller", 0), ("k_target", 0), ("k_method", 2)):
        graph.blocks[key]["index"] = index
    return graph


async def _search(graph, retrieval, **kwargs) -> dict:
    return await search_semantic_impl(graph_provider=graph, retrieval_service=retrieval,
                                      connector_id=CONN, org_id=ORG, user_id=USER, **kwargs)


async def test_hits_become_symbols_best_first_once_each() -> None:
    retrieval = FakeRetrieval([
        _hit("rec-b", 2, 0.9), _hit("rec-b", 2, 0.8),  # two passages of one long symbol
        _hit("rec-a", 0, 0.5), _hit("rec-zz", 0, 0.4),  # a block the graph does not have
        _hit("rec-b", 0, 0.3, isBlockGroup=True),
    ])
    out = await _search(_graph(), retrieval, query="what runs the thing")
    assert [(m["file_path"], m["qualified_name"], m["score"]) for m in out["matches"]] == [
        ("src/b.py", "method:run", 0.9), ("src/a.py", "function:caller", 0.5)]
    assert retrieval.calls[0]["filter_groups"] == {"apps": [CONN]}
    assert retrieval.calls[0]["queries"] == ["what runs the thing"]


async def test_test_files_are_hidden_unless_asked_for() -> None:
    graph = _graph(file_roles={"rec-a": "test"})
    retrieval = FakeRetrieval([_hit("rec-a", 0, 0.9), _hit("rec-b", 0, 0.5)])
    out = await _search(graph, retrieval, query="pay")
    assert [m["qualified_name"] for m in out["matches"]] == ["function:target"]
    assert out["hidden_tests"] == 1
    out = await _search(graph, retrieval, query="pay", include_tests=True)
    assert [m["qualified_name"] for m in out["matches"]] == ["function:caller", "function:target"]


async def test_limit_and_guards() -> None:
    retrieval = FakeRetrieval([_hit("rec-b", 2, 0.9), _hit("rec-a", 0, 0.5)])
    out = await _search(_graph(), retrieval, query="x", limit=1)
    assert len(out["matches"]) == 1 and out["truncated"] is True
    assert "required" in (await _search(_graph(), retrieval, query="  "))["error"]
    assert "not available" in (await _search(_graph(), None, query="x"))["error"]
    empty = await _search(_graph(), FakeRetrieval([]), query="x")
    assert empty["matches"] == [] and empty["hint"]


async def test_a_repo_is_scoped_in_the_search_itself() -> None:
    retrieval = FakeRetrieval([_hit("rec-b", 2, 0.9)])
    out = await _search(_graph(), retrieval, query="x", repo="acme/shop")
    assert retrieval.calls[0]["filter_groups"] == {"apps": [CONN], "recordGroups": ["repo-1"]}
    assert [m["qualified_name"] for m in out["matches"]] == ["method:run"]
    assert "error" in await _search(_graph(), retrieval, query="x", repo="other/repo")


async def test_a_failed_search_is_an_error_not_no_matches() -> None:
    out = await _search(_graph(), FakeRetrieval([], status="search_index_unavailable"), query="x")
    assert out == {"error": "why"}
    out = await _search(_graph(), FakeRetrieval([], status="empty_response"), query="x")
    assert out["matches"] == []


async def test_a_repo_falls_back_to_the_connector_when_groups_are_not_ready() -> None:
    class NotReady(FakeRetrieval):
        async def search_with_filters(self, **kwargs) -> dict:
            if "recordGroups" in kwargs["filter_groups"]:
                self.calls.append(kwargs)
                return {"searchResults": [], "status": "selection_not_ready", "message": "no"}
            return await super().search_with_filters(**kwargs)

    retrieval = NotReady([_hit("rec-b", 2, 0.9)])
    out = await _search(_graph(), retrieval, query="x", repo="acme/shop")
    assert [c["filter_groups"] for c in retrieval.calls] == [
        {"apps": [CONN], "recordGroups": ["repo-1"]}, {"apps": [CONN]}]
    assert [m["qualified_name"] for m in out["matches"]] == ["method:run"]
