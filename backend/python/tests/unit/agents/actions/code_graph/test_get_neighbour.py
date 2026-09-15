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

from .conftest import BLOCKS, CODE_FILES
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
    async def test_an_unindexed_path_looks_like_a_denied_one(self, graph) -> None:
        """An error naming the path for a miss, next to an empty walk for a
        denial, would tell the caller which of the two it hit."""
        missing = await get_neighbour_impl(
            graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
            file_path="src/nope.py", direction="outbound",
        )
        denied = await get_neighbour_impl(
            graph_provider=FakeGraphProvider(deny_records=["rec-b"]),
            connector_id=CONN, org_id=ORG, user_id=USER,
            file_path="src/b.py", direction="outbound",
        )
        assert "error" not in missing
        assert missing == {**denied, "file_path": "src/nope.py"}

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

    @pytest.mark.asyncio
    async def test_an_unknown_symbol_looks_like_a_denied_one(self, graph) -> None:
        missing = await get_neighbour_impl(
            graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
            file_path="src/a.py", qualified_name="function:nope", direction="outbound",
        )
        denied = await _neighbours(FakeGraphProvider(deny_records=["rec-a"]), direction="outbound")
        assert missing == denied == {"symbol": None, "direction": "outbound", "neighbors": []}


class TestProviderFailures:
    """A lookup that raises must not come back as an empty success: `neighbors:
    []` reads as "nothing here", and a missing role table reads as "not a
    test file"."""

    @pytest.mark.asyncio
    async def test_a_failed_block_load_raises_instead_of_reporting_no_neighbours(self) -> None:
        class Broken(FakeGraphProvider):
            async def get_nodes_by_field_in(
                self, collection, field_name, field_values, **kw
            ) -> list[dict]:
                if collection == BLOCKS:
                    raise RuntimeError("graph is down")
                return await super().get_nodes_by_field_in(
                    collection, field_name, field_values, **kw
                )

        with pytest.raises(RuntimeError, match="graph is down"):
            await _neighbours(Broken(), direction="outbound")

    @pytest.mark.asyncio
    async def test_a_failed_role_lookup_raises_instead_of_failing_open(self) -> None:
        class Broken(FakeGraphProvider):
            async def get_nodes_by_field_in(
                self, collection, field_name, field_values, **kw
            ) -> list[dict]:
                if collection == CODE_FILES:
                    raise RuntimeError("graph is down")
                return await super().get_nodes_by_field_in(
                    collection, field_name, field_values, **kw
                )

        with pytest.raises(RuntimeError, match="graph is down"):
            await _neighbours(Broken(), direction="outbound")


class TestPagination:
    """`limit` used to be a hard cap with `truncated: true` and no way to see
    the rest: a 167-neighbour depth-2 walk in a live trace came back as one
    12k-token blob. `offset` pages it, and `next` tells the model where the
    following page starts, the same way `read_code` reports a stopped file."""

    async def _page(self, graph, **kw):
        return await get_neighbour_impl(
            graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
            file_path="src/b.py", direction="any", **kw,
        )

    @pytest.mark.asyncio
    async def test_pages_concatenate_to_the_unpaginated_walk(self, graph) -> None:
        full = await self._page(graph)
        total = full["total"]
        assert total == len(full["neighbors"]) and total >= 2, "fixture must hold >=2 neighbours"

        pages, offset = [], 0
        while True:
            page = await self._page(graph, limit=1, offset=offset)
            assert page["total"] == total
            assert page["offset"] == offset
            assert len(page["neighbors"]) == 1
            pages.extend(page["neighbors"])
            if not page["truncated"]:
                assert "next" not in page
                break
            assert f"offset={offset + 1}" in page["next"]
            offset += 1
        # Pages partition rank order; the full result is displayed anchor-
        # grouped. Same edges, so compare as a multiset, not a sequence.
        key = lambda n: (n["file_path"], n["qualified_name"], n["relation"])
        assert sorted(map(key, pages)) == sorted(map(key, full["neighbors"]))

    @pytest.mark.asyncio
    async def test_offset_past_the_end_is_empty_not_truncated(self, graph) -> None:
        full = await self._page(graph)
        page = await self._page(graph, offset=full["total"] + 5)
        assert page["neighbors"] == []
        assert page["truncated"] is False
        assert "next" not in page

    @pytest.mark.asyncio
    async def test_negative_offset_is_rejected(self, graph) -> None:
        assert "error" in await self._page(graph, offset=-1)


