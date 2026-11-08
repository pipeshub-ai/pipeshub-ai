"""The Nextcloud user-to-app edge migration on real Neo4j and ArangoDB.

Earlier Nextcloud syncs linked whoever had the Nextcloud profile email to the
personal connector's app. The migration keeps only the creator's edge; the two
backends return edges in different shapes, so it runs against both.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.config.constants.arangodb import CollectionNames
from app.migrations.nextcloud_user_app_edges_migration import (
    NextcloudUserAppEdgesMigrationService,
)
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

ARANGO_DB = "nextcloud_user_app_edges_it"

logger = logging.getLogger("nextcloud-user-app-edges-it")


class _Config:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get_config(self, key: str, **_: object) -> Any:  # noqa: ANN401
        return self.values.get(key)

    async def set_config(self, key: str, value: Any) -> None:  # noqa: ANN401
        self.values[key] = value


@pytest.fixture(params=["neo4j", "arango"])
async def graph(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[IGraphDBProvider]:
    try:
        provider = await (
            connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB)
        )
    except Exception as exc:
        backend_unavailable(request.param, exc)
    yield provider
    disconnect = getattr(provider, "disconnect", None)
    if disconnect is not None:
        await disconnect()


async def _app_users(graph: IGraphDBProvider, app_id: str) -> set[str]:
    edges = await graph.get_edges_to_node(
        f"{CollectionNames.APPS.value}/{app_id}", CollectionNames.USER_APP_RELATION.value, raise_on_error=True
    )
    return {str(e.get("from_id") or e.get("_from") or "").rsplit("/", 1)[-1] for e in edges}


async def test_only_the_creators_edge_into_a_nextcloud_app_survives(graph: IGraphDBProvider) -> None:
    run = uuid.uuid4().hex[:10]
    now = get_epoch_timestamp_in_ms()
    stamps = {"createdAtTimestamp": now, "updatedAtTimestamp": now}
    org_id = f"org-nc-{run}"
    alice, bob = f"alice-{run}", f"bob-{run}"
    nextcloud, drive = f"nextcloud-{run}", f"drive-{run}"
    try:
        assert await graph.batch_upsert_nodes(
            [{"id": nextcloud, "orgId": org_id, "name": "Nextcloud", "type": "Nextcloud", "appGroup": "Cloud Storage",
              "scope": "personal", "isActive": True, "createdBy": alice, **stamps},
             {"id": drive, "orgId": org_id, "name": "Drive", "type": "Drive", "appGroup": "Google Workspace",
              "scope": "personal", "isActive": True, "createdBy": alice, **stamps}],
            collection=CollectionNames.APPS.value,
        )
        assert await graph.batch_upsert_nodes(
            [{"id": key, "userId": key, "orgId": org_id, "email": f"{key}@example.com", "isActive": True, **stamps}
             for key in (alice, bob)],
            collection=CollectionNames.USERS.value,
        )
        # Bob's edge into the Nextcloud app is what an old sync wrote for the profile email.
        assert await graph.batch_create_edges(
            [{"from_id": user, "from_collection": CollectionNames.USERS.value, "to_id": app,
              "to_collection": CollectionNames.APPS.value, "syncState": "NOT_STARTED", "lastSyncUpdate": now,
              **stamps}
             for user, app in ((alice, nextcloud), (bob, nextcloud), (bob, drive))],
            collection=CollectionNames.USER_APP_RELATION.value,
        )
        assert await _app_users(graph, nextcloud) == {alice, bob}

        await NextcloudUserAppEdgesMigrationService(graph, _Config(), logger).migrate()

        assert await _app_users(graph, nextcloud) == {alice}
        assert await _app_users(graph, drive) == {bob}, "only Nextcloud apps are touched"
    finally:
        with contextlib.suppress(Exception):
            await graph.delete_nodes_and_edges([nextcloud, drive], CollectionNames.APPS.value)
            await graph.delete_nodes_and_edges([alice, bob], CollectionNames.USERS.value)
