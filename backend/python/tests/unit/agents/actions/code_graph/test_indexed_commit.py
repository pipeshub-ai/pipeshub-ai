"""Every code-graph result says which commit its files were read from (``indexed_commit``).

One commit for every file the result names goes once at the top level (rows stay compact). When the files
disagree -- one synced at another commit, or one with no commit at all (uploaded to a knowledge base) -- each row
says its own and the top level says nothing rather than pick one. An error carries no commit.
"""
import asyncio
import json

import pytest

from app.agents.actions.code_graph import code_graph as cg
from app.agents.actions.code_graph.results import (
    attach_indexed_commit,
    with_repo_commit,
)

from .conftest import COMMIT, CONN, ORG, USER, QueryGraphProvider
from .test_read_code import _blob
from .test_search_without_path import FakeLexical
from .test_semantic_search import FakeRetrieval, _hit

pytestmark = pytest.mark.asyncio


def _tool(graph: QueryGraphProvider | None = None, *, semantic: bool = False) -> cg.CodeGraph:
    graph = graph or QueryGraphProvider()
    if semantic:  # vector hits name a block by (record, index), as test_semantic_search's graph does
        for key, index in (("k_caller", 0), ("k_target", 0)):
            graph.blocks[key]["index"] = index
    return cg.CodeGraph({"org_id": ORG, "user_id": USER, "graph_provider": graph, "blob_store": _blob(),
                         "retrieval_service": FakeRetrieval([_hit("rec-b", 0, 0.9)])})


CALLS = {
    "query_code_graph": {"select": "src/"},
    "get_neighbour": {"file_path": "src/b.py", "qualified_name": "function:target"},
    "read_code": {"file_path": "src/b.py", "qualified_name": "function:target"},
    "find_symbol_path": {"file_path_a": "src/a.py", "qualified_name_a": "function:caller",
                         "file_path_b": "src/b.py", "qualified_name_b": "function:target"},
    "search_code": {"pattern": "target"},
    "search_semantic": {"query": "the target function"},
    "outline": {"file_path": "src/b.py"},
    "impact": {"file_path": "src/b.py", "qualified_name": "function:target"},
    "entry_points": {},
    "impact_of_diff": {"patch": "--- a/src/b.py\n+++ b/src/b.py\n@@ -1 +1 @@\n-a\n+b\n"},
    "history": {"file_path": "src/b.py"},
}


async def _call(tool: cg.CodeGraph, name: str, **kwargs) -> dict:
    ok, raw = await getattr(tool, name)(connector_id=CONN, **kwargs)
    return json.loads(raw)


def _rows_with_files(result: object) -> list[dict]:
    out = []
    if isinstance(result, dict):
        if isinstance(result.get("file_path"), str):
            out.append(result)
        for value in result.values():
            out += _rows_with_files(value)
    elif isinstance(result, list):
        for value in result:
            out += _rows_with_files(value)
    return out


@pytest.mark.parametrize("name", sorted(CALLS))
async def test_every_tool_result_names_the_commit_it_was_read_from(name) -> None:
    out = await _call(_tool(semantic=name == "search_semantic"), name, **CALLS[name])
    assert "error" not in out, out
    assert out["indexed_commit"] == COMMIT
    # one commit: said once, not repeated on every row
    assert all("indexed_commit" not in row for row in _rows_with_files(out) if row is not out)


async def test_every_tool_has_a_case() -> None:
    tools = {n for n in dir(cg.CodeGraph) if not n.startswith("_") and callable(getattr(cg.CodeGraph, n))}
    assert set(CALLS) == tools


@pytest.mark.parametrize(("name", "kwargs"), [
    ("read_code", {"file_path": "src/missing.py"}),
    ("get_neighbour", {}),            # nothing to start from
    ("outline", {"file_path": "src/missing.py"}),
    ("impact", {}),
])
async def test_an_error_carries_no_commit(name, kwargs) -> None:
    out = await _call(_tool(), name, **kwargs)
    assert "error" in out and "indexed_commit" not in out


