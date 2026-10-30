"""search_code, outline, impact and entry_points (A-9)."""
import pytest

from app.agents.actions.code_graph.neighbours import impact_impl
from app.agents.actions.code_graph.query import entry_points_impl
from app.agents.actions.code_graph.read import outline_impl
from app.agents.actions.code_graph.results import without_marker
from app.agents.actions.code_graph.search import search_code_impl

from .conftest import CONN, ORG, USER, FakeBlobStore, QueryGraphProvider

pytestmark = pytest.mark.asyncio

_B_SOURCE = {
    "block_containers": {
        "block_groups": [],
        "blocks": [
            {"index": 0, "data": {"text": "def target():\n    return helper(\"payments\")\n"},
             "code_metadata": {"qualified_name": "function:target", "kind": "function",
                               "start_line": 1, "end_line": 2, "signature": "def target()",
                               "docstring": "Pay the invoice.\nMore detail."}},
            {"index": 1, "data": {"text": "class Thing:\n    def run(self):\n        pass\n"},
             "code_metadata": {"qualified_name": "class:Thing", "kind": "class",
                               "start_line": 4, "end_line": 6, "signature": "class Thing",
                               "decorators": ["@dataclass"]}},
        ],
    }
}


def _blobs() -> FakeBlobStore:
    return FakeBlobStore({"v-b": _B_SOURCE})


def _ctx(graph: QueryGraphProvider) -> dict:
    return {"graph_provider": graph, "connector_id": CONN, "org_id": ORG, "user_id": USER}


async def test_outline_lists_definitions_with_signatures_not_bodies() -> None:
    out = await outline_impl(**_ctx(QueryGraphProvider()), blob_store=_blobs(), file_path="src/b.py")
    assert out["symbols"] == [
        {"qualified_name": "function:target", "lines": "1-2", "signature": "def target()",
         "doc": "Pay the invoice."},
        {"qualified_name": "class:Thing", "lines": "4-6", "signature": "class Thing",
         "decorators": ["@dataclass"]},
    ]
    assert "code" not in str(out)


async def test_outline_of_a_hidden_file_reads_like_a_missing_one() -> None:
    graph = QueryGraphProvider(deny_records=["rec-b"])
    out = await outline_impl(**_ctx(graph), blob_store=_blobs(), file_path="src/b.py")
    assert out == {"error": "No indexed code at 'src/b.py'"}


async def test_identifier_search_finds_definitions_by_name_case_insensitively() -> None:
    out = await search_code_impl(**_ctx(QueryGraphProvider()), blob_store=None, pattern="Target")
    assert [(m["file_path"], m["qualified_name"]) for m in out["matches"]] == [
        ("src/b.py", "function:target")]


async def test_text_search_finds_lines_in_bodies_under_a_path() -> None:
    out = await search_code_impl(**_ctx(QueryGraphProvider()), blob_store=_blobs(),
                                 pattern='"payments"', mode="text", path="src/")
    assert out["matches"] == [{"file_path": "src/b.py", "line": 2,
                               "text": 'return helper("payments")'}]
    assert out["untrusted"]


async def test_regex_search_and_its_guards() -> None:
    ctx = _ctx(QueryGraphProvider())
    out = await search_code_impl(**ctx, blob_store=_blobs(), pattern=r"def \w+\(self",
                                 mode="regex", path="src/b.py")
    assert [m["line"] for m in out["matches"]] == [5]
    assert "nested" in (await search_code_impl(**ctx, blob_store=_blobs(), pattern="(a+)+",
                                               mode="regex", path="src/"))["error"]
    assert "path" in (await search_code_impl(**ctx, blob_store=_blobs(), pattern="x",
                                             mode="text"))["error"]


async def test_text_search_drops_a_checkout_prefix_the_index_lacks() -> None:
    out = await search_code_impl(**_ctx(QueryGraphProvider()), blob_store=_blobs(),
                                 pattern='"payments"', mode="text", path="repo/src/**")
    assert [m["file_path"] for m in out["matches"]] == ["src/b.py"]
    assert out["path"] == "src/**"


async def test_text_search_on_a_missing_path_is_an_error_naming_what_exists() -> None:
    out = await search_code_impl(**_ctx(QueryGraphProvider()), blob_store=_blobs(),
                                 pattern="x", mode="text", path="src/nope/")
    assert "nothing was searched" in out["error"]
    assert "src/ holds: " in out["error"] and "b.py" in out["error"]
    for wide in ("repo/nope/**", "nope/**"):  # never widened to `**`, the whole repo
        out = await search_code_impl(**_ctx(QueryGraphProvider()), blob_store=_blobs(),
                                     pattern="x", mode="text", path=wide)
        assert "nothing was searched" in out["error"], wide


