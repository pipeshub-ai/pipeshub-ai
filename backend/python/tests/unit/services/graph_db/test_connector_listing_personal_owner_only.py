"""A personal connector is listed only for the user who created it, on both graph backends.

With no ``scope`` the listing query had no scope condition at all, so every
member and admin saw everyone else's personal connectors, although opening one
answers 404. The rule sits in the conditions both the count and the page query
share, so the totals match the rows.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

CALLER = "user-1"
NEO4J_OWNER_PREDICATE = "(doc.scope = $team_scope OR doc.createdBy = $user_id)"
ARANGO_OWNER_PREDICATE = "FILTER doc.scope == @team_scope OR doc.createdBy == @user_id"

LISTING_CALLS = [
    pytest.param({}, id="no-scope-member"),
    pytest.param({"is_admin": True}, id="no-scope-admin"),
    pytest.param({"is_admin": True, "is_configured": True}, id="configured-admin"),
    pytest.param({"is_configured": True, "is_agent_active": True}, id="agents-active"),
    pytest.param({"scope": "personal"}, id="personal"),
    pytest.param({"scope": "team", "is_admin": True}, id="team-admin"),
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


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", LISTING_CALLS)
async def test_neo4j_count_and_page_both_hide_other_users_personal_connectors(
    neo4j_provider: Neo4jProvider, kwargs: dict
) -> None:
    await neo4j_provider.get_filtered_connector_instances(
        collection="apps", edge_collection="orgAppRelation",
        org_id="org-acme", user_id=CALLER, **kwargs,
    )

    calls = neo4j_provider.client.execute_query.await_args_list
    assert len(calls) == 2
    for call in calls:
        assert NEO4J_OWNER_PREDICATE in call.args[0]
        assert call.kwargs["parameters"]["user_id"] == CALLER
        assert call.kwargs["parameters"]["team_scope"] == "team"


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", LISTING_CALLS)
async def test_arango_count_and_page_both_hide_other_users_personal_connectors(
    arango_provider: ArangoHTTPProvider, kwargs: dict
) -> None:
    await arango_provider.get_filtered_connector_instances(
        collection="apps", edge_collection="orgAppRelation",
        org_id="org-acme", user_id=CALLER, **kwargs,
    )

    calls = arango_provider.execute_query.await_args_list
    assert len(calls) == 2
    for call in calls:
        assert ARANGO_OWNER_PREDICATE in call.args[0]
        assert call.kwargs["bind_vars"]["user_id"] == CALLER
        assert call.kwargs["bind_vars"]["team_scope"] == "team"
