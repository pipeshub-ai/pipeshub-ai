"""An existing member of the org's "All" team must keep their edge (and role)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.config.constants.neo4j import collection_to_label
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

ORG = "org-1"
TEAM = f"all_{ORG}"
USER_LABEL = collection_to_label(CollectionNames.USERS.value)
TEAM_LABEL = collection_to_label(CollectionNames.TEAMS.value)


def _provider_with_members(members: set[str]) -> Neo4jProvider:
    """A graph where `members` already have a PERMISSION edge to the All team.

    Membership only matches a query that uses the labels the nodes are stored under.
    """
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())

    async def execute_query(query: str, parameters: dict | None = None, **_: object) -> list[dict]:
        if "count(r)" in query:
            return [{"count": len(members)}]
        stored_labels = f"(u:{USER_LABEL} {{" in query and f"(t:{TEAM_LABEL} {{" in query
        if stored_labels and parameters.get("user_key") in members:
            return [{"r": {"role": "OWNER"}}]
        return []

    provider.client = AsyncMock()
    provider.client.execute_query = AsyncMock(side_effect=execute_query)
    provider.get_document = AsyncMock(return_value={"id": TEAM, "orgId": ORG})
    provider.batch_upsert_nodes = AsyncMock()
    provider.batch_create_edges = AsyncMock()
    provider.update_node = AsyncMock()
    provider.get_team_with_users = AsyncMock(
        return_value={"members": [{"id": m} for m in members]}
    )
    return provider


def _edges_created(provider: Neo4jProvider) -> list[dict]:
    return [edge for call in provider.batch_create_edges.await_args_list for edge in call.args[0]]


@pytest.mark.asyncio
async def test_add_user_to_all_team_leaves_an_existing_member_alone() -> None:
    provider = _provider_with_members({"owner"})

    await provider.add_user_to_all_team(ORG, "owner")

    assert _edges_created(provider) == []
    provider.update_node.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_user_to_all_team_adds_a_new_member_as_reader() -> None:
    provider = _provider_with_members({"owner"})

    await provider.add_user_to_all_team(ORG, "newcomer")

    edges = _edges_created(provider)
    assert [(e["from_id"], e["role"]) for e in edges] == [("newcomer", "READER")]


@pytest.mark.asyncio
async def test_ensure_all_team_only_adds_users_without_an_edge() -> None:
    provider = _provider_with_members({"owner"})
    provider.get_users = AsyncMock(return_value=[
        {"id": "owner", "createdAtTimestamp": 1},
        {"id": "newcomer", "createdAtTimestamp": 2},
    ])

    await provider.ensure_all_team_with_users(ORG)

    edges = _edges_created(provider)
    assert [(e["from_id"], e["role"]) for e in edges] == [("newcomer", "READER")]
