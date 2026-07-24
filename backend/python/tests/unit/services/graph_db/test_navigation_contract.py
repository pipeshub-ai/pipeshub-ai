"""Provider-parity contract tests for the graph-navigation tools (``lookup_record``, ``navigate``).

These tools call a small set of ``IGraphDBProvider`` methods directly and assume the
ArangoDB and Neo4j implementations return the *same shape* for the same scenario —
that assumption is exactly what broke silently for ``get_record_by_weburl`` (Neo4j's
positional args were swapped relative to the interface). Each test below runs the
identical scenario against both providers, mocking only the lowest-level DB client
call (``http_client.execute_aql`` for Arango, ``client.execute_query`` for Neo4j) so
the provider's own translation/typing logic still runs.

This is deliberately scoped to the methods the navigation tools actually use
(``record_tool_helpers.py``, ``lookup_record.py``, ``navigate_tool.py``), not full
provider coverage — that's handled by the per-provider test suites.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


@pytest.fixture
def arango_provider() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(), MagicMock())
    provider.http_client = AsyncMock()
    return provider


@pytest.fixture
def neo4j_provider() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    return provider


# ---------------------------------------------------------------------------
# get_record_by_weburl — hit / miss / org-scoping
# ---------------------------------------------------------------------------


class TestGetRecordByWeburlContract:
    """This is the exact method whose Neo4j signature previously diverged
    from the interface (connector_id/web_url swapped for weburl/org_id)."""

    @pytest.mark.asyncio
    async def test_hit_returns_non_none_on_both_providers(self, arango_provider, neo4j_provider) -> None:
        arango_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"_key": "r1", "recordType": "FILE"}]
        )
        with patch("app.services.graph_db.arango.arango_http_provider.Record") as mock_record:
            mock_record.from_arango_base_record.return_value = MagicMock(id="r1")
            arango_result = await arango_provider.get_record_by_weburl("https://example.com/doc")

        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"r": {"_key": "r1"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "r1"})  # type: ignore[method-assign]
        with patch(
            "app.services.graph_db.neo4j.neo4j_provider.Record.from_arango_base_record",
            return_value=MagicMock(id="r1"),
        ):
            neo4j_result = await neo4j_provider.get_record_by_weburl("https://example.com/doc")

        assert arango_result is not None
        assert neo4j_result is not None

    @pytest.mark.asyncio
    async def test_miss_returns_none_on_both_providers(self, arango_provider, neo4j_provider) -> None:
        arango_provider.http_client.execute_aql = AsyncMock(return_value=[])
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        arango_result = await arango_provider.get_record_by_weburl("https://example.com/missing")
        neo4j_result = await neo4j_provider.get_record_by_weburl("https://example.com/missing")

        assert arango_result is None
        assert neo4j_result is None

    @pytest.mark.asyncio
    async def test_org_id_is_accepted_as_second_positional_arg_on_both_providers(
        self, arango_provider, neo4j_provider
    ) -> None:
        """Guards the interface signature itself: ``(weburl, org_id, transaction)``.
        A regression that swaps args again would pass the org_id into the wrong
        bind variable and silently stop scoping lookups to the caller's org."""
        arango_provider.http_client.execute_aql = AsyncMock(return_value=[])
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        # lookup_record.py calls this positionally: get_record_by_weburl(candidate, org_id)
        await arango_provider.get_record_by_weburl("https://example.com/doc", "org-1")
        await neo4j_provider.get_record_by_weburl("https://example.com/doc", "org-1")

        arango_bind_vars = arango_provider.http_client.execute_aql.await_args.kwargs.get(
            "bind_vars"
        ) or arango_provider.http_client.execute_aql.await_args.args[1]
        assert arango_bind_vars.get("org_id") == "org-1"

        neo4j_params = neo4j_provider.client.execute_query.await_args.kwargs["parameters"]
        assert neo4j_params.get("org_id") == "org-1"


# ---------------------------------------------------------------------------
# get_record_by_issue_key — hit / miss
# ---------------------------------------------------------------------------


class TestGetRecordByIssueKeyContract:
    @pytest.mark.asyncio
    async def test_hit_returns_non_none_on_both_providers(self, arango_provider, neo4j_provider) -> None:
        arango_provider._create_typed_record_from_arango = MagicMock(return_value=MagicMock(id="r1"))  # type: ignore[method-assign]
        arango_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"record": {"_key": "r1"}, "ticket": {}}]
        )
        arango_result = await arango_provider.get_record_by_issue_key("conn-1", "PA-1787")

        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"record": {"_key": "r1"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "r1"})  # type: ignore[method-assign]
        with patch(
            "app.services.graph_db.neo4j.neo4j_provider.Record.from_arango_base_record",
            return_value=MagicMock(id="r1"),
        ):
            neo4j_result = await neo4j_provider.get_record_by_issue_key("conn-1", "PA-1787")

        assert arango_result is not None
        assert neo4j_result is not None

    @pytest.mark.asyncio
    async def test_miss_returns_none_on_both_providers(self, arango_provider, neo4j_provider) -> None:
        arango_provider.http_client.execute_aql = AsyncMock(return_value=[])
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        arango_result = await arango_provider.get_record_by_issue_key("conn-1", "PA-9999")
        neo4j_result = await neo4j_provider.get_record_by_issue_key("conn-1", "PA-9999")

        assert arango_result is None
        assert neo4j_result is None


# ---------------------------------------------------------------------------
# get_record_by_external_id — hit / miss
# ---------------------------------------------------------------------------


