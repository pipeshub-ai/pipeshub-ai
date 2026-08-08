"""Unit tests for ``IGraphDBProvider.get_entities_for_sync`` on both the
ArangoDB and Neo4j providers (KG Clean Rebuild plan, Phase 1).

Every query issued by this method must bind ``org_id`` — these tests assert
that directly rather than trusting the docstring, per Part G / Phase 8:
"test_provider_org_scope.py — every sync type binds org_id".
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def arango_provider() -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
    p.execute_query = AsyncMock(return_value=[])
    return p


@pytest.fixture
def neo4j_provider() -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(return_value=[])
    return p


# ---------------------------------------------------------------------------
# ArangoHTTPProvider
# ---------------------------------------------------------------------------


class TestArangoGetEntitiesForSyncOrgScope:
    @pytest.mark.asyncio
    async def test_no_org_id_returns_empty_without_querying(self, arango_provider):
        result = await arango_provider.get_entities_for_sync(org_id="")

        assert result == []
        arango_provider.execute_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_record_group_query_binds_org_id(self, arango_provider):
        await arango_provider.get_entities_for_sync(org_id="org-1", entity_types=["record_group"])

        arango_provider.execute_query.assert_called_once()
        call = arango_provider.execute_query.call_args
        bind_vars = call[1]["bind_vars"]
        assert bind_vars["org_id"] == "org-1"
        assert "rg.orgId == @org_id" in call[0][0]

    @pytest.mark.asyncio
    async def test_record_group_pagination_bind_vars_forwarded(self, arango_provider):
        await arango_provider.get_entities_for_sync(
            org_id="org-1", entity_types=["record_group"], limit=25, offset=50
        )

        bind_vars = arango_provider.execute_query.call_args[1]["bind_vars"]
        assert bind_vars["limit"] == 25
        assert bind_vars["offset"] == 50

    @pytest.mark.asyncio
    async def test_taxonomy_query_scopes_via_records_org_id(self, arango_provider):
        await arango_provider.get_entities_for_sync(org_id="org-1", entity_types=["category"])

        arango_provider.execute_query.assert_called_once()
        call = arango_provider.execute_query.call_args
        query, bind_vars = call[0][0], call[1]["bind_vars"]
        assert bind_vars["org_id"] == "org-1"
        # Category/subcategory nodes have no orgId of their own — scoping
        # must be derived from this org's own records, not the taxonomy node.
        assert "rec.orgId == @org_id" in query
        assert "belongsToCategory" in query

    @pytest.mark.asyncio
    async def test_category_and_subcategory_share_one_query(self, arango_provider):
        await arango_provider.get_entities_for_sync(
            org_id="org-1", entity_types=["category", "subcategory"]
        )

        # Both types traverse the same belongsToCategory edge — must not
        # issue two redundant AQL round trips for one edge group.
        assert arango_provider.execute_query.call_count == 1

    @pytest.mark.asyncio
    async def test_all_types_dispatches_one_query_per_group(self, arango_provider):
        await arango_provider.get_entities_for_sync(org_id="org-1")

        # record_group + person + category_group + department_group + topic_group + language_group
        assert arango_provider.execute_query.call_count == 6
        for call in arango_provider.execute_query.call_args_list:
            assert call[1]["bind_vars"]["org_id"] == "org-1"

    @pytest.mark.asyncio
    async def test_entity_type_filter_excludes_other_taxonomy_groups(self, arango_provider):
        await arango_provider.get_entities_for_sync(org_id="org-1", entity_types=["topic"])

        arango_provider.execute_query.assert_called_once()
        assert "belongsToTopic" in arango_provider.execute_query.call_args[0][0]

    @pytest.mark.asyncio
    async def test_person_query_binds_org_id_directly(self, arango_provider):
        await arango_provider.get_entities_for_sync(org_id="org-1", entity_types=["person"])

        arango_provider.execute_query.assert_called_once()
        call = arango_provider.execute_query.call_args
        query, bind_vars = call[0][0], call[1]["bind_vars"]
        assert bind_vars["org_id"] == "org-1"
        assert bind_vars["entity_type"] == "person"
        # Users carry orgId directly — no traversal via records needed.
        assert "u.orgId == @org_id" in query

    @pytest.mark.asyncio
    async def test_unsupported_entity_type_is_skipped_not_raised(self, arango_provider):
        result = await arango_provider.get_entities_for_sync(org_id="org-1", entity_types=["connector"])

        assert result == []
        arango_provider.execute_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_query_failure_is_caught_and_returns_empty(self, arango_provider):
        arango_provider.execute_query = AsyncMock(side_effect=RuntimeError("boom"))

        result = await arango_provider.get_entities_for_sync(org_id="org-1", entity_types=["record_group"])

        assert result == []

    @pytest.mark.asyncio
    async def test_taxonomy_rows_mapped_to_correct_entity_type(self, arango_provider):
        arango_provider.execute_query = AsyncMock(
            return_value=[
                {"entityId": "c1", "name": "Legal", "_collection": "categories"},
                {"entityId": "s1", "name": "Contracts", "_collection": "subcategories1"},
            ]
        )

        result = await arango_provider.get_entities_for_sync(
            org_id="org-1", entity_types=["category", "subcategory"]
        )

        by_id = {r["entityId"]: r for r in result}
        assert by_id["c1"]["entityType"] == "category"
        assert by_id["s1"]["entityType"] == "subcategory"


# ---------------------------------------------------------------------------
# Neo4jProvider
# ---------------------------------------------------------------------------


class TestNeo4jGetEntitiesForSyncOrgScope:
    @pytest.mark.asyncio
    async def test_no_org_id_returns_empty_without_querying(self, neo4j_provider):
        result = await neo4j_provider.get_entities_for_sync(org_id="")

        assert result == []
        neo4j_provider.client.execute_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_record_group_query_binds_org_id(self, neo4j_provider):
        await neo4j_provider.get_entities_for_sync(org_id="org-1", entity_types=["record_group"])

        neo4j_provider.client.execute_query.assert_called_once()
        call = neo4j_provider.client.execute_query.call_args
        params = call[1]["parameters"]
        assert params["org_id"] == "org-1"
        assert "orgId: $org_id" in call[0][0]

    @pytest.mark.asyncio
    async def test_taxonomy_query_scopes_via_records_org_id(self, neo4j_provider):
        await neo4j_provider.get_entities_for_sync(org_id="org-1", entity_types=["department"])

        call = neo4j_provider.client.execute_query.call_args
        query, params = call[0][0], call[1]["parameters"]
        assert params["org_id"] == "org-1"
        assert "Record {orgId: $org_id}" in query
        assert "BELONGS_TO_DEPARTMENT" in query

    @pytest.mark.asyncio
    async def test_all_types_dispatches_one_query_per_group(self, neo4j_provider):
        await neo4j_provider.get_entities_for_sync(org_id="org-1")

        assert neo4j_provider.client.execute_query.call_count == 6
        for call in neo4j_provider.client.execute_query.call_args_list:
            assert call[1]["parameters"]["org_id"] == "org-1"

    @pytest.mark.asyncio
    async def test_person_query_binds_org_id_directly(self, neo4j_provider):
        await neo4j_provider.get_entities_for_sync(org_id="org-1", entity_types=["person"])

        neo4j_provider.client.execute_query.assert_called_once()
        call = neo4j_provider.client.execute_query.call_args
        query, params = call[0][0], call[1]["parameters"]
        assert params["org_id"] == "org-1"
        assert "u:User {orgId: $org_id}" in query

    @pytest.mark.asyncio
    async def test_query_failure_is_caught_and_returns_empty(self, neo4j_provider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("boom"))

        result = await neo4j_provider.get_entities_for_sync(org_id="org-1", entity_types=["record_group"])

        assert result == []

    @pytest.mark.asyncio
    async def test_taxonomy_rows_mapped_to_correct_entity_type_via_labels(self, neo4j_provider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[
                {"entityId": "c1", "name": "Legal", "nodeLabels": ["Categories"]},
                {"entityId": "s1", "name": "Contracts", "nodeLabels": ["Subcategories1"]},
            ]
        )

        result = await neo4j_provider.get_entities_for_sync(
            org_id="org-1", entity_types=["category", "subcategory"]
        )

        by_id = {r["entityId"]: r for r in result}
        assert by_id["c1"]["entityType"] == "category"
        assert by_id["s1"]["entityType"] == "subcategory"

    @pytest.mark.asyncio
    async def test_no_client_returns_empty(self, neo4j_provider):
        neo4j_provider.client = None

        result = await neo4j_provider.get_entities_for_sync(org_id="org-1", entity_types=["record_group"])

        assert result == []