async def test_files_that_disagree_each_say_their_own_and_the_top_level_says_nothing() -> None:
    graph = QueryGraphProvider(commits={"rec-a": None})  # src/a.py came from a knowledge-base upload
    out = await _call(_tool(graph), "get_neighbour", file_path="src/b.py", qualified_name="function:target")
    assert "indexed_commit" not in out
    by_file = {row["file_path"]: row["indexed_commit"] for row in _rows_with_files(out)}
    assert by_file["src/a.py"] is None and by_file["src/b.py"] == COMMIT


async def test_two_commits_are_not_merged_into_one() -> None:
    graph = QueryGraphProvider(commits={"rec-a": "f" * 40})
    out = await _call(_tool(graph), "find_symbol_path", **CALLS["find_symbol_path"])
    assert "indexed_commit" not in out
    assert {row["indexed_commit"] for row in _rows_with_files(out)} == {COMMIT, "f" * 40}


NO_PATH = {"file_path_a": "web/api/client.ts", "qualified_name_a": "function:fetchTarget",
           "file_path_b": "web/ui/panel.ts", "qualified_name_b": "imports:run"}


async def test_a_path_not_found_says_the_commit_its_repo_was_synced_at() -> None:
    """A path not found names no file: it says the commit the repo was last synced at."""
    graph = QueryGraphProvider()
    graph.record_groups["repo-1"]["lastSyncedCommit"] = "9" * 40
    out = await _call(_tool(graph), "find_symbol_path", **NO_PATH)
    assert out["found"] is False and out["indexed_commit"] == "9" * 40


async def test_a_repo_never_stamped_gives_no_commit() -> None:
    out = await _call(_tool(), "find_symbol_path", **NO_PATH)
    assert out["found"] is False and "indexed_commit" not in out


async def test_a_failed_commit_lookup_leaves_the_answer_standing() -> None:
    class Down(QueryGraphProvider):
        failed = 0

        async def get_nodes_by_field_in(self, collection, *args, **kwargs) -> list[dict]:
            if collection == "recordGroups" and "lastSyncedCommit" in (kwargs.get("return_fields") or ()):
                Down.failed += 1
                raise RuntimeError("graph down")
            return await super().get_nodes_by_field_in(collection, *args, **kwargs)

    graph = Down()
    graph.record_groups["repo-1"]["lastSyncedCommit"] = "9" * 40  # so only the failure can leave it out
    out = await _call(_tool(graph), "find_symbol_path", **NO_PATH)
    assert Down.failed == 1
    assert out["found"] is False and "error" not in out and "indexed_commit" not in out


class Interleaving(QueryGraphProvider):
    """Yields to the loop inside every lookup, so two concurrent calls interleave their reads."""

    async def get_file_paths_for_records(self, *args, **kwargs) -> dict:
        await asyncio.sleep(0)
        return await super().get_file_paths_for_records(*args, **kwargs)

    async def get_nodes_by_filters(self, *args, **kwargs) -> list[dict]:
        await asyncio.sleep(0)
        return await super().get_nodes_by_filters(*args, **kwargs)


async def test_concurrent_calls_keep_their_commits_apart() -> None:
    one, two = "1" * 40, "2" * 40
    records = ("rec-a", "rec-b", "rec-c", "rec-d")
    a = _tool(Interleaving(commits=dict.fromkeys(records, one)))
    b = _tool(Interleaving(commits=dict.fromkeys(records, two)))
    outs = await asyncio.gather(*(
        _call(tool, name, **CALLS[name])
        for name in ("get_neighbour", "read_code", "impact", "outline") for tool in (a, b)
    ))
    assert [o["indexed_commit"] for o in outs] == [one, two] * 4


async def test_one_toolset_serving_two_calls_at_once_keeps_each_calls_files() -> None:
    """Same toolset, two files synced at different commits, read at the same time."""
    graph = Interleaving(commits={"rec-a": "a" * 40, "rec-b": "b" * 40})
    tool = _tool(graph)
    caller, target = await asyncio.gather(_call(tool, "query_code_graph", select="src/a.py"),
                                          _call(tool, "query_code_graph", select="src/b.py"))
    assert (caller["indexed_commit"], target["indexed_commit"]) == ("a" * 40, "b" * 40)


