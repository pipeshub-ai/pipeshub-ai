"""Unit tests for the bi-temporal canonical-edge + cross-app hard-key
methods added to ``IGraphDBProvider`` in KG Clean Rebuild Phase 6:
``upsert_bitemporal_edge``, ``invalidate_bitemporal_edges``,
``get_bitemporal_edges``, ``find_nodes_by_hard_key``.

Exercised against both concrete providers with mocked query execution —
these tests assert query/parameter shape (org scoping, bi-temporal filter
predicates, no-op vs. supersede decisions), not real DB behavior.
"""
from __future__ import annotations

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
# ArangoHTTPProvider — upsert_bitemporal_edge
# ---------------------------------------------------------------------------


class TestArangoUpsertBitemporalEdge:
    @pytest.mark.asyncio
    async def test_no_existing_edge_inserts_new(self, arango_provider) -> None:
        arango_provider.execute_query = AsyncMock(side_effect=[[], [{"_key": "e1"}]])

        result = await arango_provider.upsert_bitemporal_edge(
            org_id="org-1", from_id="p1", from_collection="people",
            to_id="p2", to_collection="people", edge_type="SAME_AS",
            attributes={"confidence": 0.9},
        )

        assert result == {"_key": "e1"}
        assert arango_provider.execute_query.call_count == 2
        insert_call = arango_provider.execute_query.call_args_list[1]
        new_edge = insert_call[1]["bind_vars"]["new_edge"]
        assert new_edge["orgId"] == "org-1"
        assert new_edge["edgeType"] == "SAME_AS"
        assert new_edge["invalidAtTimestamp"] is None
        assert new_edge["expiredAtTimestamp"] is None

    @pytest.mark.asyncio
    async def test_existing_edge_same_attributes_is_noop(self, arango_provider) -> None:
        existing = {"_key": "e1", "attributes": {"confidence": 0.9}}
        arango_provider.execute_query = AsyncMock(return_value=[existing])

        result = await arango_provider.upsert_bitemporal_edge(
            org_id="org-1", from_id="p1", from_collection="people",
            to_id="p2", to_collection="people", edge_type="SAME_AS",
            attributes={"confidence": 0.9},
        )

        assert result == existing
        # Only the lookup ran — no invalidate, no insert.
        assert arango_provider.execute_query.call_count == 1

    @pytest.mark.asyncio
    async def test_existing_edge_different_attributes_invalidates_then_inserts(self, arango_provider) -> None:
        existing = {"_key": "e1", "attributes": {"confidence": 0.5}}
        arango_provider.execute_query = AsyncMock(
            side_effect=[[existing], None, [{"_key": "e2"}]]
        )

        result = await arango_provider.upsert_bitemporal_edge(
            org_id="org-1", from_id="p1", from_collection="people",
            to_id="p2", to_collection="people", edge_type="SAME_AS",
            attributes={"confidence": 0.95},
        )

        assert result == {"_key": "e2"}
        assert arango_provider.execute_query.call_count == 3
        invalidate_call = arango_provider.execute_query.call_args_list[1]
        assert invalidate_call[1]["bind_vars"]["key"] == "e1"

    @pytest.mark.asyncio
    async def test_lookup_query_scopes_by_org_id(self, arango_provider) -> None:
        arango_provider.execute_query = AsyncMock(side_effect=[[], [{}]])

        await arango_provider.upsert_bitemporal_edge(
            org_id="org-1", from_id="p1", from_collection="people",
            to_id="p2", to_collection="people", edge_type="SAME_AS",
        )

        lookup_call = arango_provider.execute_query.call_args_list[0]
        assert lookup_call[1]["bind_vars"]["org_id"] == "org-1"
        assert "e.orgId == @org_id" in lookup_call[0][0]