async def test_text_search_skips_a_suffix_only_another_connector_holds() -> None:
    graph = QueryGraphProvider()
    graph.code_files["rec-x"] = "repo/src/b.py"
    graph.records["rec-x"] = {"_key": "rec-x", "orgId": ORG, "connectorId": "conn-2",
                              "virtualRecordId": "v-x"}
    out = await search_code_impl(**_ctx(graph), blob_store=_blobs(),
                                 pattern='"payments"', mode="text", path="repo/src/b.py")
    assert [m["file_path"] for m in out["matches"]] == ["src/b.py"]


async def test_files_without_stored_source_do_not_use_up_the_file_cap(monkeypatch) -> None:
    """A repo's images and data files sort among its code; counted toward the cap they
    left most of a large directory unread."""
    from app.agents.actions.code_graph import search

    monkeypatch.setattr(search, "_TEXT_SEARCH_FILES", 2)  # src/ holds a.py and b.py
    graph = QueryGraphProvider()
    graph.code_files["rec-img"] = "src/a_logo.png"  # sorts between them
    graph.records["rec-img"] = {"_key": "rec-img", "orgId": ORG, "connectorId": CONN}
    out = await search_code_impl(**_ctx(graph), blob_store=_blobs(),
                                 pattern='"payments"', mode="text", path="src/")
    assert [m["file_path"] for m in out["matches"]] == ["src/b.py"]
    assert not out.get("truncated")


async def test_impact_groups_what_reaches_a_symbol_by_file() -> None:
    out = without_marker(await impact_impl(**_ctx(QueryGraphProvider()), file_path="src/b.py",
                                           qualified_name="function:target"))
    assert out["impacted_files"] == [
        {"file_path": "src/a.py", "symbols": ["function:caller"], "nearest_hop": 1}]
    assert out["impacted_symbols"] == 1


async def test_entry_points_are_uncalled_functions() -> None:
    out = await entry_points_impl(**_ctx(QueryGraphProvider()))
    names = {p["qualified_name"] for p in out["entry_points"]}
    assert "function:caller" in names, "nothing calls it"
    assert "function:target" not in names, "caller calls it"
    assert all(p["reason"] for p in out["entry_points"])


async def test_outline_says_when_the_file_did_not_parse_cleanly() -> None:
    """P-9: a syntax error ends the parse early; the outline must not look complete."""
    broken = {"block_containers": {"block_groups": [], "blocks": [
        *_B_SOURCE["block_containers"]["blocks"],
        {"index": 2, "data": {"text": "b.py", "kind": "file_summary", "parse_error_line": 5}},
    ]}}
    out = await outline_impl(**_ctx(QueryGraphProvider()), blob_store=FakeBlobStore({"v-b": broken}),
                             file_path="src/b.py")
    assert out["parse_error_line"] == 5 and "line 5" in out["note"]
    clean = await outline_impl(**_ctx(QueryGraphProvider()), blob_store=_blobs(), file_path="src/b.py")
    assert "parse_error_line" not in clean


async def test_impact_lists_the_tests_a_change_reaches_apart() -> None:
    """The tests to run after a change are part of its blast radius, not only a count."""
    out = without_marker(await impact_impl(**_ctx(QueryGraphProvider(file_roles={"rec-a": "test"})),
                                           file_path="src/b.py", qualified_name="function:target"))
    assert out["impacted_files"] == []
    assert out["impacted_tests"] == [
        {"file_path": "src/a.py", "symbols": ["function:caller"], "nearest_hop": 1}]
    assert "hidden_tests" not in out


async def test_impact_marks_what_builds_on_a_symbol_and_can_follow_calls_only() -> None:
    """`target` inherits Thing and `caller` calls target: one builds on Thing, one reaches it by a call."""
    ctx = _ctx(QueryGraphProvider())
    out = without_marker(await impact_impl(**ctx, file_path="src/b.py", qualified_name="class:Thing"))
    assert out["impacted_files"] == [
        {"file_path": "src/b.py", "symbols": ["function:target"], "built_on": ["function:target"],
         "nearest_hop": 1},
        {"file_path": "src/a.py", "symbols": ["function:caller"], "nearest_hop": 2}]
    calls = without_marker(await impact_impl(**ctx, file_path="src/b.py", qualified_name="class:Thing",
                                             edge_types=["CALLS"]))
    assert calls["impacted_files"] == []
    assert "unknown edge_types" in (await impact_impl(**ctx, file_path="src/b.py",
                                                      qualified_name="class:Thing",
                                                      edge_types=["CONTAINS"]))["error"]


