"""The three code-graph tools.

Exercises the `_impl` coroutines directly, so the `@tool` decorator is not in the
way and each behaviour is pinned on its own.
"""
import pytest

from app.agents.actions.code_graph.ops import (
    find_call_neighbors_impl,
    find_symbol_path_impl,
    get_symbol_code_impl,
)

from .conftest import ORG, USER, FakeBlobStore, FakeGraphProvider

pytestmark = pytest.mark.asyncio


def _ctx(graph, user=USER):
    return {"graph_provider": graph, "org_id": ORG, "user_id": user}


class TestFindCallNeighbors:
    async def test_caller_walks_inbound(self, graph):
        result = await find_call_neighbors_impl(
            **_ctx(graph), file_path="src/b.py", symbol_id="src_b_target",
            direction="caller",
        )
        assert [n["symbol_id"] for n in result["neighbors"]] == ["src_a_caller"]
        assert result["neighbors"][0]["file_path"] == "src/a.py"
        assert result["neighbors"][0]["relation"] == "CALLS"

    async def test_callee_walks_outbound(self, graph):
        result = await find_call_neighbors_impl(
            **_ctx(graph), file_path="src/a.py", symbol_id="src_a_caller",
            direction="callee",
        )
        assert [n["symbol_id"] for n in result["neighbors"]] == ["src_b_target"]

    async def test_directions_are_disjoint(self, graph):
        callers = await find_call_neighbors_impl(
            **_ctx(graph), file_path="src/a.py", symbol_id="src_a_caller", direction="caller")
        callees = await find_call_neighbors_impl(
            **_ctx(graph), file_path="src/a.py", symbol_id="src_a_caller", direction="callee")
        assert callers["neighbors"] == []
        assert callees["neighbors"] != []

    async def test_non_call_relations_are_excluded(self, graph):
        # k_target INHERITS k_class, which is not a call.
        result = await find_call_neighbors_impl(
            **_ctx(graph), file_path="src/b.py", symbol_id="src_b_target", direction="callee")
        assert result["neighbors"] == []

    async def test_unknown_symbol_is_an_error(self, graph):
        result = await find_call_neighbors_impl(
            **_ctx(graph), file_path="src/b.py", symbol_id="nope", direction="caller")
        assert "error" in result

    async def test_bad_direction_rejected(self, graph):
        result = await find_call_neighbors_impl(
            **_ctx(graph), file_path="src/b.py", symbol_id="src_b_target", direction="sideways")
        assert "error" in result


class TestGetSymbolCode:
    async def test_returns_source_from_the_blob(self, graph, blob):
        result = await get_symbol_code_impl(
            **_ctx(graph), blob_store=blob,
            file_path="src/b.py", symbol_id="src_b_target",
        )
        assert result["code"] == "def target():\n    return 1\n"
        assert result["file_path"] == "src/b.py"
        assert result["symbol_id"] == "src_b_target"

    async def test_does_not_leak_the_subtokens_padding(self, graph, blob):
        result = await get_symbol_code_impl(
            **_ctx(graph), blob_store=blob,
            file_path="src/b.py", symbol_id="src_b_target",
        )
        assert "subtokens" not in result["code"]

    async def test_missing_blob_is_an_error_not_a_crash(self, graph):
        result = await get_symbol_code_impl(
            **_ctx(graph), blob_store=FakeBlobStore(records={}),
            file_path="src/b.py", symbol_id="src_b_target",
        )
        assert "error" in result and "code" not in result

    async def test_symbol_absent_from_the_blob(self, graph, blob):
        # Present in the graph, missing from stored content.
        result = await get_symbol_code_impl(
            **_ctx(graph), blob_store=blob,
            file_path="src/b.py", symbol_id="src_b_thing_run",
        )
        assert "error" in result and "code" not in result