async def _attach(result: object, seen: dict, *, repos: dict | None = None, synced: dict | None = None,
                  asked: list | None = None) -> object:
    async def repos_of(records: list[str]) -> dict[str, str]:
        (asked if asked is not None else []).append(sorted(records))
        return {r: (repos or {})[r] for r in records if r in (repos or {})}

    async def synced_commits(groups: list[str]) -> dict[str, str]:
        return {g: (synced or {})[g] for g in groups if g in (synced or {})}

    return await attach_indexed_commit(result, seen, repos_of=repos_of, synced_commits=synced_commits)


async def test_a_listing_names_files_by_path_and_a_directory_row_is_no_file() -> None:
    listing = {"directories": [{"path": "src/sub", "files": 3, "select": "src/sub/"}],
               "files": [{"path": "src/b.py", "select": "src/b.py"}]}
    assert (await _attach(listing, {"src/b.py": {"rec-b": COMMIT}}))["indexed_commit"] == COMMIT


async def test_attach_leaves_errors_and_non_objects_alone() -> None:
    seen = {"src/b.py": {"rec-b": COMMIT}}
    assert await _attach({"error": "x", "file_path": "src/b.py"}, seen) == {"error": "x", "file_path": "src/b.py"}
    assert await _attach(["src/b.py"], seen) == ["src/b.py"]
    assert await _attach({"file_path": "src/b.py"}, seen) == {"file_path": "src/b.py", "indexed_commit": COMMIT}
    # a path read at two commits in one call (two repos, no repo on the row): unknown, not guessed
    two = await _attach({"rows": [{"file_path": "src/b.py"}]}, {"src/b.py": {"rec-b": COMMIT, "rec-x": "e" * 40}})
    assert "indexed_commit" not in two and two["rows"][0]["indexed_commit"] is None


OTHER = "e" * 40
TWO_REPOS = {"src/b.py": {"rec-b": COMMIT, "rec-x": OTHER}}
REPO_OF = {"rec-b": "acme/shop", "rec-x": "acme/admin"}


async def test_a_path_two_repos_share_takes_the_commit_of_the_rows_own_repo() -> None:
    """Each row takes the commit of its own repo, not whichever repo was read last."""
    asked: list = []
    out = await _attach({"neighbors": [{"file_path": "src/b.py", "repo": "acme/shop"},
                                       {"file_path": "src/b.py", "repo": "acme/admin"}]},
                        TWO_REPOS, repos=REPO_OF, asked=asked)
    assert [n["indexed_commit"] for n in out["neighbors"]] == [COMMIT, OTHER]
    assert asked == [["rec-b", "rec-x"]], "only the shared path's records are looked up"


async def test_a_repo_said_once_for_every_row_still_picks_the_commit() -> None:
    """Results hoist the repo every row shares to the result; the rows have none of their own."""
    out = await _attach({"repo": "acme/admin", "neighbors": [{"file_path": "src/b.py"}]},
                        TWO_REPOS, repos=REPO_OF)
    assert out["indexed_commit"] == OTHER


async def test_files_that_agree_need_no_repo_lookup() -> None:
    asked: list = []
    out = await _attach({"rows": [{"file_path": "src/b.py", "repo": "acme/shop"}]},
                        {"src/b.py": {"rec-b": COMMIT, "rec-x": COMMIT}}, repos=REPO_OF, asked=asked)
    assert out["indexed_commit"] == COMMIT and asked == []


async def test_a_result_naming_no_file_says_its_repos_synced_commit() -> None:
    out = await _attach({"found": False, "hops": [], "_graph_groups": ["repo-1"]}, {}, synced={"repo-1": COMMIT})
    assert out["indexed_commit"] == COMMIT


@pytest.mark.parametrize(("groups", "synced"), [
    ([], {}),                                           # no repo touched
    (["repo-1"], {}),                                   # synced before the stamp existed
    (["repo-1", "repo-2"], {"repo-1": COMMIT}),          # one repo has none
    (["repo-1", "repo-2"], {"repo-1": COMMIT, "repo-2": OTHER}),  # two repos, two commits
])
async def test_a_repo_commit_is_never_guessed(groups, synced) -> None:
    out = await _attach({"found": False, "_graph_groups": groups}, {}, synced=synced)
    assert "indexed_commit" not in out


