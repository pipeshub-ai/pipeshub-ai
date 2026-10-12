"""The `query_code_graph` primitive: "what is here", for a path.

A directory selector lists its children, a file or glob selector lists the
symbols those files define. Edges are `get_neighbour`'s job and a symbol's
source is `read_code`'s; the rejection of those shapes lives in
`test_query_paths_only.py`. These tests cover what a path resolves to, how the
result is gated, ranked and capped, and that a denial looks like a miss.
"""
import pytest

from app.agents.actions.code_graph.query import TEST_ROLE, query_code_graph_impl

from .conftest import CONN, ORG, USER, QueryGraphProvider

pytestmark = pytest.mark.asyncio


def _ctx(graph, user=USER, connector=CONN):
    return {"graph_provider": graph, "org_id": ORG, "user_id": user, "connector_id": connector}


def _names(result: dict) -> set[str]:
    return {n["qualified_name"] for n in result["nodes"]}


class TestSelectorResolution:
    async def test_path_glob(self, query_graph) -> None:
        result = await query_code_graph_impl(**_ctx(query_graph), select="web/ui/*")
        assert result["resolved_as"] == "path"
        assert _names(result) == {"function:renderPanel", "imports:run"}

    async def test_literal_file_path(self, query_graph) -> None:
        result = await query_code_graph_impl(**_ctx(query_graph), select="src/b.py")
        assert result["resolved_as"] == "path"
        assert _names(result) == {"function:target", "class:Thing", "method:run"}
        assert result["connector_id"] == CONN, "every follow-up call needs it back"

    async def test_glob_does_not_leak_its_prefix_siblings(self, query_graph) -> None:
        # The DB-side filter is a prefix, so `web/api/*` must not return web/ui.
        result = await query_code_graph_impl(**_ctx(query_graph), select="web/api/*")
        assert _names(result) == {"function:fetchTarget"}

    async def test_a_directory_lists_its_children(self, query_graph) -> None:
        result = await query_code_graph_impl(**_ctx(query_graph), select="web/")
        assert result["resolved_as"] == "directory"
        assert [d["select"] for d in result["directories"]] == ["web/api/", "web/ui/"]
        assert result["files"] == []

    async def test_a_leaf_directory_lists_its_files(self, query_graph) -> None:
        result = await query_code_graph_impl(**_ctx(query_graph), select="web/ui")
        assert result["resolved_as"] == "directory"
        assert result["directories"] == []
        assert [f["select"] for f in result["files"]] == ["web/ui/panel.ts"]

    async def test_no_match_is_empty_not_an_error(self, query_graph) -> None:
        result = await query_code_graph_impl(**_ctx(query_graph), select="src/nosuchfile.py")
        assert result["matches"] == 0
        assert result["nodes"] == [] and "error" not in result
        assert "select='src/'" in result["hint"], "a miss must name the next call"


class TestNoise:
    async def test_filler_kinds_stay_reachable_by_path(self, query_graph) -> None:
        result = await query_code_graph_impl(**_ctx(query_graph), select="web/ui/panel.ts")
        assert "imports:run" in _names(result)

    async def test_kinds_filter_the_symbols(self, query_graph) -> None:
        result = await query_code_graph_impl(
            **_ctx(query_graph), select="src/b.py", kinds=["method"])
        assert _names(result) == {"method:run"}
        assert result["matches"] == 1


class TestRanking:
    async def test_hubs_come_first_and_carry_their_degree(self, query_graph) -> None:
        """`target` and `Thing` touch two edges each, `run` one. Without the
        degree a 60-file selection reads as a flat list in scan order."""
        result = await query_code_graph_impl(**_ctx(query_graph), select="src/b.py")
        assert [n["degree"] for n in result["nodes"]] == [2, 2, 1]
        assert result["nodes"][-1]["qualified_name"] == "method:run"


