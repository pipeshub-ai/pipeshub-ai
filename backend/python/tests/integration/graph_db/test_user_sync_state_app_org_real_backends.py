"""A user's sync state is the relation to the app of that name in the caller's org, on real Neo4j and ArangoDB.

Two orgs can each hold an app with the same name. The other org's app is written first and its key sorts
first, so a pick of "the first app with this name" over every org lands on it and misses this org's relation.
An app written before apps carried ``orgId`` is still this org's through the org's relation to it. A relation
from this org's user to another org's app (written before apps carried an org) is neither read nor set.
The app user of an address is the user that holds the connector's relation, whichever org's user has the address first.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL,
ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.models.entities import AppUser
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

logger = logging.getLogger("user-sync-state-app-org-it")


@pytest.fixture(params=["neo4j", "arango"])
async def graph(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[IGraphDBProvider]:
    try:
        provider = await (
            connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, "user_sync_state_app_org_it")
        )
    except Exception as exc:
        backend_unavailable(request.param, exc)
    yield provider
    disconnect = getattr(provider, "disconnect", None)
    if disconnect is not None:
        await disconnect()


async def test_a_users_sync_state_is_read_and_set_on_the_app_of_that_name_in_the_callers_org(graph: IGraphDBProvider) -> None:
    run = uuid.uuid4().hex[:10]
    now = get_epoch_timestamp_in_ms()
    stamps = {"createdAtTimestamp": now, "updatedAtTimestamp": now}
    other, mine = f"org-a-{run}", f"org-b-{run}"
    email, drive, mail, chat = f"sam-{run}@example.com", f"Drive-{run}", f"Mail-{run}", f"Chat-{run}"
    their_user, my_user = f"a0-user-{run}", f"b-user-{run}"
    their_drive, my_drive, their_mail, my_legacy_mail = f"a0-drive-{run}", f"b-drive-{run}", f"a0-mail-{run}", f"b-mail-{run}"
    their_chat = f"a0-chat-{run}"
    app = {"type": "Drive", "appGroup": "Google Workspace", "scope": "team", "isActive": True, **stamps}
    try:
        assert await graph.batch_upsert_nodes(
            [{"id": org, "accountType": "enterprise", "isActive": True, "name": org, **stamps} for org in (other, mine)],
            collection=CollectionNames.ORGS.value,
        )
        assert await graph.batch_upsert_nodes(
            [{"id": their_drive, "orgId": other, "name": drive, **app},
             {"id": their_mail, "orgId": other, "name": mail, **app},
             {"id": their_chat, "orgId": other, "name": chat, **app},
             {"id": my_drive, "orgId": mine, "name": drive, **app},
             {"id": my_legacy_mail, "name": mail, **app}],          # no orgId: this org's through the org's relation only
            collection=CollectionNames.APPS.value,
        )
        assert await graph.batch_create_edges(
            [{"from_id": org, "from_collection": CollectionNames.ORGS.value, "to_id": a, "to_collection": CollectionNames.APPS.value,
              "createdAtTimestamp": now}
             for org, a in ((other, their_drive), (other, their_mail), (other, their_chat), (mine, my_drive), (mine, my_legacy_mail))],
            collection=CollectionNames.ORG_APP_RELATION.value,
        )
        assert await graph.batch_upsert_nodes(
            [{"id": key, "userId": key, "orgId": org, "email": email, "isActive": True, **stamps}
             for key, org in ((their_user, other), (my_user, mine))],
            collection=CollectionNames.USERS.value,
        )
        assert await graph.batch_create_edges(
            [{"from_id": user, "from_collection": CollectionNames.USERS.value, "to_id": a,
              "to_collection": CollectionNames.APPS.value, "syncState": state, "lastSyncUpdate": now, **stamps}
             for user, a, state in ((their_user, their_drive, "COMPLETED"), (their_user, their_mail, "COMPLETED"),
                                    (my_user, my_drive, "NOT_STARTED"), (my_user, my_legacy_mail, "PAUSED"),
                                    (my_user, their_chat, "COMPLETED"))],                # a relation to the other org's app
            collection=CollectionNames.USER_APP_RELATION.value,
        )

        got = await graph.get_user_sync_state(email, drive, org_id=mine)
        assert got is not None and got["syncState"] == "NOT_STARTED", "this org's relation, not the other org's app"
        assert (await graph.get_user_sync_state(email, mail, org_id=mine) or {}).get("syncState") == "PAUSED", \
            "an app without orgId is this org's through the org's relation"

        updated = await graph.update_user_sync_state(email, "IN_PROGRESS", drive, org_id=mine)
        assert updated is not None and updated["syncState"] == "IN_PROGRESS"
        assert (await graph.get_user_sync_state(email, drive, org_id=mine))["syncState"] == "IN_PROGRESS"
        assert (await graph.get_user_sync_state(email, drive, org_id=other))["syncState"] == "COMPLETED", "the other org's relation is untouched"

        assert await graph.get_user_sync_state(email, chat, org_id=mine) is None, "the other org's app is not this org's"
        assert await graph.update_user_sync_state(email, "PAUSED", chat, org_id=mine) is None
        edge = await graph.get_edge(my_user, CollectionNames.USERS.value, their_chat, CollectionNames.APPS.value,
                                    CollectionNames.USER_APP_RELATION.value)
        assert edge is not None and edge["syncState"] == "COMPLETED", "the relation to the other org's app is not set"
    finally:
        with contextlib.suppress(Exception):
            await graph.delete_nodes_and_edges([their_user, my_user], CollectionNames.USERS.value)
            await graph.delete_nodes_and_edges([their_drive, my_drive, their_mail, their_chat, my_legacy_mail], CollectionNames.APPS.value)
            await graph.delete_nodes_and_edges([other, mine], CollectionNames.ORGS.value)


@pytest.mark.parametrize("graph", ["neo4j"], indirect=True)      # ArangoDB does not create a missing app here; it raises
async def test_an_app_the_user_sync_creates_is_the_orgs_for_its_sync_state(graph: IGraphDBProvider) -> None:
    run = uuid.uuid4().hex[:10]
    org, connector, email = f"org-c-{run}", f"conn-{run}", f"kim-{run}@example.com"
    user = AppUser(app_name=Connectors.JIRA, connector_id=connector, source_user_id=f"acc-{run}", org_id=org, email=email, full_name="Kim")
    try:
        await graph.batch_upsert_app_users([user], org_id=org)
        app = await graph.get_document(connector, CollectionNames.APPS.value)
        assert app is not None and app.get("orgId") == org, "the app is created in the users' org"
        assert await graph.get_user_sync_state(email, connector, org_id=org) is not None, "its sync state is the org's to read"
    finally:
        with contextlib.suppress(Exception):
            stored = await graph.get_user_by_email(email, org_id=org)
            if stored is not None:
                await graph.delete_nodes_and_edges([stored.id], CollectionNames.USERS.value)
            await graph.delete_nodes_and_edges([connector], CollectionNames.APPS.value)


async def test_the_app_user_of_an_address_is_the_one_the_connector_holds(graph: IGraphDBProvider) -> None:
    """Two orgs hold a user with one address; the other org's is written first and its key sorts first. The connector's app user is
    the one with the connector's relation, not the first user with the address."""
    run = uuid.uuid4().hex[:10]
    now = get_epoch_timestamp_in_ms()
    stamps = {"createdAtTimestamp": now, "updatedAtTimestamp": now}
    other, mine, email, connector = f"org-a-{run}", f"org-b-{run}", f"lee-{run}@example.com", f"conn-{run}"
    their_user, my_user = f"a0-user-{run}", f"b-user-{run}"
    try:
        assert await graph.batch_upsert_nodes(
            [{"id": connector, "orgId": mine, "name": connector, "type": "Drive", "appGroup": "Google Workspace", "scope": "team",
              "isActive": True, **stamps}], collection=CollectionNames.APPS.value)
        assert await graph.batch_upsert_nodes(
            [{"id": key, "userId": key, "orgId": org, "email": email, "fullName": "Lee", "isActive": True, **stamps}
             for key, org in ((their_user, other), (my_user, mine))], collection=CollectionNames.USERS.value)
        assert await graph.batch_create_edges(
            [{"from_id": my_user, "from_collection": CollectionNames.USERS.value, "to_id": connector,
              "to_collection": CollectionNames.APPS.value, "sourceUserId": f"src-{run}", "syncState": "NOT_STARTED", "lastSyncUpdate": now,
              **stamps}], collection=CollectionNames.USER_APP_RELATION.value)

        found = await graph.get_app_user_by_email(email, connector)
        assert found is not None and found.source_user_id == f"src-{run}" and found.org_id == mine
    finally:
        with contextlib.suppress(Exception):
            await graph.delete_nodes_and_edges([their_user, my_user], CollectionNames.USERS.value)
            await graph.delete_nodes_and_edges([connector], CollectionNames.APPS.value)
