"""Results say which way an edge points, and a path follows that way (A-2).

`get_neighbour` dropped the direction the provider returned, so with
`direction="any"` a caller and a callee read the same, and depth-2 rows could
not be traced back. `find_symbol_path` walked every edge both ways, so two
helpers that merely shared a caller came back "connected".
"""
import pytest

from app.agents.actions.code_graph.neighbours import get_neighbour_impl
from app.agents.actions.code_graph.path import find_symbol_path_impl
from app.agents.actions.code_graph.results import without_marker

from .conftest import BLOCKS, CONN, ORG, USER
from .conftest import FakeGraphProvider as _BaseFake

pytestmark = pytest.mark.asyncio

CALLER = ("src/a.py", "function:caller")
TARGET = ("src/b.py", "function:target")
CLIENT = ("web/api/client.ts", "function:fetchTarget")


class FakeGraphProvider(_BaseFake):
    def __init__(self, extra_edges=(), **kwargs) -> None:
        super().__init__(**kwargs)
        for block in self.blocks.values():
            block["connectorId"] = CONN
        self.edges += [
            {"_from": f"{BLOCKS}/{a}", "_to": f"{BLOCKS}/{b}", "relationshipType": "CALLS"}
            for a, b in extra_edges
        ]


async def _neighbours(graph, symbol=CALLER, **kwargs) -> dict:
    return await get_neighbour_impl(
        graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
        file_path=symbol[0], qualified_name=symbol[1], **kwargs,
    )


async def _path(graph, a, b, **kwargs) -> dict:
    # The toolset turns the repos a result touched into graph_status; not this test's subject.
    result = await find_symbol_path_impl(
        graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
        file_path_a=a[0], qualified_name_a=a[1], file_path_b=b[0], qualified_name_b=b[1],
        **kwargs,
    )
    return without_marker(result)


async def test_every_row_says_which_way_its_edge_points() -> None:
    graph = FakeGraphProvider(extra_edges=[("k_client", "k_target")])
    out = await _neighbours(graph, symbol=TARGET, direction="any", edge_types=["CALLS"])
    by_name = {r["qualified_name"]: r["direction"] for r in out["neighbors"]}
    assert by_name == {"function:caller": "inbound", "function:fetchTarget": "inbound"}

    out = await _neighbours(graph, symbol=CALLER, direction="any", edge_types=["CALLS"])
    assert [(r["qualified_name"], r["direction"]) for r in out["neighbors"]] == [
        ("function:target", "outbound")]


async def test_a_second_hop_names_the_node_it_came_through_and_never_the_anchor() -> None:
    # caller -> target -> caller (a cycle), and target -> fetchTarget.
    graph = FakeGraphProvider(extra_edges=[("k_target", "k_caller"), ("k_target", "k_client")])
    out = await _neighbours(graph, direction="outbound", edge_types=["CALLS"], depth=2)
    rows = {r["qualified_name"]: r for r in out["neighbors"]}

    assert set(rows) == {"function:target", "function:fetchTarget"}
    assert "via" not in rows["function:target"]
    assert rows["function:fetchTarget"]["via"] == "src/b.py#function:target"


async def test_two_callees_of_one_caller_are_not_a_path() -> None:
    graph = FakeGraphProvider(extra_edges=[("k_caller", "k_client")])

    assert await _path(graph, TARGET, CLIENT, edge_types=["CALLS"]) == {"found": False, "hops": []}
    related = await _path(graph, TARGET, CLIENT, edge_types=["CALLS"], undirected=True)
    assert related["found"] is True


async def test_a_path_that_runs_the_other_way_is_returned_reversed() -> None:
    out = await _path(FakeGraphProvider(), TARGET, CALLER, edge_types=["CALLS"])
    assert out["found"] is True
    assert out["reversed"] is True
    assert [h["explain"] for h in out["hops"]] == ["function:caller calls function:target"]


async def test_a_method_does_not_reach_what_its_sibling_calls() -> None:
    # Thing contains run; target calls fetchTarget. `run` must not reach
    # fetchTarget by climbing to Thing and descending into another member.
    graph = FakeGraphProvider(extra_edges=[("k_target", "k_client")])
    graph.edges.append({"_from": f"{BLOCKS}/k_class", "_to": f"{BLOCKS}/k_target",
                        "relationshipType": "CONTAINS"})
    assert (await _path(graph, ("src/b.py", "method:run"), CLIENT))["found"] is False


async def test_two_methods_of_one_class_still_connect_through_it() -> None:
    graph = FakeGraphProvider()
    graph.edges.append({"_from": f"{BLOCKS}/k_class", "_to": f"{BLOCKS}/k_target",
                        "relationshipType": "CONTAINS"})
    out = await _path(graph, ("src/b.py", "method:run"), TARGET)
    assert out["found"] is True
    assert [h["relation"] for h in out["hops"]] == ["CONTAINS", "CONTAINS"]


class _Swallowing(FakeGraphProvider):
    """Both real providers log a failed query and return [] unless asked to raise."""

    async def get_neighbors_for_nodes_by_relationship_types(self, *args, raise_on_error=False,
                                                            **kwargs) -> list[dict]:
        if raise_on_error:
            raise RuntimeError("graph unavailable")
        return []


async def test_a_storage_failure_is_an_error_not_an_empty_walk() -> None:
    """Raised, like the path search: the toolset turns it into an error."""
    with pytest.raises(RuntimeError, match="Failed to walk"):
        await _neighbours(_Swallowing(), direction="inbound", edge_types=["CALLS"])


async def test_a_storage_failure_is_not_reported_as_no_path() -> None:
    with pytest.raises(RuntimeError, match="graph unavailable"):
        await _path(_Swallowing(), CALLER, TARGET)
