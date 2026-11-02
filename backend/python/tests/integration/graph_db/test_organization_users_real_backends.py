"""The organisation's user list has the same shape on Neo4j and ArangoDB.

``GET /api/v1/users/graph/list`` documents ``id``, ``_key`` and ``_id`` on every user. Neo4j filled them in
and ArangoDB left ``_key`` and ``_id`` out, so a client got a different object depending on the store behind it.

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

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "organization_users_it"
logger = logging.getLogger("organization-users-it")


@pytest.fixture(params=["neo4j", "arango"])
async def org(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[object, str, list[str]]]:
    try:
        graph = await (
            connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
            else connect_arango(logger, ARANGO_DB)
        )
    except Exception as exc:
        backend_unavailable(request.param, exc)
    run = uuid.uuid4().hex[:10]
    org_id = f"org-ou-{run}"
    keys = [f"user-{i}-{run}" for i in range(2)]
    now = get_epoch_timestamp_in_ms()
    try:
        assert await graph.batch_upsert_nodes(
            [{"id": org_id, "accountType": "enterprise", "name": "Acme", "isActive": True,
              "createdAtTimestamp": now, "updatedAtTimestamp": now}],
            collection=CollectionNames.ORGS.value,
        )
        for i, key in enumerate(keys):
            assert await graph.batch_upsert_nodes(
                [{"id": key, "userId": f"mongo-{key}", "orgId": org_id, "email": f"{key}@example.test",
                  "fullName": f"User {i}", "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now}],
                collection=CollectionNames.USERS.value,
            )
            assert await graph.batch_create_edges(
                [{"from_id": key, "from_collection": CollectionNames.USERS.value, "to_id": org_id,
                  "to_collection": CollectionNames.ORGS.value, "entityType": "ORGANIZATION",
                  "createdAtTimestamp": now, "updatedAtTimestamp": now}],
                collection=CollectionNames.BELONGS_TO.value,
            )
        yield graph, org_id, keys
    finally:
        with contextlib.suppress(Exception):
            await graph.delete_nodes_and_edges(keys, CollectionNames.USERS.value)
            await graph.delete_nodes_and_edges([org_id], CollectionNames.ORGS.value)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            with contextlib.suppress(Exception):
                await disconnect()


async def test_each_user_carries_id_key_and_id_of_the_node(org: tuple[object, str, list[str]]) -> None:
    graph, org_id, keys = org

    users, total = await graph.get_organization_users(org_id)

    assert total == 2 and sorted(u["id"] for u in users) == sorted(keys)
    for user in users:
        assert user["_key"] == user["id"]
        assert user["_id"] == f"{CollectionNames.USERS.value}/{user['id']}"
