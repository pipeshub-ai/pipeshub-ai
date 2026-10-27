"""What a walk or a listing returns: compact, stable, and honest about what it hid (A-8, A-12)."""
import json
import random

import pytest

from app.agents.actions.code_graph import code_graph as cg
from app.agents.actions.code_graph.neighbours import get_neighbour_impl
from app.agents.actions.code_graph.query import query_code_graph_impl

from .conftest import CONN, ORG, USER, FakeGraphProvider, QueryGraphProvider

pytestmark = pytest.mark.asyncio


async def _walk(graph: FakeGraphProvider, **kwargs) -> dict:
    return await get_neighbour_impl(
        graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
        file_path="src/b.py", qualified_name="function:target", direction="any", **kwargs,
    )


async def test_a_test_files_neighbours_are_counted_when_hidden() -> None:
    graph = FakeGraphProvider(file_roles={"rec-a": "test"})
    out = await _walk(graph)
    assert "function:caller" not in {n["qualified_name"] for n in out["neighbors"]}
    assert out["hidden_tests"] == 1
    shown = await _walk(graph, include_tests=True)
    assert "function:caller" in {n["qualified_name"] for n in shown["neighbors"]}
    assert "hidden_tests" not in shown


async def test_a_listing_says_how_many_test_files_it_hid() -> None:
    graph = QueryGraphProvider(file_roles={"rec-a": "test"})
    out = await query_code_graph_impl(graph_provider=graph, connector_id=CONN, org_id=ORG,
                                      user_id=USER, select="src/*")
    assert out["hidden_tests"] == 1


async def test_the_same_walk_reads_the_same_whatever_order_the_database_returns() -> None:
    class Shuffling(FakeGraphProvider):
        async def get_neighbors_for_nodes_by_relationship_types(self, *args, **kwargs) -> list:
            rows = await super().get_neighbors_for_nodes_by_relationship_types(*args, **kwargs)
            random.Random(len(rows) * 7 + self.calls).shuffle(rows)
            self.calls += 1
            return rows

    outs = []
    for seed in range(4):
        graph = Shuffling()
        graph.calls = seed
        outs.append(await _walk(graph, depth=2))
    assert all(o["neighbors"] == outs[0]["neighbors"] for o in outs)


async def test_rows_carry_neither_echoed_arguments_nor_repeated_fields() -> None:
    out = await _walk(FakeGraphProvider(), depth=2)
    assert out["repo"]
    assert not {"edge_types", "depth", "direction"} & out.keys()
    for row in out["neighbors"]:
        assert "kind" not in row and "repo" not in row
        assert row["qualified_name"].partition(":")[0], "the prefix carries the kind"


async def test_tool_output_has_no_padding() -> None:
    tool = cg.CodeGraph({"org_id": ORG, "user_id": USER,
                         "graph_provider": FakeGraphProvider(), "blob_store": object()})
    _ok, raw = tool._to_output({"a": [1, 2], "b": {"c": None}})
    assert raw == '{"a":[1,2],"b":{"c":null}}'
    assert json.loads(raw) == {"a": [1, 2], "b": {"c": None}}


async def test_read_source_is_marked_as_data_and_errors_are_not() -> None:
    from app.agents.actions.code_graph.results import UNTRUSTED_NOTE, as_untrusted

    assert as_untrusted({"code": "x = 1"})["untrusted"] == UNTRUSTED_NOTE
    assert "untrusted" not in as_untrusted({"error": "no such file"})


@pytest.mark.parametrize(("url", "start", "end", "link"), [
    ("https://github.com/a/b/blob/abc123/x.py", 4, 9, "https://github.com/a/b/blob/abc123/x.py#L4-L9"),
    ("https://gitlab.com/a/b/-/blob/abc123/x.py", 4, 9, "https://gitlab.com/a/b/-/blob/abc123/x.py#L4-9"),
    ("https://github.com/a/b/blob/abc123/x.py", 4, 4, "https://github.com/a/b/blob/abc123/x.py#L4"),
    ("https://github.com/a/b/blob/abc123/x.py", None, None, "https://github.com/a/b/blob/abc123/x.py"),
    (None, 1, 2, None),
])
async def test_a_read_cites_the_lines_at_the_commit_they_were_read_from(url, start, end, link) -> None:
    from app.agents.actions.code_graph.read import line_link

    assert line_link(url, start, end) == link