class TestFindSymbolPath:
    async def test_reports_each_hop_with_relation_and_direction(self, graph):
        result = await find_symbol_path_impl(
            **_ctx(graph),
            file_path_a="src/a.py", symbol_id_a="src_a_caller",
            file_path_b="src/b.py", symbol_id_b="src_b_thing_run",
        )
        assert result["found"] is True
        chain = [(h["from"]["symbol_id"], h["relation"], h["direction"], h["to"]["symbol_id"])
                 for h in result["hops"]]
        assert chain == [
            ("src_a_caller", "CALLS", "outbound", "src_b_target"),
            ("src_b_target", "INHERITS", "outbound", "src_b_thing"),
            ("src_b_thing", "CONTAINS", "outbound", "src_b_thing_run"),
        ]

    async def test_traverses_non_call_relations(self, graph):
        # class -> method is CONTAINS only; a call-only search would find nothing.
        result = await find_symbol_path_impl(
            **_ctx(graph),
            file_path_a="src/b.py", symbol_id_a="src_b_thing",
            file_path_b="src/b.py", symbol_id_b="src_b_thing_run",
        )
        assert result["found"] is True
        assert result["hops"][0]["relation"] == "CONTAINS"

    async def test_follows_edges_against_their_direction(self, graph):
        # b -> a reverses the CALLS edge; an undirected search still connects them.
        result = await find_symbol_path_impl(
            **_ctx(graph),
            file_path_a="src/b.py", symbol_id_a="src_b_target",
            file_path_b="src/a.py", symbol_id_b="src_a_caller",
        )
        assert result["found"] is True
        assert result["hops"][0]["direction"] == "inbound"

    async def test_same_symbol_is_a_zero_hop_path(self, graph):
        result = await find_symbol_path_impl(
            **_ctx(graph),
            file_path_a="src/a.py", symbol_id_a="src_a_caller",
            file_path_b="src/a.py", symbol_id_b="src_a_caller",
        )
        assert result == {"found": True, "hops": []}

    async def test_depth_limit_stops_the_search(self, graph):
        result = await find_symbol_path_impl(
            **_ctx(graph),
            file_path_a="src/a.py", symbol_id_a="src_a_caller",
            file_path_b="src/b.py", symbol_id_b="src_b_thing_run",
            max_depth=1,
        )
        assert result["found"] is False


class TestAccessControl:
    """A denial must look like absence, not like a refusal.

    Reporting "you may not read this" tells the agent the record exists, which is
    itself a leak.
    """

    async def test_neighbors_empty_for_a_user_without_access(self, graph):
        result = await find_call_neighbors_impl(
            **_ctx(graph, user="intruder"),
            file_path="src/b.py", symbol_id="src_b_target", direction="caller",
        )
        assert result["neighbors"] == []
        assert "error" not in result

    async def test_source_is_not_returned_without_access(self, graph, blob):
        result = await get_symbol_code_impl(
            **_ctx(graph, user="intruder"), blob_store=blob,
            file_path="src/b.py", symbol_id="src_b_target",
        )
        assert "code" not in result

    async def test_path_hidden_when_an_endpoint_is_unreadable(self, graph):
        result = await find_symbol_path_impl(
            **_ctx(graph, user="intruder"),
            file_path_a="src/a.py", symbol_id_a="src_a_caller",
            file_path_b="src/b.py", symbol_id_b="src_b_target",
        )
        assert result == {"found": False, "hops": []}

    async def test_neighbours_in_an_unreadable_record_are_filtered_out(self):
        # The anchor is readable; the neighbour's record is not.
        graph = FakeGraphProvider(deny_records={"rec-b"})
        result = await find_call_neighbors_impl(
            **_ctx(graph), file_path="src/a.py", symbol_id="src_a_caller", direction="callee",
        )
        assert result["neighbors"] == []

    async def test_path_through_an_unreadable_hop_is_not_returned(self):
        graph = FakeGraphProvider(deny_records={"rec-b"})
        result = await find_symbol_path_impl(
            **_ctx(graph),
            file_path_a="src/a.py", symbol_id_a="src_a_caller",
            file_path_b="src/b.py", symbol_id_b="src_b_thing_run",
        )
        assert result["found"] is False
