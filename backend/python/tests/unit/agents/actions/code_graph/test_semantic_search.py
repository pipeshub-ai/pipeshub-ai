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


def _class_hit(record_id: str, group_index: int, score: float) -> dict:
    """A class header unit's point: a group point, addressed by blockGroupIndex."""
    return {"content": "class Thing: …", "score": score,
            "metadata": {"recordId": record_id, "blockGroupIndex": group_index, "isBlockGroup": True,
                         "isBlock": False, "unitKind": "class"}}


def _file_hit(record_id: str, score: float, summary: str = "Runs the nightly billing jobs.") -> dict:
    """The LLM file summary's point: no block or group index."""
    return {"content": summary, "score": score,
            "metadata": {"recordId": record_id, "isRecordSummary": True, "isBlock": False, "isBlockGroup": False}}


class CountingGraph(FakeGraphProvider):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.block_fetches: list[str] = []
        self.block_fetch_sizes: list[int] = []

    async def get_nodes_by_field_in(self, collection, field_name, field_values, **kwargs) -> list[dict]:
        if collection == "blocks":
            self.block_fetches.append(field_name)
            self.block_fetch_sizes.append(len(field_values))
        return await super().get_nodes_by_field_in(collection, field_name, field_values, **kwargs)

    async def get_documents_paginated(self, *args, **kwargs) -> list[dict]:
        raise AssertionError("blocks are fetched for every hit record at once, not one record at a time")


def _graph(**kwargs) -> FakeGraphProvider:
    graph = CountingGraph(**kwargs)
    for key, index in (("k_caller", 0), ("k_target", 0), ("k_method", 2), ("k_class", 0)):
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


async def test_class_and_file_hits_are_answered_beside_symbols_by_score() -> None:
    long_summary = "Runs the nightly billing jobs. " * 20
    retrieval = FakeRetrieval([
        _class_hit("rec-b", 0, 0.95), _hit("rec-b", 2, 0.9), _file_hit("rec-a", 0.7, long_summary),
        _class_hit("rec-b", 0, 0.6),  # the same class again
        _class_hit("rec-b", 7, 0.5),  # a group the graph does not have
    ])
    graph = _graph()
    out = await _search(graph, retrieval, query="what bills customers")
    assert out["matches"] == [
        {"file_path": "src/b.py", "qualified_name": "class:Thing", "kind": "class", "repo": "acme/shop",
         "start_line": 1, "end_line": 5, "score": 0.95},
        {"file_path": "src/b.py", "qualified_name": "method:run", "kind": "method", "repo": "acme/shop",
         "start_line": 1, "end_line": 5, "score": 0.9},
        {"file_path": "src/a.py", "kind": "file", "repo": "acme/shop", "summary": long_summary[:200],
         "score": 0.7},
    ]
    assert "qualified_name" not in out["matches"][2]
    assert graph.block_fetches == ["recordId"]  # one batched fetch for every hit record


async def test_the_node_fetch_is_bounded_and_says_when_it_stopped(monkeypatch) -> None:
    from app.agents.actions.code_graph import semantic

    monkeypatch.setattr(semantic, "_FETCH_RECORDS", 1)
    monkeypatch.setattr(semantic, "_FETCH_ROW_CEILING", 5)
    graph = _graph()
    for i in range(10):  # a generated file: more nodes than the ceiling
        graph.blocks[f"k_big{i}"] = {**graph.blocks["k_caller"], "_key": f"k_big{i}", "id": f"k_big{i}",
                                     "recordId": "rec-c", "index": i, "qualifiedName": f"function:gen{i}"}
    retrieval = FakeRetrieval([_hit("rec-c", 3, 0.9), _hit("rec-a", 0, 0.5)])
    out = await _search(graph, retrieval, query="x")
    assert [m["qualified_name"] for m in out["matches"]] == ["function:gen3"]
    assert out["truncated"] is True and "search_code" in out["note"]
    assert graph.block_fetches == ["recordId"]  # stopped after the first chunk: rec-a was never fetched


