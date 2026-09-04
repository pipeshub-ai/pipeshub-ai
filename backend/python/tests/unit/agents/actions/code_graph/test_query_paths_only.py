"""`query_code_graph` answers "what is in this place", and nothing else.

It previously also took a symbol and returned its edges, which overlapped
`get_neighbour` entirely. In a live trace the agent used it as
`select="...events.py#method:EventProcessor._check_duplicate_by_md5"` with
`depth=0` and got back the address it had just typed -- the only two answers a
symbol select can produce are that echo, or edges, and edges belong elsewhere.
"""
import pytest

from app.agents.actions.code_graph.query import query_code_graph_impl
from app.config.constants.arangodb import CollectionNames

CTX = {"graph_provider": None, "org_id": "org-1", "user_id": "user-1", "connector_id": "conn-1"}


@pytest.fixture
def query_graph():
    """One source file holding one method — enough to reach `_degrees`."""

    class _Graph:
        async def get_nodes_by_field_prefix(self, collection, field_name, prefix,
                                            filters=None, limit=None):
            if collection != CollectionNames.CODE_FILES.value:
                return []
            return [{"_key": "rec-1", "filePath": "app/events/events.py", "fileRole": "source"}]

        async def get_nodes_by_field_in(self, collection, field_name, field_values,
                                        return_fields=None):
            return [{
                "_key": "b1", "id": "b1", "orgId": "org-1", "connectorId": "conn-1",
                "recordId": "rec-1", "qualifiedName": "method:EventProcessor.on_event",
                "kind": "method", "name": "on_event", "startLine": 1, "endLine": 9,
            }]

        async def get_neighbors_for_nodes_by_relationship_types(self, **kwargs):
            return [{"anchorKey": "b1", "key": "b2", "collection": "blocks"}]

        async def get_accessible_record_ids(self, *a, **k):
            return None

        async def check_record_access_with_details(self, *a, **k):
            return {"record": {"_key": "rec-1"}}

        async def get_nodes_by_filters(self, collection, filters, return_fields=None,
                                       transaction=None):
            return []

    return _Graph()


@pytest.fixture
def empty_graph():
    """A repo the prefix scan finds nothing in."""

    class _Graph:
        async def get_nodes_by_field_prefix(self, **kwargs):
            return []

        async def get_nodes_by_field_in(self, **kwargs):
            return []

        async def get_accessible_record_ids(self, *a, **k):
            return None

        async def get_nodes_by_filters(self, **kwargs):
            return []

    return _Graph()


@pytest.fixture
def repo_root_graph():
    """Paths are stored repo-relative, so `backend` carries no slash."""

    class _Graph:
        async def get_nodes_by_field_prefix(self, collection, field_name, prefix,
                                            filters=None, limit=None):
            paths = ["backend/python/app/events/events.py", "backend/nodejs/apps/src/app.ts"]
            return [{"_key": p, "filePath": p, "fileRole": "source"}
                    for p in paths if p.startswith(prefix)]

    return _Graph()


async def _select(select):
    return await query_code_graph_impl(**CTX, select=select)


class TestRejectedShapes:
    @pytest.mark.asyncio
    async def test_a_symbol_locator_is_rejected(self) -> None:
        r = await _select("app/events/events.py#method:EventProcessor.on_event")
        assert "read_code" in r["error"] and "get_neighbour" in r["error"], (
            "the error is the only place the agent learns where that job moved"
        )
        assert "nodes" not in r

    @pytest.mark.asyncio
    @pytest.mark.parametrize("select", ["deduplication md5", "function:main"])
    async def test_free_text_is_rejected_without_a_lookup(self, select) -> None:
        """A space or a `:` cannot be a directory, so no graph call is needed --
        `graph_provider` is None here and reaching it would raise."""
        r = await _select(select)
        assert "is not a path" in r["error"]
        assert "knowledge base" in r["error"], "must point at where a path comes from"

    @pytest.mark.asyncio
    async def test_a_bare_name_that_is_no_directory_is_rejected(self, empty_graph) -> None:
        """`stream_record` is shaped exactly like a top-level directory, so the
        listing is what settles it."""
        r = await query_code_graph_impl(
            **{**CTX, "graph_provider": empty_graph}, select="stream_record"
        )
        assert "is not a path" in r["error"]

    @pytest.mark.asyncio
    async def test_empty_select_is_rejected(self) -> None:
        assert "directory or a file path" in (await _select("  "))["error"]


class TestAcceptedShapes:
    """These reach the graph; with no provider they raise rather than reject,
    which is how we tell "accepted" from "refused at the door"."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("select", [
        "backend/python/app/events/",      # directory
        "backend/python/app/events",       # directory, no trailing slash
        "backend/python/app/events.py",    # file
        "backend/python/app/**",           # glob
    ])
    async def test_paths_are_not_rejected(self, select) -> None:
        with pytest.raises(AttributeError):
            await _select(select)

    @pytest.mark.asyncio
    async def test_a_top_level_directory_lists_its_children(self, repo_root_graph) -> None:
        r = await query_code_graph_impl(
            **{**CTX, "graph_provider": repo_root_graph}, select="backend"
        )
        assert r["resolved_as"] == "directory"
        assert [d["select"] for d in r["directories"]] == ["backend/nodejs/", "backend/python/"]


class TestFileSelectRunsEndToEnd:
    """The rejection tests above all return before touching the graph, so they
    passed while `_degrees` raised `NameError: Counter` in production. This one
    walks the whole path."""

    @pytest.mark.asyncio
    async def test_a_file_returns_symbol_addresses_and_no_edges(self, query_graph) -> None:
        r = await query_code_graph_impl(
            graph_provider=query_graph, org_id="org-1", user_id="user-1",
            connector_id="conn-1", select="app/events/events.py",
        )
        assert "error" not in r
        assert r["matches"] == 1
        assert r["nodes"][0]["qualified_name"] == "method:EventProcessor.on_event"
        assert "edges" not in r, "the tool no longer returns relationships"
        assert "get_neighbour" in r["next"], "must say where edge work moved"


@pytest.mark.asyncio
async def test_edge_parameters_are_gone() -> None:
    """`relations`/`direction`/`depth`/`group_by` moved to get_neighbour; a
    caller still passing them should fail loudly rather than have them ignored."""
    with pytest.raises(TypeError):
        await query_code_graph_impl(**CTX, select="a/b.py", depth=2, relations=["CALLS"])
