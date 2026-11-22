"""The connector_active gauge's count on a real Neo4j and ArangoDB, asked for as the gauge loop asks for it: under a system context.

Both stores count every org's active connector instances in one read and ignore the context, so the system bind the PostgreSQL fix
needs changes nothing here: two orgs' apps are each counted once, inactive ones not at all. The PostgreSQL provider runs it in
``postgres/test_connector_active_counts.py``.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import system
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

ARANGO_DB = "connector_active_counts_it"
ORGS, APPS = CollectionNames.ORGS.value, CollectionNames.APPS.value

logger = logging.getLogger("connector-active-counts-it")


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[IGraphDBProvider, str]]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB))
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        run = uuid.uuid4().hex[:10]
        app_type = f"t-{run}"      # no other test holds this type, so the count is this test's alone
        orgs = [f"org-{n}-active-{run}" for n in "ab"]
        apps = [(f"app-{n}-active-{run}", org, active) for n, org, active in
                (("a1", orgs[0], True), ("a2", orgs[0], True), ("a3", orgs[0], False), ("b1", orgs[1], True))]

        async def remove() -> None:
            for coll, keys in ((APPS, [k for k, _, _ in apps]), (ORGS, orgs)):
                with contextlib.suppress(Exception):
                    await graph.delete_nodes_and_edges(keys, coll)
        cleanup.push_async_callback(remove)
        now = get_epoch_timestamp_in_ms()
        assert await graph.batch_upsert_nodes([{"_key": o, "accountType": "individual", "isActive": True, "name": o} for o in orgs], ORGS)
        assert await graph.batch_upsert_nodes([
            {"_key": k, "name": k, "type": app_type, "appGroup": "IT", "scope": "team", "isActive": active, "orgId": org,
             "createdAtTimestamp": now}
            for k, org, active in apps
        ], APPS)
        yield graph, app_type


async def test_the_count_under_a_system_context_is_every_orgs_active_apps_once(world: tuple[IGraphDBProvider, str]) -> None:
    graph, app_type = world
    with system("connector_metrics"):
        counts = await graph.count_active_apps_by_type()
    assert counts.get(app_type) == 3
