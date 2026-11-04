"""`find_symbol_path` bounds its walk and says when the bound bit.

`max_depth` used to reach `range(max_depth)` unclamped -- one graph query per
hop, at the model's discretion -- and a frontier that hit `_FRONTIER_LIMIT`
came back as a plain `found: False`, which reads as "no path exists".
"""
import pytest

from app.agents.actions.code_graph import path as path_mod
from app.agents.actions.code_graph.ops import STRUCTURAL_RELATIONS
from app.agents.actions.code_graph.path import MAX_PATH_DEPTH, find_symbol_path_impl
from app.agents.actions.code_graph.results import without_marker

from .conftest import BLOCKS, CONN, ORG, USER, FakeGraphProvider, _block

pytestmark = pytest.mark.asyncio

CALLER = ("src/a.py", "function:caller")
RUN = ("src/b.py", "method:run")
THING = ("src/b.py", "class:Thing")
PANEL = ("web/ui/panel.ts", "function:renderPanel")


def _add(graph, key: str, name: str, kind: str, *, parent: str) -> None:
    block = _block(key, "rec-b", "src/b.py", f"src_b_{name}", name, kind)
    block.pop("filePath")  # the fake keeps paths per record
    graph.blocks[key] = {**block, "parentBlockId": parent}


async def _path(graph, a=CALLER, b=RUN, **kwargs) -> dict:
    # The toolset turns the repos a result touched into graph_status; not this test's subject.
    result = await find_symbol_path_impl(
        graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
        file_path_a=a[0], qualified_name_a=a[1],
        file_path_b=b[0], qualified_name_b=b[1], **kwargs,
    )
    return without_marker(result)


class TestPath:
    async def test_finds_the_chain_across_relation_types(self, graph) -> None:
        """caller -CALLS-> target -INHERITS-> Thing."""
        result = await _path(graph, b=THING)
        assert result["found"] is True
        assert [h["relation"] for h in result["hops"]] == ["CALLS", "INHERITS"]
        assert "capped" not in result

    async def test_reaching_a_class_does_not_reach_each_of_its_methods(self, graph) -> None:
        """A call into a class is its constructor; stepping on from Thing into `run`
        made every method of every class a call reached look called."""
        assert (await _path(graph))["found"] is False

    async def test_a_call_into_a_class_reaches_its_constructor(self, graph) -> None:
        """`Thing()` is an edge to the class; the path to `Thing.__init__` is that call
        and the one step down into the constructor, though no other method is reached."""
        _add(graph, "k_init", "__init__", "method", parent="k_class")
        graph.edges.append({"_from": f"{BLOCKS}/k_class", "_to": f"{BLOCKS}/k_init",
                            "relationshipType": "CONTAINS"})
        result = await _path(graph, b=("src/b.py", "method:__init__"))
        assert result["found"] is True
        assert [h["relation"] for h in result["hops"]] == ["CALLS", "INHERITS", "CONTAINS"]
        assert result["hops"][-1]["to"]["qualified_name"] == "method:__init__"

    async def test_a_path_goes_on_down_through_nested_definitions(self, graph) -> None:
        """Out of `run` to its class, then down through a nested definition's own."""
        _add(graph, "k_inner", "Inner", "class", parent="k_class")
        _add(graph, "k_leaf", "leaf", "field", parent="k_inner")
        graph.edges += [
            {"_from": f"{BLOCKS}/k_class", "_to": f"{BLOCKS}/k_inner", "relationshipType": "DEFINES"},
            {"_from": f"{BLOCKS}/k_inner", "_to": f"{BLOCKS}/k_leaf", "relationshipType": "DEFINES"},
        ]
        result = await _path(graph, a=RUN, b=("src/b.py", "field:leaf"))
        assert result["found"] is True
        assert [h["relation"] for h in result["hops"]][-2:] == ["DEFINES", "DEFINES"]

    async def test_an_exhausted_search_is_a_plain_miss(self, graph) -> None:
        assert await _path(graph, b=PANEL) == {"found": False, "hops": [], "searched_hops": MAX_PATH_DEPTH}


class TestDepth:
    async def test_max_depth_is_clamped_not_rejected(self) -> None:
        class Endless(FakeGraphProvider):
            calls = 0

            async def get_neighbors_for_nodes_by_relationship_types(
                self, node_keys, node_collection, relationship_types, direction,
                limit=5000, transaction=None, raise_on_error=False
            ) -> list[dict]:
                # A directed walk asks for outbound edges once per hop, plus inbound
                # containment while it climbs out of the start symbol; count the hops.
                if direction != "outbound":
                    return []
                self.calls += 1
                key = f"k_chain_{self.calls}"
                # A readable block, so the per-hop access check lets the walk go on.
                self.blocks[key] = {**self.blocks["k_caller"], "_key": key}
                return [{
                    "anchorKey": node_keys[0], "collection": BLOCKS,
                    "key": key, "direction": "outbound",
                    "relationshipType": "CALLS",
                }]

        graph = Endless()
        result = await _path(graph, max_depth=99)
        assert result == {"found": False, "hops": [], "searched_hops": MAX_PATH_DEPTH}
        # One query per hop, capped, and once more from the far end when the
        # forward search finds nothing.
        assert graph.calls == 2 * MAX_PATH_DEPTH

    async def test_a_depth_short_of_the_path_misses(self, graph) -> None:
        assert (await _path(graph, max_depth=2))["found"] is False


class TestFrontierCap:
    async def test_hitting_the_cap_is_reported_not_silent(self, graph, monkeypatch) -> None:
        monkeypatch.setattr(path_mod, "_FRONTIER_LIMIT", 1)
        result = await _path(graph, b=PANEL)
        assert result["found"] is False
        assert result["capped"] is True
        assert "cap" in result["note"]


class TestAccessControl:
    async def test_a_missing_endpoint_and_a_denied_one_are_the_same_miss(self) -> None:
        """An error naming the symbol for a miss, next to `found: False` for a
        denial, would tell the caller which of the two it hit."""
        missing = await _path(FakeGraphProvider(), b=("src/b.py", "function:nope"))
        denied = await _path(FakeGraphProvider(deny_records=["rec-b"]))
        assert missing == denied == {"found": False, "hops": []}


@pytest.mark.asyncio
async def test_a_provider_failure_is_not_reported_as_no_path() -> None:
    """`found: False` reads as "no path exists"; a failed hop must surface instead."""
    from app.agents.actions.code_graph.path import _bfs_edges

    class _Broken:
        async def get_neighbors_for_nodes_by_relationship_types(self, **kwargs: object) -> list:
            raise RuntimeError("neo4j is down")

    with pytest.raises(RuntimeError, match="neo4j is down"):
        await _bfs_edges(_Broken(), "a", "b", max_depth=2, relations=["CALLS"])


class TestDirectedWalkAsksOnlyForEdgesItCanTake:
    """B2: a hub's thousands of callers were fetched and access-checked, then dropped."""

    async def test_inbound_edges_are_asked_for_only_containment_while_climbing(self) -> None:
        class Recording(FakeGraphProvider):
            asked: list[tuple[str, tuple[str, ...]]] = []

            async def get_neighbors_for_nodes_by_relationship_types(
                self, node_keys, node_collection, relationship_types, direction,
                limit=5000, transaction=None, raise_on_error=False
            ) -> list[dict]:
                self.asked.append((direction, tuple(relationship_types)))
                return []

        graph = Recording()
        await _path(graph)
        assert {d for d, _ in graph.asked} == {"outbound", "inbound"}
        assert all(set(rels) <= set(STRUCTURAL_RELATIONS) for d, rels in graph.asked if d == "inbound")