class TestArangoInvalidateBitemporalEdges:
    @pytest.mark.asyncio
    async def test_refuses_with_no_from_or_to_id(self, arango_provider) -> None:
        result = await arango_provider.invalidate_bitemporal_edges(org_id="org-1")

        assert result == 0
        arango_provider.execute_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalidates_with_from_id_filter_binds_org_id(self, arango_provider) -> None:
        arango_provider.execute_query = AsyncMock(return_value=[{"_key": "e1"}, {"_key": "e2"}])

        result = await arango_provider.invalidate_bitemporal_edges(
            org_id="org-1", from_id="p1", from_collection="people",
        )

        assert result == 2
        bind_vars = arango_provider.execute_query.call_args[1]["bind_vars"]
        assert bind_vars["org_id"] == "org-1"
        assert bind_vars["from_ref"] == "people/p1"

    @pytest.mark.asyncio
    async def test_query_failure_returns_zero(self, arango_provider) -> None:
        arango_provider.execute_query = AsyncMock(side_effect=RuntimeError("boom"))

        result = await arango_provider.invalidate_bitemporal_edges(
            org_id="org-1", from_id="p1", from_collection="people",
        )

        assert result == 0


class TestArangoGetBitemporalEdges:
    @pytest.mark.asyncio
    async def test_default_filters_to_current_edges(self, arango_provider) -> None:
        await arango_provider.get_bitemporal_edges(org_id="org-1")

        query = arango_provider.execute_query.call_args[0][0]
        assert "e.invalidAtTimestamp == null" in query
        assert "e.expiredAtTimestamp == null" in query
        assert "e.validAtTimestamp <= @as_of" not in query

    @pytest.mark.asyncio
    async def test_as_of_adds_temporal_predicates(self, arango_provider) -> None:
        await arango_provider.get_bitemporal_edges(org_id="org-1", as_of=1000)

        call = arango_provider.execute_query.call_args
        query, bind_vars = call[0][0], call[1]["bind_vars"]
        assert bind_vars["as_of"] == 1000
        assert "e.validAtTimestamp <= @as_of" in query
        assert "e.invalidAtTimestamp == null OR e.invalidAtTimestamp > @as_of" in query

    @pytest.mark.asyncio
    async def test_include_history_skips_current_only_filter(self, arango_provider) -> None:
        await arango_provider.get_bitemporal_edges(org_id="org-1", include_history=True)

        query = arango_provider.execute_query.call_args[0][0]
        assert "e.invalidAtTimestamp == null" not in query

    @pytest.mark.asyncio
    async def test_as_of_takes_precedence_over_include_history(self, arango_provider) -> None:
        await arango_provider.get_bitemporal_edges(org_id="org-1", as_of=500, include_history=True)

        query = arango_provider.execute_query.call_args[0][0]
        assert "e.validAtTimestamp <= @as_of" in query

    @pytest.mark.asyncio
    async def test_query_failure_returns_empty_list(self, arango_provider) -> None:
        arango_provider.execute_query = AsyncMock(side_effect=RuntimeError("boom"))

        result = await arango_provider.get_bitemporal_edges(org_id="org-1")

        assert result == []


