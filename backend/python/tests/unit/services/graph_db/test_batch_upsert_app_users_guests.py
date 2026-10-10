"""A synced guest is an org member whose gate edge marks them external, on both graph providers.

The flag is what keeps the connector's org-wide grants from the guest; the org
edge stays, because other connectors resolve their group members through it
(CONF-01: deleting it dropped a guest from every Confluence group).
"""

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


def _edges(provider) -> list[tuple[str, dict]]:
    return [
        (call.kwargs["collection"], call.args[0][0])
        for call in provider.batch_create_edges.await_args_list
    ]


def _gate(provider) -> dict:
    gates = [e for c, e in _edges(provider) if c == CollectionNames.USER_APP_RELATION.value]
    assert len(gates) == 1
    return gates[0]


def _org_edges(provider) -> list[dict]:
    return [e for c, e in _edges(provider) if c == CollectionNames.BELONGS_TO.value]


GUEST_EMAIL = "bob_partner.com#EXT#@contoso.com"


@pytest.mark.asyncio
@pytest.mark.parametrize("make_provider", PROVIDERS)
async def test_a_new_guest_joins_the_org_and_is_gated_as_external(make_provider) -> None:
    provider = make_provider()
    guest = _app_user(GUEST_EMAIL, is_guest=True)
    _wire(provider, [None, _stored(guest.email, is_active=False)])

    await provider.batch_upsert_app_users([guest])

    provider.batch_upsert_nodes.assert_awaited_once()
    assert [e["entityType"] for e in _org_edges(provider)] == ["ORGANIZATION"]
    assert _gate(provider)["isExternalUser"] is True
    provider.delete_edge.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("make_provider", PROVIDERS)
async def test_a_new_member_joins_the_org_and_is_gated_as_a_member(make_provider) -> None:
    provider = make_provider()
    member = _app_user("ana@contoso.com", is_guest=False)
    _wire(provider, [None, _stored(member.email, is_active=False)])

    await provider.batch_upsert_app_users([member])

    assert [c for c, _ in _edges(provider)] == [
        CollectionNames.BELONGS_TO.value, CollectionNames.USER_APP_RELATION.value,
    ]
    assert _gate(provider)["isExternalUser"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("make_provider", PROVIDERS)
@pytest.mark.parametrize("is_active", [False, True], ids=["inactive", "active"])
async def test_an_existing_guest_keeps_or_regains_the_org_edge(make_provider, is_active) -> None:
    """CONF-01: the sync used to delete an inactive guest's org edge."""
    provider = make_provider()
    guest = _app_user(GUEST_EMAIL, is_guest=True)
    stored = _stored(guest.email, is_active=is_active)
    _wire(provider, [stored])

    await provider.batch_upsert_app_users([guest])

    provider.delete_edge.assert_not_awaited()
    org_edges = _org_edges(provider)
    assert len(org_edges) == 1
    assert stored.id in str(org_edges[0].get("from_id") or org_edges[0].get("_from"))
    assert ORG in str(org_edges[0].get("to_id") or org_edges[0].get("_to"))
    assert _gate(provider)["isExternalUser"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("make_provider", PROVIDERS)
async def test_an_existing_member_gets_only_the_gate_edge(make_provider) -> None:
    provider = make_provider()
    member = _app_user("ana@contoso.com", is_guest=False)
    _wire(provider, [_stored(member.email, is_active=False)])

    await provider.batch_upsert_app_users([member])

    provider.delete_edge.assert_not_awaited()
    assert [c for c, _ in _edges(provider)] == [CollectionNames.USER_APP_RELATION.value]
    assert _gate(provider)["isExternalUser"] is False


def test_app_users_are_members_unless_marked_guests() -> None:
    assert AppUser(
        app_name=Connectors.SHAREPOINT_ONLINE, connector_id=CONNECTOR, source_user_id="s", email="a@b.c", full_name="A",
    ).is_guest is False
