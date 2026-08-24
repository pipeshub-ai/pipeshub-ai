"""`find_symbol_path` bounds its walk and says when the bound bit.

`max_depth` used to reach `range(max_depth)` unclamped -- one graph query per
hop, at the model's discretion -- and a frontier that hit `_FRONTIER_LIMIT`
came back as a plain `found: False`, which reads as "no path exists".
"""
import pytest

from app.agents.actions.code_graph import ops
from app.agents.actions.code_graph.ops import MAX_PATH_DEPTH, find_symbol_path_impl

from .conftest import BLOCKS, CONN, ORG, USER, FakeGraphProvider

pytestmark = pytest.mark.asyncio

CALLER = ("src/a.py", "function:caller")
RUN = ("src/b.py", "method:run")
PANEL = ("web/ui/panel.ts", "function:renderPanel")


async def _path(graph, a=CALLER, b=RUN, **kwargs) -> dict:
    return await find_symbol_path_impl(
        graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
        file_path_a=a[0], qualified_name_a=a[1],
        file_path_b=b[0], qualified_name_b=b[1], **kwargs,
    )


class TestPath:
    async def test_finds_the_chain_across_relation_types(self, graph) -> None:
        """caller -CALLS-> target -INHERITS-> Thing -CONTAINS-> run."""
        result = await _path(graph)
        assert result["found"] is True
        assert [h["relation"] for h in result["hops"]] == ["CALLS", "INHERITS", "CONTAINS"]
        assert "capped" not in result

    async def test_an_exhausted_search_is_a_plain_miss(self, graph) -> None:
        assert await _path(graph, b=PANEL) == {"found": False, "hops": []}


class TestDepth:
    async def test_max_depth_is_clamped_not_rejected(self) -> None:
        class Endless(FakeGraphProvider):
            calls = 0

            async def get_neighbors_for_nodes_by_relationship_types(
                self, node_keys, node_collection, relationship_types, direction,
                limit=5000, transaction=None,
            ) -> list[dict]:
                self.calls += 1
                return [{
                    "anchorKey": node_keys[0], "collection": BLOCKS,
                    "key": f"k_chain_{self.calls}", "direction": "outbound",
                    "relationshipType": "CALLS",
                }]

        graph = Endless()
        result = await _path(graph, max_depth=99)
        assert result == {"found": False, "hops": []}
        assert graph.calls == MAX_PATH_DEPTH, "one query per hop, and no more hops than the cap"

    async def test_a_depth_short_of_the_path_misses(self, graph) -> None:
        assert (await _path(graph, max_depth=2))["found"] is False


class TestFrontierCap:
    async def test_hitting_the_cap_is_reported_not_silent(self, graph, monkeypatch) -> None:
        monkeypatch.setattr(ops, "_FRONTIER_LIMIT", 1)
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
    from app.agents.actions.code_graph.ops import _bfs_edges

    class _Broken:
        async def get_neighbors_for_nodes_by_relationship_types(self, **kwargs: object) -> list:
            raise RuntimeError("neo4j is down")

    with pytest.raises(RuntimeError, match="neo4j is down"):
        await _bfs_edges(_Broken(), "a", "b", max_depth=2, relations=["CALLS"])