class TestArangoFindNodesByHardKey:
    @pytest.mark.asyncio
    async def test_empty_inputs_short_circuit(self, arango_provider) -> None:
        assert await arango_provider.find_nodes_by_hard_key("org-1", [], "email", "a@b.com") == []
        assert await arango_provider.find_nodes_by_hard_key("org-1", ["users"], "", "a@b.com") == []
        assert await arango_provider.find_nodes_by_hard_key("org-1", ["users"], "email", "") == []
        arango_provider.execute_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_queries_each_collection_and_tags_source(self, arango_provider) -> None:
        # AQL's `MERGE(n, {_collection: @collection})` tags the source
        # collection server-side — the mock stands in for that return shape.
        arango_provider.execute_query = AsyncMock(
            side_effect=[
                [{"_key": "u1", "email": "a@b.com", "_collection": "users"}],
                [{"_key": "c1", "email": "a@b.com", "_collection": "contacts"}],
            ]
        )

        result = await arango_provider.find_nodes_by_hard_key(
            "org-1", ["users", "contacts"], "email", "a@b.com",
        )

        assert len(result) == 2
        collections = {r["_collection"] for r in result}
        assert collections == {"users", "contacts"}

    @pytest.mark.asyncio
    async def test_respects_overall_limit_across_collections(self, arango_provider) -> None:
        arango_provider.execute_query = AsyncMock(
            side_effect=[
                [{"_key": "u1"}, {"_key": "u2"}],
                [{"_key": "c1"}],
            ]
        )

        result = await arango_provider.find_nodes_by_hard_key(
            "org-1", ["users", "contacts"], "email", "a@b.com", limit=2,
        )

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_one_collection_failure_does_not_abort_others(self, arango_provider) -> None:
        arango_provider.execute_query = AsyncMock(
            side_effect=[RuntimeError("boom"), [{"_key": "c1", "_collection": "contacts"}]]
        )

        result = await arango_provider.find_nodes_by_hard_key(
            "org-1", ["users", "contacts"], "email", "a@b.com",
        )

        assert len(result) == 1
        assert result[0]["_collection"] == "contacts"

    @pytest.mark.asyncio
    async def test_binds_org_id_and_value(self, arango_provider) -> None:
        await arango_provider.find_nodes_by_hard_key("org-1", ["users"], "email", "a@b.com")

        bind_vars = arango_provider.execute_query.call_args[1]["bind_vars"]
        assert bind_vars["org_id"] == "org-1"
        assert bind_vars["value"] == "a@b.com"


# ---------------------------------------------------------------------------
# Neo4jProvider
# ---------------------------------------------------------------------------


class TestNeo4jUpsertBitemporalEdge:
    @pytest.mark.asyncio
    async def test_no_client_returns_empty_dict(self, neo4j_provider) -> None:
        neo4j_provider.client = None

        result = await neo4j_provider.upsert_bitemporal_edge(
            org_id="org-1", from_id="p1", from_collection="people",
            to_id="p2", to_collection="people", edge_type="SAME_AS",
        )

        assert result == {}

    @pytest.mark.asyncio
    async def test_no_existing_edge_inserts_new(self, neo4j_provider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[[], [{"r": {"edgeType": "SAME_AS"}}]]
        )

        result = await neo4j_provider.upsert_bitemporal_edge(
            org_id="org-1", from_id="p1", from_collection="people",
            to_id="p2", to_collection="people", edge_type="SAME_AS",
            attributes={"confidence": 0.9},
        )

        assert result == {"edgeType": "SAME_AS"}
        assert neo4j_provider.client.execute_query.call_count == 2

    @pytest.mark.asyncio
    async def test_existing_edge_same_attributes_is_noop(self, neo4j_provider) -> None:
        existing_props = {"attributes": {"confidence": 0.9}}
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"r": existing_props}])

        result = await neo4j_provider.upsert_bitemporal_edge(
            org_id="org-1", from_id="p1", from_collection="people",
            to_id="p2", to_collection="people", edge_type="SAME_AS",
            attributes={"confidence": 0.9},
        )

        assert result == existing_props
        assert neo4j_provider.client.execute_query.call_count == 1

    @pytest.mark.asyncio
    async def test_existing_edge_different_attributes_invalidates_then_inserts(self, neo4j_provider) -> None:
        existing_props = {"attributes": {"confidence": 0.5}}
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[[{"r": existing_props}], None, [{"r": {"attributes": {"confidence": 0.95}}}]]
        )

        result = await neo4j_provider.upsert_bitemporal_edge(
            org_id="org-1", from_id="p1", from_collection="people",
            to_id="p2", to_collection="people", edge_type="SAME_AS",
            attributes={"confidence": 0.95},
        )

        assert result == {"attributes": {"confidence": 0.95}}
        assert neo4j_provider.client.execute_query.call_count == 3