class TestGetRecordByExternalIdContract:
    @pytest.mark.asyncio
    async def test_hit_returns_non_none_on_both_providers(self, arango_provider, neo4j_provider) -> None:
        arango_provider.http_client.execute_aql = AsyncMock(return_value=[{"_key": "r1"}])
        arango_provider._translate_node_from_arango = MagicMock(return_value={"_key": "r1"})  # type: ignore[method-assign]
        with patch("app.services.graph_db.arango.arango_http_provider.Record") as mock_record:
            mock_record.from_arango_base_record.return_value = MagicMock(id="r1")
            arango_result = await arango_provider.get_record_by_external_id("conn-1", "EXT-1")

        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"r": {"_key": "r1"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "r1"})  # type: ignore[method-assign]
        with patch(
            "app.services.graph_db.neo4j.neo4j_provider.Record.from_arango_base_record",
            return_value=MagicMock(id="r1"),
        ):
            neo4j_result = await neo4j_provider.get_record_by_external_id("conn-1", "EXT-1")

        assert arango_result is not None
        assert neo4j_result is not None

    @pytest.mark.asyncio
    async def test_miss_returns_none_on_both_providers(self, arango_provider, neo4j_provider) -> None:
        arango_provider.http_client.execute_aql = AsyncMock(return_value=[])
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        arango_result = await arango_provider.get_record_by_external_id("conn-1", "EXT-404")
        neo4j_result = await neo4j_provider.get_record_by_external_id("conn-1", "EXT-404")

        assert arango_result is None
        assert neo4j_result is None


# ---------------------------------------------------------------------------
# get_knowledge_hub_children — pagination envelope shape (used by navigate())
# ---------------------------------------------------------------------------


class TestGetKnowledgeHubChildrenPaginationContract:
    """Both providers must return the same {"nodes": [...], "total": N} envelope —
    KnowledgeHubService (and therefore navigate_tool.py) builds PaginationInfo
    directly from these two keys."""

    @pytest.mark.asyncio
    async def test_envelope_shape_matches_on_both_providers(self, arango_provider, neo4j_provider) -> None:
        raw_nodes = [{"id": "n1", "name": "Node 1", "nodeType": "recordGroup"}]

        # Arango's AQL `RETURN {nodes: ..., total: ...}` comes back as the row itself.
        arango_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"nodes": raw_nodes, "total": 1}]
        )
        arango_result = await arango_provider.get_knowledge_hub_children(
            "app-1", "app", "org-1", "user-key-1", 0, 20, "name", "ASC",
        )

        # Neo4j's Cypher `RETURN {...} AS result` comes back nested under "result".
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"result": {"nodes": raw_nodes, "total": 1}}]
        )
        neo4j_result = await neo4j_provider.get_knowledge_hub_children(
            "app-1", "app", "org-1", "user-key-1", 0, 20, "name", "ASC",
        )

        assert set(arango_result.keys()) == {"nodes", "total"}
        assert set(neo4j_result.keys()) == {"nodes", "total"}
        assert arango_result == neo4j_result == {"nodes": raw_nodes, "total": 1}

    @pytest.mark.asyncio
    async def test_empty_envelope_on_no_results_on_both_providers(self, arango_provider, neo4j_provider) -> None:
        arango_provider.http_client.execute_aql = AsyncMock(return_value=[])
        arango_result = await arango_provider.get_knowledge_hub_children(
            "app-1", "app", "org-1", "user-key-1", 0, 20, "name", "ASC",
        )

        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        neo4j_result = await neo4j_provider.get_knowledge_hub_children(
            "app-1", "app", "org-1", "user-key-1", 0, 20, "name", "ASC",
        )

        assert arango_result == neo4j_result == {"nodes": [], "total": 0}

    @pytest.mark.asyncio
    async def test_unknown_parent_type_returns_empty_envelope_on_both_providers(
        self, arango_provider, neo4j_provider
    ) -> None:
        arango_result = await arango_provider.get_knowledge_hub_children(
            "n-1", "bogus", "org-1", "user-key-1", 0, 20, "name", "ASC",
        )
        neo4j_result = await neo4j_provider.get_knowledge_hub_children(
            "n-1", "bogus", "org-1", "user-key-1", 0, 20, "name", "ASC",
        )
        assert arango_result == neo4j_result == {"nodes": [], "total": 0}


# ---------------------------------------------------------------------------
# get_knowledge_hub_breadcrumbs — item shape + empty/no-match behavior
# ---------------------------------------------------------------------------


class TestGetKnowledgeHubBreadcrumbsContract:
    """navigate_tool.py renders ``breadcrumb_path`` from ``name`` and joins it with
    " › " — both providers must key breadcrumb items the same way."""

    @pytest.mark.asyncio
    async def test_not_found_returns_empty_list_on_both_providers(self, arango_provider, neo4j_provider) -> None:
        arango_provider.http_client.execute_aql = AsyncMock(return_value=[])
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        arango_result = await arango_provider.get_knowledge_hub_breadcrumbs("missing-node")
        neo4j_result = await neo4j_provider.get_knowledge_hub_breadcrumbs("missing-node")

        assert arango_result == neo4j_result == []

    @pytest.mark.asyncio
    async def test_single_root_node_has_same_item_shape_on_both_providers(
        self, arango_provider, neo4j_provider
    ) -> None:
        # A root app node: no parentId, so the loop appends once then stops.
        arango_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"id": "app-1", "name": "Jira", "nodeType": "app", "subType": "JIRA", "parentId": None}]
        )
        arango_result = await arango_provider.get_knowledge_hub_breadcrumbs("app-1")

        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"result": {"id": "app-1", "name": "Jira", "nodeType": "app", "subType": "JIRA", "parentId": None}}]
        )
        neo4j_result = await neo4j_provider.get_knowledge_hub_breadcrumbs("app-1")

        expected = [{"id": "app-1", "name": "Jira", "nodeType": "app", "subType": "JIRA"}]
        assert arango_result == expected
        assert neo4j_result == expected
