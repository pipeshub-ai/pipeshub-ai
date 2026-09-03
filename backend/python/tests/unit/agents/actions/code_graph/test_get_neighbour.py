"""`get_neighbour` walks any code relation, not just calls.

Its predecessor hardcoded CALLS and took 'caller'/'callee', which only mean
something for a call edge -- there is no "caller" of an INHERITS edge. It was
also a strict subset of `query_code_graph(depth=1, relations=['CALLS'])`, so
the model never had a reason to reach for it.
"""
import pytest

from app.agents.actions.code_graph.ops import (
    CROSS_FILE_RELATIONS,
    get_neighbour_impl,
)

from .conftest import FakeGraphProvider as _BaseFake

ORG = "org-1"
USER = "user-1"
CONN = "conn-1"


class FakeGraphProvider(_BaseFake):
    """The shared fake, with the scoping field `resolve_symbol` filters on.

    conftest's blocks predate `connector_id` scoping, so an unmodified fixture
    resolves no symbol at all.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        for block in self.blocks.values():
            block["connectorId"] = CONN


@pytest.fixture
def graph():
    return FakeGraphProvider()


async def _neighbours(graph, **kwargs):
    return await get_neighbour_impl(
        graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
        file_path="src/a.py", qualified_name="function:caller", **kwargs,
    )


class TestDirection:
    @pytest.mark.asyncio
    async def test_outbound_finds_what_the_symbol_calls(self, graph) -> None:
        result = await _neighbours(graph, direction="outbound")
        assert [n["qualified_name"] for n in result["neighbors"]] == ["function:target"]
        assert result["neighbors"][0]["relation"] == "CALLS"

    @pytest.mark.asyncio
    async def test_inbound_finds_nothing_for_a_leaf_caller(self, graph) -> None:
        result = await _neighbours(graph, direction="inbound")
        assert result["neighbors"] == []

    @pytest.mark.asyncio
    async def test_caller_callee_are_no_longer_accepted(self, graph) -> None:
        """They only describe CALLS; every other relation needs edge terms."""
        result = await _neighbours(graph, direction="caller")
        assert "error" in result
        assert "inbound" in result["error"]


class TestEdgeTypes:
    @pytest.mark.asyncio
    async def test_defaults_to_every_cross_file_relation(self, graph) -> None:
        result = await _neighbours(graph, direction="any")
        assert result["edge_types"] == CROSS_FILE_RELATIONS

    @pytest.mark.asyncio
    async def test_structural_edges_are_excluded_by_default(self, graph) -> None:
        """CONTAINS would answer "what reaches this class" with its own methods."""
        result = await get_neighbour_impl(
            graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
            file_path="src/b.py", qualified_name="class:Thing", direction="outbound",
        )
        assert [n["qualified_name"] for n in result["neighbors"]] == []

    @pytest.mark.asyncio
    async def test_structural_edges_are_reachable_when_asked_for(self, graph) -> None:
        result = await get_neighbour_impl(
            graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
            file_path="src/b.py", qualified_name="class:Thing", direction="outbound",
            edge_types=["CONTAINS"],
        )
        assert [n["qualified_name"] for n in result["neighbors"]] == ["method:run"]

    @pytest.mark.asyncio
    async def test_a_non_call_relation_is_followed(self, graph) -> None:
        result = await get_neighbour_impl(
            graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
            file_path="src/b.py", qualified_name="function:target", direction="outbound",
            edge_types=["INHERITS"],
        )
        assert [n["relation"] for n in result["neighbors"]] == ["INHERITS"]

    @pytest.mark.asyncio
    async def test_an_unknown_edge_type_is_rejected_not_ignored(self, graph) -> None:
        """Filtering it out would answer a typo with every relation instead of
        none -- a narrower request silently becoming the broadest one."""
        result = await _neighbours(graph, edge_types=["CALL"])
        assert "error" in result
        assert "CALL" in result["error"]
        assert "neighbors" not in result


class TestDepth:
    @pytest.mark.asyncio
    async def test_depth_one_stops_at_direct_neighbours(self, graph) -> None:
        result = await _neighbours(graph, direction="outbound", depth=1)
        assert len(result["neighbors"]) == 1
        assert "hop" not in result["neighbors"][0]

    @pytest.mark.asyncio
    async def test_depth_two_follows_the_chain_and_labels_hops(self, graph) -> None:
        """caller -CALLS-> target -INHERITS-> Thing."""
        result = await _neighbours(
            graph, direction="outbound", depth=2, edge_types=["CALLS", "INHERITS"]
        )
        assert [(n["qualified_name"], n["hop"]) for n in result["neighbors"]] == [
            ("function:target", 1),
            ("class:Thing", 2),
        ]

    @pytest.mark.asyncio
    async def test_depth_is_clamped_not_rejected(self, graph) -> None:
        result = await _neighbours(graph, direction="outbound", depth=99)
        assert result["depth"] == 3

    @pytest.mark.asyncio
    async def test_limit_keeps_the_nearest_and_says_it_truncated(self, graph) -> None:
        result = await _neighbours(
            graph, direction="outbound", depth=2, limit=1,
            edge_types=["CALLS", "INHERITS"],
        )
        assert [n["qualified_name"] for n in result["neighbors"]] == ["function:target"]
        assert result["truncated"] is True


class TestAccessControl:
    @pytest.mark.asyncio
    async def test_a_denied_anchor_yields_a_plain_miss(self) -> None:
        """A denial and a miss have to look identical, or the payload discloses
        that a symbol the caller cannot read exists."""
        result = await _neighbours(
            FakeGraphProvider(deny_records=["rec-a"]), direction="outbound"
        )
        assert result == {"symbol": None, "direction": "outbound", "neighbors": []}

    @pytest.mark.asyncio
    async def test_an_unreadable_neighbour_is_dropped_from_the_walk(self) -> None:
        result = await _neighbours(
            FakeGraphProvider(deny_records=["rec-b"]), direction="outbound"
        )
        assert result["neighbors"] == []
