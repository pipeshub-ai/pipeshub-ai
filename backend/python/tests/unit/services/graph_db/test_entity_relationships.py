"""Unit tests for ``IGraphDBProvider.get_entity_relationships`` on both the
ArangoDB and Neo4j providers — the 1-level graph-neighborhood summary used to
enrich ``search_entities``/``resolve_entity_filters`` results (see
ops/entity_discovery.py).
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


@pytest.fixture
def arango_provider() -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
    p.execute_query = AsyncMock(return_value=[0])
    p.get_related_nodes = AsyncMock(return_value=[])
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


class TestArangoGetEntityRelationships:
    @pytest.mark.asyncio
    async def test_missing_org_id_returns_empty_shape(self, arango_provider):
        result = await arango_provider.get_entity_relationships(
            org_id="", entity_id="d1", entity_type="department",
        )
        assert result == {
            "parentEntity": None, "childEntities": [], "relationshipTypes": [],
            "connectedRecordCount": 0, "connectedEntities": [],
            "connectedRecords": [],
        }
        arango_provider.execute_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_unsupported_entity_type_returns_empty_shape(self, arango_provider):
        result = await arango_provider.get_entity_relationships(
            org_id="o1", entity_id="x1", entity_type="record_group",
        )
        assert result["connectedRecordCount"] == 0
        arango_provider.execute_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_department_has_no_hierarchy_only_record_count(self, arango_provider):
        arango_provider.execute_query = AsyncMock(
            side_effect=[[7], [], [{"recordId": "r1", "name": "Budget", "recordType": "FILE"}]],
        )

        result = await arango_provider.get_entity_relationships(
            org_id="o1", entity_id="d1", entity_type="department",
        )

        assert result["parentEntity"] is None
        assert result["childEntities"] == []
        assert result["connectedRecordCount"] == 7
        assert result["connectedEntities"] == []
        assert result["connectedRecords"] == [{"recordId": "r1", "name": "Budget", "recordType": "FILE"}]
        first_query = arango_provider.execute_query.call_args_list[0][0][0]
        assert "belongsToDepartment" in first_query

    @pytest.mark.asyncio
    async def test_category_returns_children_from_subcategories1(self, arango_provider):
        arango_provider.get_related_nodes = AsyncMock(
            return_value=[{"_key": "s1", "name": "Contracts"}, {"_key": "s2", "name": "NDAs"}],
        )
        arango_provider.execute_query = AsyncMock(
            side_effect=[[5], [], []],
        )

        result = await arango_provider.get_entity_relationships(
            org_id="o1", entity_id="c1", entity_type="category", limit=10,
        )

        assert result["parentEntity"] is None
        assert result["childEntities"] == [
            {"entityId": "s1", "entityType": "subcategory", "name": "Contracts"},
            {"entityId": "s2", "entityType": "subcategory", "name": "NDAs"},
        ]
        call = arango_provider.get_related_nodes.call_args
        assert call[0][0] == "categories/c1"
        assert call[1]["direction"] == "inbound"

    @pytest.mark.asyncio
    async def test_subcategory_returns_parent_category_and_children(self, arango_provider):
        arango_provider.get_related_nodes = AsyncMock(
            side_effect=[
                [{"_key": "c1", "name": "Legal"}],  # parent lookup (outbound)
                [{"_key": "s2a", "name": "Sub-sub"}],  # children lookup (inbound)
            ],
        )
        arango_provider.execute_query = AsyncMock(
            side_effect=[[2], [], []],
        )

        result = await arango_provider.get_entity_relationships(
            org_id="o1", entity_id="s1", entity_type="subcategory",
        )

        assert result["parentEntity"] == {"entityId": "c1", "entityType": "category", "name": "Legal"}
        assert result["childEntities"] == [
            {"entityId": "s2a", "entityType": "subcategory", "name": "Sub-sub"},
        ]

    @pytest.mark.asyncio
    async def test_child_entities_capped_at_limit(self, arango_provider):
        arango_provider.get_related_nodes = AsyncMock(
            return_value=[{"_key": f"s{i}", "name": f"Sub {i}"} for i in range(20)],
        )
        arango_provider.execute_query = AsyncMock(
            side_effect=[[0], [], []],
        )

        result = await arango_provider.get_entity_relationships(
            org_id="o1", entity_id="c1", entity_type="category", limit=3,
        )

        assert len(result["childEntities"]) == 3

    @pytest.mark.asyncio
    async def test_person_returns_relationship_types_and_count(self, arango_provider):
        arango_provider.execute_query = AsyncMock(
            return_value=[
                {"edgeType": "ASSIGNED_TO", "count": 3},
                {"edgeType": "CREATED_BY", "count": 5},
            ],
        )

        result = await arango_provider.get_entity_relationships(
            org_id="o1", entity_id="u1", entity_type="person",
        )

        assert result["parentEntity"] is None
        assert result["childEntities"] == []
        assert result["relationshipTypes"] == ["ASSIGNED_TO", "CREATED_BY"]
        assert result["connectedRecordCount"] == 8
        query = arango_provider.execute_query.call_args[0][0]
        assert "entityRelations" in query

    @pytest.mark.asyncio
    async def test_record_count_query_failure_defaults_to_zero(self, arango_provider):
        arango_provider.execute_query = AsyncMock(
            side_effect=[RuntimeError("boom"), [], []],
        )

        result = await arango_provider.get_entity_relationships(
            org_id="o1", entity_id="t1", entity_type="topic",
        )

        assert result["connectedRecordCount"] == 0

    @pytest.mark.asyncio
    async def test_hierarchy_lookup_failure_does_not_raise(self, arango_provider):
        arango_provider.execute_query = AsyncMock(
            side_effect=[[2], [], []],
        )
        arango_provider.get_related_nodes = AsyncMock(side_effect=RuntimeError("boom"))

        result = await arango_provider.get_entity_relationships(
            org_id="o1", entity_id="c1", entity_type="category",
        )

        assert result["connectedRecordCount"] == 2
        assert result["childEntities"] == []


# ---------------------------------------------------------------------------
# Neo4jProvider
# ---------------------------------------------------------------------------


class TestNeo4jGetEntityRelationships:
    @pytest.mark.asyncio
    async def test_missing_org_id_returns_empty_shape(self, neo4j_provider):
        result = await neo4j_provider.get_entity_relationships(
            org_id="", entity_id="d1", entity_type="department",
        )
        assert result["connectedRecordCount"] == 0
        neo4j_provider.client.execute_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_client_returns_empty_shape(self, neo4j_provider):
        neo4j_provider.client = None
        result = await neo4j_provider.get_entity_relationships(
            org_id="o1", entity_id="d1", entity_type="department",
        )
        assert result["connectedRecordCount"] == 0

    @pytest.mark.asyncio
    async def test_department_record_count(self, neo4j_provider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[[{"cnt": 4}], [], []],
        )

        result = await neo4j_provider.get_entity_relationships(
            org_id="o1", entity_id="d1", entity_type="department",
        )

        assert result["connectedRecordCount"] == 4
        assert result["connectedEntities"] == []
        assert result["connectedRecords"] == []
        first_query = neo4j_provider.client.execute_query.call_args_list[0][0][0]
        assert "BELONGS_TO_DEPARTMENT" in first_query

    @pytest.mark.asyncio
    async def test_person_relationship_types(self, neo4j_provider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"edgeType": "REPORTED_BY", "cnt": 2}],
        )

        result = await neo4j_provider.get_entity_relationships(
            org_id="o1", entity_id="u1", entity_type="person",
        )

        assert result["relationshipTypes"] == ["REPORTED_BY"]
        assert result["connectedRecordCount"] == 2

    @pytest.mark.asyncio
    async def test_category_children_query_uses_inbound_direction(self, neo4j_provider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"cnt": 1}],  # record count
                [{"entityId": "s1", "name": "Contracts"}],  # children
                [],  # co-occurring entities
                [],  # connected records
            ],
        )

        result = await neo4j_provider.get_entity_relationships(
            org_id="o1", entity_id="c1", entity_type="category",
        )

        assert result["childEntities"] == [
            {"entityId": "s1", "entityType": "subcategory", "name": "Contracts"},
        ]


# ---------------------------------------------------------------------------
# ArangoHTTPProvider.get_entity_pair_relationships
# ---------------------------------------------------------------------------


class TestArangoGetEntityPairRelationships:
    @pytest.mark.asyncio
    async def test_missing_org_id_returns_empty_shape(self, arango_provider):
        result = await arango_provider.get_entity_pair_relationships(
            org_id="", source_entity_id="d1", source_entity_type="department",
            target_entity_id="t1", target_entity_type="topic",
        )
        assert result == {"directEdges": [], "sharedRecordCount": 0, "sharedRecords": []}
        arango_provider.execute_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_unsupported_entity_type_returns_empty_shape(self, arango_provider):
        result = await arango_provider.get_entity_pair_relationships(
            org_id="o1", source_entity_id="x1", source_entity_type="record_group",
            target_entity_id="t1", target_entity_type="topic",
        )
        assert result == {"directEdges": [], "sharedRecordCount": 0, "sharedRecords": []}
        arango_provider.execute_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_category_subcategory_pair_checks_direct_edge_and_shared_records(self, arango_provider):
        arango_provider.execute_query = AsyncMock(
            side_effect=[
                [{"_from": "categories/c1", "_to": "subcategories1/s1"}],  # direct edge
                [{"count": 2, "sample": [{"recordId": "r1", "name": "Doc", "recordType": "FILE"}]}],
            ],
        )

        result = await arango_provider.get_entity_pair_relationships(
            org_id="o1", source_entity_id="c1", source_entity_type="category",
            target_entity_id="s1", target_entity_type="subcategory",
        )

        assert result["directEdges"] == [
            {"edgeType": "CATEGORY_HIERARCHY", "edgeCollection": "interCategoryRelations", "direction": "outbound"},
        ]
        assert result["sharedRecordCount"] == 2
        assert result["sharedRecords"] == [{"recordId": "r1", "name": "Doc", "recordType": "FILE"}]
        assert arango_provider.execute_query.call_count == 2

    @pytest.mark.asyncio
    async def test_non_hierarchy_pair_skips_direct_edge_query(self, arango_provider):
        arango_provider.execute_query = AsyncMock(
            return_value=[{"count": 1, "sample": []}],
        )

        result = await arango_provider.get_entity_pair_relationships(
            org_id="o1", source_entity_id="d1", source_entity_type="department",
            target_entity_id="t1", target_entity_type="topic",
        )

        assert result["directEdges"] == []
        assert result["sharedRecordCount"] == 1
        assert arango_provider.execute_query.call_count == 1

    @pytest.mark.asyncio
    async def test_person_pair_has_no_shared_records(self, arango_provider):
        result = await arango_provider.get_entity_pair_relationships(
            org_id="o1", source_entity_id="u1", source_entity_type="person",
            target_entity_id="u2", target_entity_type="person",
        )

        assert result == {"directEdges": [], "sharedRecordCount": 0, "sharedRecords": []}
        arango_provider.execute_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_shared_records_query_failure_returns_zero(self, arango_provider):
        arango_provider.execute_query = AsyncMock(side_effect=RuntimeError("boom"))

        result = await arango_provider.get_entity_pair_relationships(
            org_id="o1", source_entity_id="d1", source_entity_type="department",
            target_entity_id="t1", target_entity_type="topic",
        )

        assert result["sharedRecordCount"] == 0
        assert result["sharedRecords"] == []


# ---------------------------------------------------------------------------
# Neo4jProvider.get_entity_pair_relationships
# ---------------------------------------------------------------------------


class TestNeo4jGetEntityPairRelationships:
    @pytest.mark.asyncio
    async def test_missing_org_id_returns_empty_shape(self, neo4j_provider):
        result = await neo4j_provider.get_entity_pair_relationships(
            org_id="", source_entity_id="d1", source_entity_type="department",
            target_entity_id="t1", target_entity_type="topic",
        )
        assert result == {"directEdges": [], "sharedRecordCount": 0, "sharedRecords": []}
        neo4j_provider.client.execute_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_client_returns_empty_shape(self, neo4j_provider):
        neo4j_provider.client = None
        result = await neo4j_provider.get_entity_pair_relationships(
            org_id="o1", source_entity_id="d1", source_entity_type="department",
            target_entity_id="t1", target_entity_type="topic",
        )
        assert result == {"directEdges": [], "sharedRecordCount": 0, "sharedRecords": []}

    @pytest.mark.asyncio
    async def test_category_subcategory_pair_checks_direct_edge(self, neo4j_provider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"direction": "outbound"}],  # direct edge query
                [{"recordId": "r1", "name": "Doc", "recordType": "FILE"}],  # shared record sample
                [{"cnt": 1}],  # shared record count
            ],
        )

        result = await neo4j_provider.get_entity_pair_relationships(
            org_id="o1", source_entity_id="c1", source_entity_type="category",
            target_entity_id="s1", target_entity_type="subcategory",
        )

        assert result["directEdges"] == [
            {"edgeType": "CATEGORY_HIERARCHY", "edgeCollection": "interCategoryRelations", "direction": "outbound"},
        ]
        assert result["sharedRecordCount"] == 1
        assert result["sharedRecords"] == [{"recordId": "r1", "name": "Doc", "recordType": "FILE"}]

    @pytest.mark.asyncio
    async def test_non_hierarchy_pair_skips_direct_edge_query(self, neo4j_provider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[[], [{"cnt": 2}]],
        )

        result = await neo4j_provider.get_entity_pair_relationships(
            org_id="o1", source_entity_id="d1", source_entity_type="department",
            target_entity_id="t1", target_entity_type="topic",
        )

        assert result["directEdges"] == []
        assert result["sharedRecordCount"] == 2

    @pytest.mark.asyncio
    async def test_person_pair_has_no_shared_records(self, neo4j_provider):
        result = await neo4j_provider.get_entity_pair_relationships(
            org_id="o1", source_entity_id="u1", source_entity_type="person",
            target_entity_id="u2", target_entity_type="person",
        )

        assert result == {"directEdges": [], "sharedRecordCount": 0, "sharedRecords": []}
        neo4j_provider.client.execute_query.assert_not_called()
