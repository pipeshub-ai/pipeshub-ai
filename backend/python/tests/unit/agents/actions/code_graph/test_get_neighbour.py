"""`get_neighbour` walks any code relation, not just calls.

Its predecessor hardcoded CALLS and took 'caller'/'callee', which only mean
something for a call edge -- there is no "caller" of an INHERITS edge. It was
also a strict subset of `query_code_graph(depth=1, relations=['CALLS'])`, so
the model never had a reason to reach for it.
"""
import pytest

from app.agents.actions.code_graph.ops import (
    CODE_RELATIONS,
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
    async def test_defaults_to_every_code_relation(self, graph) -> None:
        result = await _neighbours(graph, direction="any")
        assert result["edge_types"] == CODE_RELATIONS

    @pytest.mark.asyncio
    async def test_structural_edges_are_included_by_default(self, graph) -> None:
        """METHOD/CONTAINS are how a walk from a method reaches its class."""
        result = await get_neighbour_impl(
            graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
            file_path="src/b.py", qualified_name="class:Thing", direction="outbound",
        )
        assert [n["qualified_name"] for n in result["neighbors"]] == ["method:run"]
        assert result["chain"] == [{
            "file_path": "src/b.py",
            "qualified_name": "method:run",
            "relation": "CONTAINS",
        }]
        assert "get_neighbour" in result["note"]

    @pytest.mark.asyncio
    async def test_calls_only_filter_gets_a_note_to_widen(self, graph) -> None:
        result = await get_neighbour_impl(
            graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
            file_path="src/a.py", qualified_name="function:caller",
            direction="outbound", edge_types=["CALLS"],
        )
        assert "chain" not in result
        assert "CALLS-only" in result["note"]
        assert [n["qualified_name"] for n in result["neighbors"]] == ["function:target"]

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


class TestWholeFile:
    """`qualified_name` omitted walks every symbol in the file at once.

    This is the shape a caller has straight after a knowledge search: a path,
    and no idea yet which symbol matters. Without it the only file-scoped move
    is a `query_code_graph` glob, which returns a ranked sample of a subtree
    rather than the file's actual edges -- and in live traces the agent reached
    for the glob 46.7% of the time and the file's own edges almost never.
    """

    @pytest.mark.asyncio
    async def test_walks_every_symbol_and_names_the_one_each_edge_came_from(
        self, graph
    ) -> None:
        result = await get_neighbour_impl(
            graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
            file_path="src/b.py", direction="outbound", edge_types=["INHERITS"],
        )
        assert result["file_path"] == "src/b.py"
        assert result["symbols_walked"] == 3, "target, Thing and run"
        assert [(n["from"], n["qualified_name"]) for n in result["neighbors"]] == [
            ("function:target", "class:Thing"),
        ]

    @pytest.mark.asyncio
    async def test_no_symbol_key_is_reported_for_a_named_walk(self, graph) -> None:
        """`symbol` and `from` are the two shapes' distinguishing fields; a
        caller reading one result must not have to guess which mode ran."""
        named = await _neighbours(graph, direction="outbound")
        assert "from" not in named["neighbors"][0]
        assert "symbols_walked" not in named

    @pytest.mark.asyncio
    async def test_an_imports_block_still_carries_the_files_dependencies(self) -> None:
        """`imports` is a filler kind, but it owns every IMPORTS_FROM edge --
        excluding it would make "what does this file depend on" unanswerable."""
        graph = FakeGraphProvider()
        graph.edges.append({
            "_from": "blocks/k_panel_imports", "_to": "blocks/k_client",
            "relationshipType": "IMPORTS_FROM",
        })
        result = await get_neighbour_impl(
            graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
            file_path="web/ui/panel.ts", direction="outbound",
            edge_types=["IMPORTS_FROM"],
        )
        assert [n["from"] for n in result["neighbors"]] == ["imports:run"]

    @pytest.mark.asyncio
    async def test_the_budget_is_shared_across_symbols_not_taken_by_the_first(
        self,
    ) -> None:
        """A hub symbol listed first must not spend the whole limit: the point
        of a file walk is coverage of the file, not depth on one symbol."""
        graph = FakeGraphProvider()
        graph.edges = [
            {"_from": "blocks/k_target", "_to": f"blocks/k_hub_{i}",
             "relationshipType": "CALLS"} for i in range(5)
        ] + [{"_from": "blocks/k_class", "_to": "blocks/k_caller",
              "relationshipType": "CALLS"}]
        for i in range(5):
            graph.blocks[f"k_hub_{i}"] = dict(
                graph.blocks["k_caller"], _key=f"k_hub_{i}", id=f"k_hub_{i}",
                qualifiedName=f"function:hub_{i}", connectorId=CONN,
            )
        result = await get_neighbour_impl(
            graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
            file_path="src/b.py", direction="outbound", edge_types=["CALLS"],
            limit=2,
        )
        assert {n["from"] for n in result["neighbors"]} == {
            "function:target", "class:Thing",
        }, "one slot each before the hub gets a second"
        assert result["truncated"] is True

    @pytest.mark.asyncio
    async def test_an_unindexed_path_is_an_error_naming_the_path(self, graph) -> None:
        result = await get_neighbour_impl(
            graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
            file_path="src/nope.py",
        )
        assert "src/nope.py" in result["error"]
        assert "neighbors" not in result

    @pytest.mark.asyncio
    async def test_a_denied_file_looks_like_an_empty_walk(self) -> None:
        result = await get_neighbour_impl(
            graph_provider=FakeGraphProvider(deny_records=["rec-b"]),
            connector_id=CONN, org_id=ORG, user_id=USER,
            file_path="src/b.py", direction="outbound",
        )
        assert result == {
            "file_path": "src/b.py", "direction": "outbound", "neighbors": [],
        }
