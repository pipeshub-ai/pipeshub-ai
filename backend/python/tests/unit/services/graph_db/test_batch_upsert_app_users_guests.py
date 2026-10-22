"""A synced guest is saved as a user but is not made an org member, on both graph providers."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.models.entities import AppUser, User
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

ORG = "org-1"
CONNECTOR = "sharepoint-1"


def _neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    provider.get_all_orgs = AsyncMock(return_value=[{"id": ORG}])
    provider.get_document = AsyncMock(return_value={"id": CONNECTOR})
    return provider


def _arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(spec=logging.Logger), AsyncMock())
    provider.http_client = AsyncMock()
    provider.get_all_orgs = AsyncMock(return_value=[{"_key": ORG}])
    provider.get_document = AsyncMock(return_value={"_id": f"apps/{CONNECTOR}", "_key": CONNECTOR})
    return provider


PROVIDERS = [pytest.param(_neo4j, id="neo4j"), pytest.param(_arango, id="arango")]


def _app_user(email: str, *, is_guest: bool) -> AppUser:
    return AppUser(
        app_name=Connectors.SHAREPOINT_ONLINE, connector_id=CONNECTOR, source_user_id=f"src-{email}",
        email=email, full_name=email.split("@")[0], is_guest=is_guest,
    )


def _stored(email: str, *, is_active: bool) -> User:
    return User(id=f"user-{email}", email=email, is_active=is_active)


def _wire(provider, stored_users: list) -> None:
    provider.get_user_by_email = AsyncMock(side_effect=stored_users)
    provider.batch_upsert_nodes = AsyncMock()
    provider.batch_create_edges = AsyncMock()
    provider.delete_edge = AsyncMock()


def _edge_collections(provider) -> list[str]:
    return [call.kwargs["collection"] for call in provider.batch_create_edges.await_args_list]


@pytest.mark.asyncio
@pytest.mark.parametrize("make_provider", PROVIDERS)
async def test_a_new_guest_is_saved_and_linked_to_the_app_but_not_the_org(make_provider) -> None:
    provider = make_provider()
    guest = _app_user("bob_partner.com#EXT#@contoso.com", is_guest=True)
    _wire(provider, [None, _stored(guest.email, is_active=False)])

    await provider.batch_upsert_app_users([guest])

    provider.batch_upsert_nodes.assert_awaited_once()
    assert _edge_collections(provider) == [CollectionNames.USER_APP_RELATION.value]
    provider.delete_edge.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("make_provider", PROVIDERS)
async def test_a_new_member_still_joins_the_org(make_provider) -> None:
    provider = make_provider()
    member = _app_user("ana@contoso.com", is_guest=False)
    _wire(provider, [None, _stored(member.email, is_active=False)])

    await provider.batch_upsert_app_users([member])

    assert _edge_collections(provider) == [
        CollectionNames.BELONGS_TO.value, CollectionNames.USER_APP_RELATION.value,
    ]
    org_edge = provider.batch_create_edges.await_args_list[0].args[0][0]
    assert org_edge["entityType"] == "ORGANIZATION"


@pytest.mark.asyncio
@pytest.mark.parametrize("make_provider", PROVIDERS)
async def test_an_existing_guest_who_never_joined_pipeshub_loses_the_org_edge(make_provider) -> None:
    provider = make_provider()
    guest = _app_user("bob_partner.com#EXT#@contoso.com", is_guest=True)
    stored = _stored(guest.email, is_active=False)
    _wire(provider, [stored])

    await provider.batch_upsert_app_users([guest])

    provider.delete_edge.assert_awaited_once_with(
        stored.id, CollectionNames.USERS.value, ORG, CollectionNames.ORGS.value,
        CollectionNames.BELONGS_TO.value, transaction=None,
    )
    assert _edge_collections(provider) == [CollectionNames.USER_APP_RELATION.value]


@pytest.mark.asyncio
@pytest.mark.parametrize("make_provider", PROVIDERS)
async def test_an_invited_guest_keeps_the_org_edge(make_provider) -> None:
    provider = make_provider()
    guest = _app_user("bob_partner.com#EXT#@contoso.com", is_guest=True)
    _wire(provider, [_stored(guest.email, is_active=True)])

    await provider.batch_upsert_app_users([guest])

    provider.delete_edge.assert_not_awaited()
    provider.logger.info.assert_any_call(
        f"Guest {guest.email} is active in PipesHub; keeping their organization membership"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("make_provider", PROVIDERS)
async def test_an_existing_member_is_left_as_before(make_provider) -> None:
    provider = make_provider()
    member = _app_user("ana@contoso.com", is_guest=False)
    _wire(provider, [_stored(member.email, is_active=False)])

    await provider.batch_upsert_app_users([member])

    provider.delete_edge.assert_not_awaited()
    assert _edge_collections(provider) == [CollectionNames.USER_APP_RELATION.value]


def test_app_users_are_members_unless_marked_guests() -> None:
    assert AppUser(
        app_name=Connectors.SHAREPOINT_ONLINE, connector_id=CONNECTOR, source_user_id="s", email="a@b.c", full_name="A",
    ).is_guest is False
