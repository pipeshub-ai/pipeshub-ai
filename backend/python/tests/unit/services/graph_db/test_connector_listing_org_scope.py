"""The connector lists are scoped to the caller's organization on both graph backends.

Both listing queries accepted ``org_id`` and then ignored it, so an admin's team
list held every team connector in the database, other organizations' included.
The org-to-app edge is the boundary, because connector apps created before
August 2026 carry no ``orgId`` property.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

ORG = "org-acme"
NEO4J_ORG_PREDICATE = "EXISTS { MATCH (:Organization {id: $org_id})-[:ORG_APP_RELATION]->(doc) }"
ARANGO_ORG_PREDICATE = "FILTER e._to == doc._id AND e._from == @org_handle"

LISTING_CALLS = [
    pytest.param({}, id="no-scope"),
    pytest.param({"scope": "team", "is_admin": True}, id="team-admin"),
    pytest.param({"scope": "personal"}, id="personal"),
    pytest.param({"scope": "team", "is_admin": False}, id="team-member"),
]


@pytest.fixture
def neo4j_provider() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.client.execute_query = AsyncMock(side_effect=[[{"total": 0}], []])
    provider._get_user_accessible_team_app_ids = AsyncMock(return_value=["app-1"])
    return provider


@pytest.fixture
def arango_provider() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(spec=logging.Logger), AsyncMock())
    provider.http_client = AsyncMock()
    provider.execute_query = AsyncMock(side_effect=[[0], []])
    provider._get_user_accessible_team_app_keys = AsyncMock(return_value=["app-1"])
    return provider


class TestNeo4jConnectorListsAreOrgScoped:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("kwargs", LISTING_CALLS)
    async def test_count_and_page_both_require_the_callers_org_edge(
        self, neo4j_provider: Neo4jProvider, kwargs: dict
    ) -> None:
        await neo4j_provider.get_filtered_connector_instances(
            collection="apps", edge_collection="orgAppRelation",
            org_id=ORG, user_id="user-1", **kwargs,
        )

        calls = neo4j_provider.client.execute_query.await_args_list
        assert len(calls) == 2
        for call in calls:
            assert NEO4J_ORG_PREDICATE in call.args[0]
            assert call.kwargs["parameters"]["org_id"] == ORG

    @pytest.mark.asyncio
    async def test_user_connector_instances_require_the_callers_org_edge(
        self, neo4j_provider: Neo4jProvider
    ) -> None:
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        await neo4j_provider.get_user_connector_instances(
            collection="apps", user_id="user-1", org_id=ORG,
            team_scope="team", personal_scope="personal",
        )

        call = neo4j_provider.client.execute_query.await_args
        assert NEO4J_ORG_PREDICATE.replace("(doc)", "(n)") in call.args[0]
        assert call.kwargs["parameters"]["org_id"] == ORG


class TestArangoConnectorListsAreOrgScoped:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("kwargs", LISTING_CALLS)
    async def test_count_and_page_both_require_the_callers_org_edge(
        self, arango_provider: ArangoHTTPProvider, kwargs: dict
    ) -> None:
        await arango_provider.get_filtered_connector_instances(
            collection="apps", edge_collection="orgAppRelation",
            org_id=ORG, user_id="user-1", **kwargs,
        )

        calls = arango_provider.execute_query.await_args_list
        assert len(calls) == 2
        for call in calls:
            assert ARANGO_ORG_PREDICATE in call.args[0]
            bind_vars = call.kwargs["bind_vars"]
            assert bind_vars["org_handle"] == f"organizations/{ORG}"
            assert bind_vars["@org_edge_collection"] == "orgAppRelation"

    @pytest.mark.asyncio
    async def test_user_connector_instances_require_the_callers_org_edge(
        self, arango_provider: ArangoHTTPProvider
    ) -> None:
        arango_provider.execute_query = AsyncMock(return_value=[])

        await arango_provider.get_user_connector_instances(
            collection="apps", user_id="user-1", org_id=ORG,
            team_scope="team", personal_scope="personal",
        )

        call = arango_provider.execute_query.await_args
        assert ARANGO_ORG_PREDICATE in call.args[0]
        assert call.kwargs["bind_vars"]["org_handle"] == f"organizations/{ORG}"
        assert call.kwargs["bind_vars"]["@org_edge_collection"] == "orgAppRelation"