async def test_impact_of_an_unresolved_symbol_is_an_error_not_an_empty_blast_radius() -> None:
    out = await impact_impl(**_ctx(QueryGraphProvider()), file_path="src/vs/b.py",
                            qualified_name="function:target")
    assert "No readable symbol" in out["error"]


async def test_hidden_test_neighbours_say_how_to_list_them() -> None:
    from app.agents.actions.code_graph.neighbours import get_neighbour_impl

    out = await get_neighbour_impl(**_ctx(QueryGraphProvider(file_roles={"rec-a": "test"})),
                                   file_path="src/b.py", qualified_name="function:target",
                                   direction="inbound", edge_types=["CALLS"])
    assert out["hidden_tests"] == 1 and "include_tests=true" in out["hint"]


async def test_a_capped_depth_is_reported_and_impact_walks_five_levels() -> None:
    """A walk cut short silently reads as "nothing further up"."""
    from app.agents.actions.code_graph.neighbours import get_neighbour_impl

    ctx = _ctx(QueryGraphProvider())
    out = await get_neighbour_impl(**ctx, file_path="src/b.py", qualified_name="function:target",
                                   direction="inbound", depth=5)
    assert "depth 5 asked, 3 walked" in out["depth_capped"]
    five = await impact_impl(**ctx, file_path="src/b.py", qualified_name="function:target", depth=5)
    assert "depth_capped" not in five
    seven = await impact_impl(**ctx, file_path="src/b.py", qualified_name="function:target", depth=7)
    assert "7 asked, 5 walked" in seven["depth_capped"]


async def test_text_search_reuses_an_indexed_files_lines_until_the_record_changes(monkeypatch) -> None:
    """Every caller list scans the repo's text; the blob fetch is the whole cost, so an
    indexed file's lines are kept, and a newer record (reindexed) is read again."""
    from app.agents.actions.code_graph import search

    monkeypatch.setattr(search, "_LINE_CACHE", type(search._LINE_CACHE)())
    graph = QueryGraphProvider()
    graph.records["rec-b"].update(indexingStatus="COMPLETED", updatedAtTimestamp=1)
    blobs = _blobs()
    reads = []
    original = blobs.get_record_from_storage

    async def counted(virtual_record_id, org_id, lookup_result=None) -> dict | None:
        reads.append(virtual_record_id)
        return await original(virtual_record_id, org_id, lookup_result)

    blobs.get_record_from_storage = counted
    for _ in range(2):
        out = await search_code_impl(**_ctx(graph), blob_store=blobs,
                                     pattern='"payments"', mode="text", path="src/b.py")
        assert [m["file_path"] for m in out["matches"]] == ["src/b.py"]
    assert reads == ["v-b"]
    graph.records["rec-b"]["updatedAtTimestamp"] = 2
    await search_code_impl(**_ctx(graph), blob_store=blobs, pattern='"payments"', mode="text", path="src/b.py")
    assert reads == ["v-b", "v-b"]


async def test_text_search_can_be_held_to_one_language_across_the_repo() -> None:
    """`*.py` reads every Python file and nothing else, at any depth."""
    out = await search_code_impl(**_ctx(QueryGraphProvider()), blob_store=_blobs(),
                                 pattern='"payments"', mode="text", path="*.py")
    assert [m["file_path"] for m in out["matches"]] == ["src/b.py"]
    none = await search_code_impl(**_ctx(QueryGraphProvider()), blob_store=_blobs(),
                                  pattern='"payments"', mode="text", path="*.ts")
    assert not none.get("matches")


async def test_a_capped_scan_that_matched_no_file_says_it_was_capped(monkeypatch) -> None:
    """The path scan stops at its limit before the glob is applied: finding no file
    there is not finding none in the repo."""
    from app.agents.actions.code_graph import search

    monkeypatch.setattr(search, "_TEXT_SCAN_LIMIT", 1)
    out = await search_code_impl(**_ctx(QueryGraphProvider()), blob_store=_blobs(),
                                 pattern='"payments"', mode="text", path="*.tsx")
    assert "error" in out and out["truncated"] is True