async def test_one_query_reads_at_most_five_records() -> None:
    graph = _graph()
    for i in range(12):
        rid = f"rec-x{i}"
        graph.records[rid] = {"_key": rid, "orgId": ORG, "connectorId": CONN, "virtualRecordId": f"v-x{i}"}
        graph.code_files[rid] = f"gen/x{i}.py"
        graph.blocks[f"k_x{i}"] = {**graph.blocks["k_caller"], "_key": f"k_x{i}", "id": f"k_x{i}", "recordId": rid,
                                   "index": 0, "qualifiedName": f"function:x{i}"}
    out = await _search(graph, FakeRetrieval([_hit(f"rec-x{i}", 0, 1 - i / 100) for i in range(12)]), query="x")
    assert len(out["matches"]) == 12
    assert graph.block_fetch_sizes == [5, 5, 2]


async def test_a_file_hit_on_a_file_without_named_code_takes_its_repo_from_the_record() -> None:
    graph = _graph()
    for rid, path in (("rec-e", "scripts/setup.py"), ("rec-f", "scripts/empty.py")):
        graph.records[rid] = {"_key": rid, "orgId": ORG, "connectorId": CONN, "virtualRecordId": f"v-{rid}",
                              "recordGroupId": "repo-1", "recordType": "CODE_FILE"}
        graph.code_files[rid] = path
    # rec-e holds only module statements (no qualified name); rec-f has no node at all
    graph.blocks["k_stmt"] = {**graph.blocks["k_caller"], "_key": "k_stmt", "id": "k_stmt", "recordId": "rec-e",
                              "index": 0, "qualifiedName": None}
    retrieval = FakeRetrieval([_file_hit("rec-e", 0.8, "Sets things up."), _file_hit("rec-f", 0.7, "Nothing yet.")])
    out = await _search(graph, retrieval, query="setup", repo="acme/shop")
    assert out["matches"] == [
        {"file_path": "scripts/setup.py", "kind": "file", "repo": "acme/shop", "summary": "Sets things up.",
         "score": 0.8},
        {"file_path": "scripts/empty.py", "kind": "file", "repo": "acme/shop", "summary": "Nothing yet.",
         "score": 0.7},
    ]


async def test_a_summary_hit_on_a_record_that_is_not_a_code_file_is_not_a_match() -> None:
    """A code connector's issues and pull requests have summaries too; no code tool can read them as files,
    and a path lookup would answer with the record's name."""
    graph = _graph()
    graph.records["rec-i"] = {"_key": "rec-i", "orgId": ORG, "connectorId": CONN, "virtualRecordId": "v-rec-i",
                              "recordGroupId": "repo-1", "recordType": "ISSUE"}
    graph.code_files["rec-i"] = "Billing fails on retry"
    retrieval = FakeRetrieval([_file_hit("rec-i", 0.9, "Billing fails on retry."), _hit("rec-a", 0, 0.5)])
    out = await _search(graph, retrieval, query="billing retry")
    assert [m["file_path"] for m in out["matches"]] == ["src/a.py"] and out["total"] == 1


async def test_class_and_file_hits_in_a_file_the_caller_cannot_read_are_dropped() -> None:
    retrieval = FakeRetrieval([_class_hit("rec-b", 0, 0.9), _file_hit("rec-b", 0.8), _hit("rec-a", 0, 0.5)])
    out = await _search(_graph(deny_records={"rec-b"}), retrieval, query="x")
    assert [m["file_path"] for m in out["matches"]] == ["src/a.py"]
    assert out["total"] == 1


async def test_class_and_file_hits_follow_test_hiding_and_the_repo_scope() -> None:
    retrieval = FakeRetrieval([_class_hit("rec-b", 0, 0.9), _file_hit("rec-a", 0.8)])
    out = await _search(_graph(file_roles={"rec-b": "test"}), retrieval, query="x")
    assert [m["kind"] for m in out["matches"]] == ["file"] and out["hidden_tests"] == 1
    out = await _search(_graph(), retrieval, query="x", repo="acme/shop")
    assert [m["kind"] for m in out["matches"]] == ["class", "file"]


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


async def test_a_residue_hit_is_not_a_match() -> None:
    """A file's residue unit is its short top-level lines, for keyword search: no symbol to answer."""
    residue = {"content": "import os", "score": 0.99,
               "metadata": {"recordId": "rec-b", "isBlock": False, "isBlockGroup": False, "unitKind": "residue"}}
    out = await _search(_graph(), FakeRetrieval([residue, _hit("rec-b", 2, 0.5)]), query="imports")
    assert [m["qualified_name"] for m in out["matches"]] == ["method:run"]