def test_the_named_repos_commit_never_overrides_what_the_result_says() -> None:
    rows = {"neighbors": [{"file_path": "src/a.py", "indexed_commit": None}]}
    assert with_repo_commit(dict(rows), COMMIT) == rows                      # files that disagree stay so
    assert with_repo_commit({"indexed_commit": OTHER}, COMMIT) == {"indexed_commit": OTHER}
    assert with_repo_commit({"error": "x"}, COMMIT) == {"error": "x"}
    assert with_repo_commit({"found": False}, None) == {"found": False}      # repo not stamped yet
    assert with_repo_commit({"found": False}, COMMIT) == {"found": False, "indexed_commit": COMMIT}
    # files whose commit lookup failed: a file can be older than its repo's last sync, so nothing is said
    assert with_repo_commit({"nodes": [{"file_path": "src/a.py"}]}, COMMIT) == {"nodes": [{"file_path": "src/a.py"}]}


@pytest.mark.parametrize("hidden_by", ["another connector", "no access"])
async def test_a_text_match_takes_its_own_files_commit_not_a_hidden_copys(hidden_by) -> None:
    """Another record at the same path, outside the call's repo or unreadable, neither blanks the visible match's
    commit nor shows through it."""
    graph = QueryGraphProvider(commits={"rec-x": OTHER},
                               deny_records={"rec-x"} if hidden_by == "no access" else ())
    graph.code_files["rec-x"] = "src/b.py"
    graph.records["rec-x"] = {"_key": "rec-x", "orgId": ORG, "virtualRecordId": "v-x",
                              "connectorId": "conn-other" if hidden_by == "another connector" else CONN}
    out = await _call(_tool(graph), "search_code", pattern="return", mode="text", path="src/b.py")
    assert out["matches"] and out["indexed_commit"] == COMMIT



def _second_copy_of_b(graph: QueryGraphProvider, **record) -> None:
    """rec-x: another readable file at src/b.py (a second repo in the same connector), synced at OTHER."""
    graph.commits["rec-x"] = OTHER
    graph.code_files["rec-x"] = "src/b.py"
    graph.records["rec-x"] = {"_key": "rec-x", "orgId": ORG, "connectorId": CONN, "virtualRecordId": "v-x",
                              **record}


async def test_a_text_search_says_the_commit_of_the_file_that_matched() -> None:
    """Both copies of the path are read; only rec-b holds the text, so only its commit is the answer's."""
    graph = QueryGraphProvider()
    _second_copy_of_b(graph)
    blobs = _blob()
    blobs.records["v-x"] = {"block_containers": {"blocks": [{
        "code_metadata": {"qualified_name": "function:other", "kind": "function", "start_line": 1, "end_line": 2},
        "data": {"text": "def other():\n    pass\n"}}], "block_groups": []}}
    tool = cg.CodeGraph({"org_id": ORG, "user_id": USER, "graph_provider": graph, "blob_store": blobs})
    out = await _call(tool, "search_code", pattern="return", mode="text", path="src/b.py")
    assert out["files_searched"] == 2 and len(out["matches"]) == 1
    assert out["indexed_commit"] == COMMIT


@pytest.mark.parametrize(("hidden_by", "args"), [("the limit", {"limit": 2}),
                                                 ("the test filter", {})])
async def test_a_listing_says_only_the_listed_files_commits(hidden_by, args) -> None:
    """A second src/b.py that the listing leaves out does not blank the listed one's commit."""
    graph = QueryGraphProvider(file_roles={"rec-x": "test"} if hidden_by == "the test filter" else None)
    _second_copy_of_b(graph)
    out = await _call(_tool(graph), "query_code_graph", select="src/", **args)
    assert [f["path"] for f in out["files"]] == ["src/a.py", "src/b.py"]
    assert out["indexed_commit"] == COMMIT