class TestNeo4jInvalidateBitemporalEdges:
    @pytest.mark.asyncio
    async def test_refuses_with_no_from_or_to_id(self, neo4j_provider) -> None:
        result = await neo4j_provider.invalidate_bitemporal_edges(org_id="org-1")

        assert result == 0
        neo4j_provider.client.execute_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalidates_with_from_id_binds_org_id(self, neo4j_provider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"updated": 3}])

        result = await neo4j_provider.invalidate_bitemporal_edges(
            org_id="org-1", from_id="p1", from_collection="people",
        )

        assert result == 3
        params = neo4j_provider.client.execute_query.call_args[1]["parameters"]
        assert params["org_id"] == "org-1"
        assert params["from_id"] == "p1"

    @pytest.mark.asyncio
    async def test_query_failure_returns_zero(self, neo4j_provider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("boom"))

        result = await neo4j_provider.invalidate_bitemporal_edges(
            org_id="org-1", from_id="p1", from_collection="people",
        )

        assert result == 0


class TestNeo4jGetBitemporalEdges:
    @pytest.mark.asyncio
    async def test_default_filters_to_current_edges(self, neo4j_provider) -> None:
        await neo4j_provider.get_bitemporal_edges(org_id="org-1")

        query = neo4j_provider.client.execute_query.call_args[0][0]
        assert "r.invalidAtTimestamp IS NULL" in query
        assert "r.expiredAtTimestamp IS NULL" in query

    @pytest.mark.asyncio
    async def test_as_of_adds_temporal_predicates(self, neo4j_provider) -> None:
        await neo4j_provider.get_bitemporal_edges(org_id="org-1", as_of=1000)

        call = neo4j_provider.client.execute_query.call_args
        query, params = call[0][0], call[1]["parameters"]
        assert params["as_of"] == 1000
        assert "r.validAtTimestamp <= $as_of" in query

    @pytest.mark.asyncio
    async def test_returns_flattened_edge_properties(self, neo4j_provider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"r": {"edgeType": "SAME_AS"}}, {"r": "not-a-dict"}]
        )

        result = await neo4j_provider.get_bitemporal_edges(org_id="org-1")

        assert result == [{"edgeType": "SAME_AS"}]

    @pytest.mark.asyncio
    async def test_no_client_returns_empty(self, neo4j_provider) -> None:
        neo4j_provider.client = None

        result = await neo4j_provider.get_bitemporal_edges(org_id="org-1")

        assert result == []


class TestNeo4jFindNodesByHardKey:
    @pytest.mark.asyncio
    async def test_empty_inputs_short_circuit(self, neo4j_provider) -> None:
        assert await neo4j_provider.find_nodes_by_hard_key("org-1", [], "email", "a@b.com") == []
        neo4j_provider.client.execute_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_unsafe_field_name(self, neo4j_provider) -> None:
        result = await neo4j_provider.find_nodes_by_hard_key(
            "org-1", ["users"], "email }) DETACH DELETE n //", "a@b.com",
        )

        assert result == []
        neo4j_provider.client.execute_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_queries_each_collection_and_tags_source(self, neo4j_provider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"n": {"id": "u1", "email": "a@b.com"}}],
                [{"n": {"id": "c1", "email": "a@b.com"}}],
            ]
        )

        result = await neo4j_provider.find_nodes_by_hard_key(
            "org-1", ["users", "contacts"], "email", "a@b.com",
        )

        assert len(result) == 2
        collections = {r["_collection"] for r in result}
        assert collections == {"users", "contacts"}

    @pytest.mark.asyncio
    async def test_no_client_returns_empty(self, neo4j_provider) -> None:
        neo4j_provider.client = None

        result = await neo4j_provider.find_nodes_by_hard_key("org-1", ["users"], "email", "a@b.com")

        assert result == []