class TestTraversalCap:
    """The provider's `limit` caps rows across the whole frontier. A flat 200
    once let a hub with 300 callers come back as 200 rows and `truncated:
    false`, which the prompt tells the model to read as a complete set."""

    @staticmethod
    def _hub_with_callers(count: int) -> FakeGraphProvider:
        graph = FakeGraphProvider()
        graph.edges = [
            {"_from": f"blocks/k_in_{i}", "_to": "blocks/k_target", "relationshipType": "CALLS"}
            for i in range(count)
        ]
        for i in range(count):
            graph.blocks[f"k_in_{i}"] = dict(
                graph.blocks["k_caller"], _key=f"k_in_{i}", id=f"k_in_{i}",
                qualifiedName=f"function:in_{i}", connectorId=CONN,
            )
        return graph

    async def _inbound(self, graph: FakeGraphProvider, **kwargs: object) -> dict:
        return await get_neighbour_impl(
            graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
            file_path="src/b.py", qualified_name="function:target",
            direction="inbound", edge_types=["CALLS"], **kwargs,
        )

    @pytest.mark.asyncio
    async def test_more_callers_than_the_old_flat_cap_is_still_a_full_set(self) -> None:
        result = await self._inbound(self._hub_with_callers(250), limit=1000)
        assert result["total"] == 250
        assert len(result["neighbors"]) == 250
        assert result["truncated"] is False
        assert "fanout_capped" not in result

    @pytest.mark.asyncio
    async def test_hitting_the_row_cap_is_reported_not_silent(self, monkeypatch) -> None:
        from app.agents.actions.code_graph import ops

        monkeypatch.setattr(ops, "_ROWS_PER_ANCHOR", 3)
        result = await self._inbound(self._hub_with_callers(5), limit=100)
        assert result["total"] == 3, "rows past the cap are not fetched"
        assert result["fanout_capped"] is True
        assert result["truncated"] is True, "a page with room left is still not complete"
        assert "lower bound" in result["next"]

    @pytest.mark.asyncio
    async def test_exactly_the_cap_is_complete(self, monkeypatch) -> None:
        """The sentinel row is what separates 'exactly N' from 'more than N'."""
        from app.agents.actions.code_graph import ops

        monkeypatch.setattr(ops, "_ROWS_PER_ANCHOR", 5)
        result = await self._inbound(self._hub_with_callers(5), limit=100)
        assert result["total"] == 5
        assert result["truncated"] is False
        assert "fanout_capped" not in result

    @pytest.mark.asyncio
    async def test_a_frontier_wider_than_the_fanout_is_reported(self, monkeypatch) -> None:
        from app.agents.actions.code_graph import ops

        monkeypatch.setattr(ops, "_NEIGHBOUR_FANOUT", 2)
        graph = self._hub_with_callers(3)
        # Hop 2 would expand three nodes; only two are walked.
        result = await self._inbound(graph, depth=2, limit=100)
        assert result["fanout_capped"] is True
        assert result["truncated"] is True


class TestNameOnlyAnchor:
    """A name with no file is the only way in for a caller that has not been
    handed an address. Every other supplier of one -- a search hit, a listing --
    is big enough to be compacted out of context before it gets used, which is
    what left this tool uncalled on set-shaped questions."""

    @staticmethod
    async def _by_name(graph, name, **kwargs):
        return await get_neighbour_impl(
            graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
            qualified_name=name, **kwargs,
        )

    @pytest.mark.asyncio
    async def test_a_unique_name_is_walked_without_a_path(self, graph) -> None:
        result = await self._by_name(graph, "caller", direction="outbound")
        assert result["symbol"]["qualified_name"] == "function:caller"
        assert result["symbol"]["file_path"] == "src/a.py", "the path is resolved, not required"
        assert [n["qualified_name"] for n in result["neighbors"]] == ["function:target"]

    @pytest.mark.asyncio
    async def test_an_ambiguous_name_returns_addresses_and_walks_nothing(self, graph) -> None:
        """Merging the edges of several same-named symbols would invent a
        caller set that looks exactly like a real one."""
        result = await self._by_name(graph, "run")
        assert "neighbors" not in result
        assert result["symbol"] is None
        assert result["matched_name"] == "run"
        addresses = {(c["file_path"], c["qualified_name"]) for c in result["candidates"]}
        assert addresses == {
            ("src/b.py", "method:run"),
            ("web/ui/panel.ts", "imports:run"),
        }
        assert all("degree" in c for c in result["candidates"])

    @pytest.mark.asyncio
    async def test_a_fully_spelled_name_beats_its_namesakes(self, graph) -> None:
        """`method:run` is unambiguous even though `imports:run` shares a name,
        so it must walk rather than ask which was meant."""
        result = await self._by_name(graph, "method:run", direction="any")
        assert result["symbol"]["qualified_name"] == "method:run"
        assert "candidates" not in result

    @pytest.mark.asyncio
    async def test_an_owner_prefix_finds_the_symbol_but_does_not_narrow(self, graph) -> None:
        """`Thing.run` is not the form the indexer stored, so it matches on the
        last segment and the owner is not used to disambiguate — the caller
        still gets addresses rather than a walk of the wrong symbol."""
        result = await self._by_name(graph, "Thing.run", direction="any")
        names = {c["qualified_name"] for c in result["candidates"]}
        assert names == {"method:run", "imports:run"}

    @pytest.mark.asyncio
    async def test_an_unknown_name_is_empty_not_an_error(self, graph) -> None:
        result = await self._by_name(graph, "no_such_symbol")
        assert result["symbol"] is None
        assert result["neighbors"] == []

    @pytest.mark.asyncio
    async def test_no_anchor_at_all_is_rejected(self, graph) -> None:
        result = await get_neighbour_impl(
            graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_candidates_exclude_records_the_user_cannot_read(self) -> None:
        """A name lookup spans the repo, so it must not become a way to learn
        that an unreadable file holds a symbol."""
        graph = FakeGraphProvider(deny_records=["rec-c"])
        result = await self._by_name(graph, "run")
        assert result["symbol"]["qualified_name"] == "method:run", (
            "with the denied namesake gone the name is unambiguous"
        )
