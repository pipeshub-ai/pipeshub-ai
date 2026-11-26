"""What a search may be scoped by (`get_accessible_containers`) on Neo4j and ArangoDB, and on PostgreSQL through ``check`` (postgres/test_accessible_containers_pg.py): the Apps the gate lets the user reach, nothing trusted
(the verifier decides every hit), an App whose vector membership was never written declining the container path, and a hidden Collection reached only by
naming it. The same answers on every store.
"""
from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

READY = {"vectorMembershipBackfilled": True, "vectorMembershipBackfillExhausted": False}


async def build(graph: object, org_id: str, cleanup: contextlib.AsyncExitStack) -> tuple:
    """A user gated to an open App and a hidden Collection; an App without vector membership and one the user is not gated to. Returns what ``check`` reads."""
    run, now = uuid.uuid4().hex[:8], get_epoch_timestamp_in_ms()
    apps = {k: f"{k}-{run}" for k in ("open", "hidden", "unready", "ungated")}
    user_id, user_key = f"user-{run}", f"u-{run}"

    async def remove() -> None:
        with contextlib.suppress(Exception):
            await graph.delete_nodes_and_edges(list(apps.values()), CollectionNames.APPS.value)
            await graph.delete_nodes_and_edges([user_key], CollectionNames.USERS.value)
    cleanup.push_async_callback(remove)
    await graph.batch_upsert_nodes(
        [{"id": user_key, "userId": user_id, "orgId": org_id, "email": f"{user_id}@example.com", "fullName": "Container Tester", "isActive": True,
          "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.USERS.value)
    flags = {"open": READY, "hidden": {**READY, "isHidden": True}, "unready": {}, "ungated": READY}
    await graph.batch_upsert_nodes(
        [{"id": app, "orgId": org_id, "name": k, "type": "Drive", "appGroup": "Google Workspace", "scope": "team", "isActive": True,
          "createdAtTimestamp": now, "updatedAtTimestamp": now, **flags[k]} for k, app in apps.items()], collection=CollectionNames.APPS.value)

    async def gate(*keys: str) -> None:
        await graph.batch_create_edges(
            [{"from_id": user_key, "from_collection": CollectionNames.USERS.value, "to_id": apps[k], "to_collection": CollectionNames.APPS.value,
              "syncState": "COMPLETED", "lastSyncUpdate": now, "createdAtTimestamp": now, "updatedAtTimestamp": now} for k in keys],
            collection=CollectionNames.USER_APP_RELATION.value)
    await gate("open", "hidden")
    return graph, org_id, user_id, apps, gate


async def check(world: tuple) -> None:
    """The same answers on every store (the PostgreSQL provider runs this from tests/integration/graph_db/postgres/test_accessible_containers_pg.py)."""
    graph, org_id, user_id, apps, gate = world

    got = await graph.get_accessible_containers(user_id, org_id)
    assert got.fallback_reason is None and got.usable
    assert got.app_ids == {apps["open"]} and got.scope_connector_ids is None          # the hidden Collection is out of an unscoped search
    assert not got.app_ids_trusted and not got.record_group_ids and not got.direct_records

    scope = [apps["open"], apps["hidden"], apps["ungated"]]
    got = await graph.get_accessible_containers(user_id, org_id, {"apps": scope})
    assert got.app_ids == {apps["open"], apps["hidden"]}                               # named, the hidden one is reached; the ungated one never
    assert got.scope_connector_ids == frozenset(scope)

    got = await graph.get_accessible_containers(user_id, org_id, {"strictScope": True})
    assert got.is_empty and got.fallback_reason is None                                # a project chat with nothing selected searches nothing
    assert (await graph.get_accessible_containers(user_id, org_id, {"departments": ["d"]})).fallback_reason       # a record-level filter takes the record-id path
    assert (await graph.get_accessible_containers("nobody", org_id)).fallback_reason == "user_not_found"
    assert not (await graph.get_accessible_containers(user_id, f"other-{org_id}")).app_ids                           # another org reaches nothing

    await gate("unready")                                                              # an App whose points carry no connectorIds yet
    got = await graph.get_accessible_containers(user_id, org_id)
    assert got.fallback_reason == f"membership_not_backfilled:{apps['unready']}" and not got.usable


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple]:
    from tests.integration.real_graph import (
        backend_unavailable,
        connect_arango,
        connect_neo4j,
    )

    log = logging.getLogger("accessible-containers-it")
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (connect_neo4j(log, monkeypatch) if request.param == "neo4j" else connect_arango(log, "accessible_containers_qa"))
        except Exception as exc:
            backend_unavailable(request.param, exc)
        if (disconnect := getattr(graph, "disconnect", None)) is not None:
            cleanup.push_async_callback(disconnect)
        yield await build(graph, f"org-ac-{uuid.uuid4().hex[:10]}", cleanup)


async def test_the_containers_are_the_gated_apps_on_every_store(world: tuple) -> None:
    await check(world)