class TestTests:
    async def test_test_files_are_hidden_unless_asked_for(self) -> None:
        graph = QueryGraphProvider(file_roles={"rec-a": TEST_ROLE})
        hidden = await query_code_graph_impl(**_ctx(graph), select="src/*")
        assert _names(hidden) == {"function:target", "class:Thing", "method:run"}
        shown = await query_code_graph_impl(**_ctx(graph), select="src/*", include_tests=True)
        assert "function:caller" in _names(shown)


class TestTruncation:
    async def test_the_true_total_is_reported(self, query_graph) -> None:
        result = await query_code_graph_impl(**_ctx(query_graph), select="src/b.py", limit=1)
        assert result["truncated"] is True
        assert result["matches"] == 3
        assert len(result["nodes"]) == 1

    async def test_a_complete_result_says_so(self, query_graph) -> None:
        result = await query_code_graph_impl(**_ctx(query_graph), select="src/b.py", limit=50)
        assert result["truncated"] is False


class TestArgumentValidation:
    @pytest.mark.parametrize("kwargs", [
        {"select": "   "},
        {"select": "src/b.py", "connector": ""},
    ])
    async def test_rejected(self, query_graph, kwargs) -> None:
        connector = kwargs.pop("connector", CONN)
        result = await query_code_graph_impl(
            **_ctx(query_graph, connector=connector), **kwargs)
        assert "error" in result

    @pytest.mark.parametrize("limit", [0, 99_999])
    async def test_limit_is_clamped_not_rejected(self, query_graph, limit) -> None:
        result = await query_code_graph_impl(**_ctx(query_graph), select="src/b.py", limit=limit)
        assert "error" not in result
        assert 1 <= len(result["nodes"]) <= 3


class TestAccessControl:
    """A denial and a miss must be indistinguishable.

    Returning `matches: 3, nodes: []` would tell the agent that the file exists
    and how much is in it, which is the leak the empty result exists to prevent.
    """

    @staticmethod
    def _without_select(result: dict) -> dict:
        return {k: v for k, v in result.items() if k not in ("select", "hint")}

    async def test_denied_user_sees_a_plain_miss(self, query_graph) -> None:
        denied = await query_code_graph_impl(
            **_ctx(query_graph, user="intruder"), select="src/b.py")
        missing = await query_code_graph_impl(
            **_ctx(query_graph), select="src/nosuchfile.py")
        assert denied["matches"] == 0 and "error" not in denied
        assert self._without_select(denied) == self._without_select(missing)

    async def test_denied_user_gets_no_directory_listing(self, query_graph) -> None:
        """A listing is built from `codeFiles`, which carries no permissions;
        the gate has to come from the owning records."""
        result = await query_code_graph_impl(**_ctx(query_graph, user="intruder"), select="web/")
        assert "directories" not in result
        assert result["matches"] == 0 and "error" not in result

    async def test_an_unreadable_file_is_dropped_from_the_listing(self) -> None:
        graph = QueryGraphProvider(deny_records={"rec-d"})
        result = await query_code_graph_impl(**_ctx(graph), select="web/")
        assert [d["select"] for d in result["directories"]] == ["web/ui/"]

    async def test_another_connectors_files_are_not_listed(self, query_graph) -> None:
        """Two repos can share `src/`; the caller named one of them."""
        result = await query_code_graph_impl(**_ctx(query_graph, connector="conn-2"), select="web/")
        assert "directories" not in result
        assert result["matches"] == 0 and "error" not in result

    async def test_a_failed_permission_lookup_does_not_fail_open(self) -> None:
        class Broken(QueryGraphProvider):
            async def get_accessible_virtual_record_ids(self, *a: object, **k: object) -> None:
                raise RuntimeError("neo4j is down")

            async def check_record_access_with_details(self, *a: object, **k: object) -> None:
                raise RuntimeError("neo4j is down")

        listing = await query_code_graph_impl(**_ctx(Broken()), select="web/")
        assert "directories" not in listing and listing["matches"] == 0
        symbols = await query_code_graph_impl(**_ctx(Broken()), select="src/b.py")
        assert symbols["nodes"] == [] and symbols["matches"] == 0