async def test_a_listing_tells_two_repos_copies_of_a_path_apart() -> None:
    """Two of the connector's repos hold src/b.py at different commits: each row names its repo and its commit."""
    graph = QueryGraphProvider()
    _second_copy_of_b(graph, recordGroupId="repo-2")
    graph.records["rec-b"]["recordGroupId"] = "repo-1"
    graph.record_groups["repo-2"] = {"_key": "repo-2", "orgId": ORG, "connectorId": CONN, "groupName": "acme/admin"}
    out = await _call(_tool(graph), "query_code_graph", select="src/")
    rows = [(f["path"], f.get("repo"), f["indexed_commit"]) for f in out["files"]]
    assert rows == [("src/a.py", None, COMMIT), ("src/b.py", "acme/shop", COMMIT), ("src/b.py", "acme/admin", OTHER)]
    assert "indexed_commit" not in out



def _two_repos_hold_b(graph: QueryGraphProvider) -> None:
    """src/b.py in acme/shop (rec-b, COMMIT) and in acme/admin (rec-x, OTHER), one connector."""
    _second_copy_of_b(graph, recordGroupId="repo-2")
    graph.records["rec-b"]["recordGroupId"] = "repo-1"
    graph.record_groups["repo-2"] = {"_key": "repo-2", "orgId": ORG, "connectorId": CONN, "groupName": "acme/admin"}


def _same_text_in_both(graph: QueryGraphProvider, retrieval_service: object = None) -> cg.CodeGraph:
    blobs = _blob()
    blobs.records["v-x"] = blobs.records["v-b"]
    return cg.CodeGraph({"org_id": ORG, "user_id": USER, "graph_provider": graph, "blob_store": blobs,
                         "retrieval_service": retrieval_service})


async def test_text_matches_in_two_repos_copies_of_a_path_each_say_their_commit() -> None:
    graph = QueryGraphProvider()
    _two_repos_hold_b(graph)
    out = await _call(_same_text_in_both(graph), "search_code", pattern="return", mode="text", path="src/b.py")
    rows = sorted((m["repo"], m["indexed_commit"]) for m in out["matches"])
    assert rows == [("acme/admin", OTHER), ("acme/shop", COMMIT)]
    assert "indexed_commit" not in out


async def test_a_text_search_without_a_path_says_each_repos_copy_its_commit() -> None:
    """The keyword index proposes both copies of src/b.py; each match names its repo and that copy's commit."""
    graph = QueryGraphProvider()
    _two_repos_hold_b(graph)
    tool = _same_text_in_both(graph, FakeLexical(["v-b", "v-x"]))
    out = await _call(tool, "search_code", pattern="return", mode="text")
    rows = sorted((m["repo"], m["indexed_commit"]) for m in out["matches"])
    assert rows == [("acme/admin", OTHER), ("acme/shop", COMMIT)]
    assert "indexed_commit" not in out


class NoRepoNames(QueryGraphProvider):
    """The record -> repo lookup that tells shared paths apart fails; every other read works."""

    async def get_nodes_by_field_in(self, collection, *args, **kwargs) -> list[dict]:
        if collection == "records" and set(kwargs.get("return_fields") or ()) == {"_key", "recordGroupId", "orgId"}:
            raise RuntimeError("graph down")
        return await super().get_nodes_by_field_in(collection, *args, **kwargs)


@pytest.mark.parametrize("tool", ["query_code_graph", "search_code"])
async def test_a_failed_repo_lookup_keeps_the_answer_and_says_no_commit_for_the_shared_path(tool) -> None:
    graph = NoRepoNames()
    _two_repos_hold_b(graph)
    if tool == "query_code_graph":
        out = await _call(_tool(graph), tool, select="src/")
        rows = [f for f in out["files"] if f["path"] == "src/b.py"]
    else:
        out = await _call(_same_text_in_both(graph), tool, pattern="return", mode="text", path="src/b.py")
        rows = out["matches"]
    assert "error" not in out and len(rows) == 2
    assert all("repo" not in r and r.get("indexed_commit") is None for r in rows)  # unknown, never guessed
