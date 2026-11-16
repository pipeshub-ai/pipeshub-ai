"""A user's sync state is the relation to the app of that name in the caller's org, on real Neo4j and ArangoDB.

Two orgs can each hold an app with the same name. The other org's app is written first and its key sorts
first, so a pick of "the first app with this name" over every org lands on it and misses this org's relation.
An app written before apps carried ``orgId`` is still this org's through the org's relation to it.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL,
ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
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
    email, drive, mail = f"sam-{run}@example.com", f"Drive-{run}", f"Mail-{run}"
    their_user, my_user = f"a0-user-{run}", f"b-user-{run}"
    their_drive, my_drive, their_mail, my_legacy_mail = f"a0-drive-{run}", f"b-drive-{run}", f"a0-mail-{run}", f"b-mail-{run}"
    app = {"type": "Drive", "appGroup": "Google Workspace", "scope": "team", "isActive": True, **stamps}
    try:
        assert await graph.batch_upsert_nodes(
            [{"id": org, "accountType": "enterprise", "isActive": True, "name": org, **stamps} for org in (other, mine)],
            collection=CollectionNames.ORGS.value,
        )
        assert await graph.batch_upsert_nodes(
            [{"id": their_drive, "orgId": other, "name": drive, **app},
             {"id": their_mail, "orgId": other, "name": mail, **app},
             {"id": my_drive, "orgId": mine, "name": drive, **app},
             {"id": my_legacy_mail, "name": mail, **app}],          # no orgId: this org's through the org's relation only
            collection=CollectionNames.APPS.value,
        )
        assert await graph.batch_create_edges(
            [{"from_id": org, "from_collection": CollectionNames.ORGS.value, "to_id": a, "to_collection": CollectionNames.APPS.value,
              "createdAtTimestamp": now}
             for org, a in ((other, their_drive), (other, their_mail), (mine, my_drive), (mine, my_legacy_mail))],
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
                                    (my_user, my_drive, "NOT_STARTED"), (my_user, my_legacy_mail, "PAUSED"))],
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
    finally:
        with contextlib.suppress(Exception):
            await graph.delete_nodes_and_edges([their_user, my_user], CollectionNames.USERS.value)
            await graph.delete_nodes_and_edges([their_drive, my_drive, their_mail, my_legacy_mail], CollectionNames.APPS.value)
            await graph.delete_nodes_and_edges([other, mine], CollectionNames.ORGS.value)
