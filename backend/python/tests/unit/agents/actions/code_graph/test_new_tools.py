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
