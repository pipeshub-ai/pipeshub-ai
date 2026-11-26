"""The schedule sweep's listing of scheduled connectors, on a real Neo4j and ArangoDB.

The route lists every org's active connectors, each org read under its own context, under the org-less service token the sweep
sends. Both stores answer a connector read for every org, so each org keeps only its own connectors: two orgs' connectors are
each listed once, with their own org. The PostgreSQL provider runs it in ``postgres/test_system_scope.py``.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.api.router import get_all_scheduled_connector_instances_internal
from app.core import org_context
from app.core.org_context import OrgContext, bind
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

ARANGO_DB = "connector_schedule_listing_it"
ORGS, APPS = CollectionNames.ORGS.value, CollectionNames.APPS.value

logger = logging.getLogger("connector-schedule-listing-it")


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[IGraphDBProvider, dict[str, str]]]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB))
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        run = uuid.uuid4().hex[:10]
        ids = {k: f"{k}-sched-{run}" for k in ("org_a", "org_b", "app_a", "app_b", "off_a")}
        seeded = {ORGS: [ids["org_a"], ids["org_b"]], APPS: [ids["app_a"], ids["app_b"], ids["off_a"]]}

        async def remove() -> None:
            for coll, keys in seeded.items():
                with contextlib.suppress(Exception):
                    await graph.delete_nodes_and_edges(keys, coll)
        cleanup.push_async_callback(remove)
        now = get_epoch_timestamp_in_ms()
        assert await graph.batch_upsert_nodes([{"_key": ids[o], "accountType": "individual", "isActive": True, "name": o} for o in ("org_a", "org_b")], ORGS)
        app = {"type": "JIRA", "appGroup": "Atlassian", "scope": "team", "createdAtTimestamp": now}
        assert await graph.batch_upsert_nodes([
            {"_key": ids["app_a"], "name": "a", "isActive": True, "orgId": ids["org_a"], **app},
            {"_key": ids["app_b"], "name": "b", "isActive": True, "orgId": ids["org_b"], **app},
            {"_key": ids["off_a"], "name": "off", "isActive": False, "orgId": ids["org_a"], **app},
        ], APPS)
        yield graph, ids


def _request(graph: IGraphDBProvider) -> MagicMock:
    """The request the schedule sweep sends: this provider, and every connector's settings say SCHEDULED."""
    request = MagicMock()
    request.app.container.logger.return_value = logger
    request.app.container.config_service.return_value = MagicMock(get_config=AsyncMock(
        return_value={"sync": {"selectedStrategy": "SCHEDULED", "scheduledConfig": {"intervalMinutes": 30}}}))
    request.app.state.graph_provider = graph
    return request


async def test_each_orgs_active_connectors_are_listed_once_with_their_org(world: tuple[IGraphDBProvider, dict[str, str]]) -> None:
    graph, ids = world
    request = _request(graph)
    with bind(OrgContext(org_context.SYSTEM_ORG_ID, "system", "service", "scoped_jwt", "t")):
        listed, page = [], 1
        while True:
            answer = await get_all_scheduled_connector_instances_internal(request, page=page, limit=200, connector_ids=None)
            listed += answer["items"]
            if not answer["hasMore"]:
                break
            page += 1
        by_id = await get_all_scheduled_connector_instances_internal(
            request, page=1, limit=50, connector_ids=f"{ids['app_a']},{ids['app_b']}")
    mine = [(i["connectorId"], i["orgId"]) for i in listed if i["connectorId"].endswith(ids["app_a"][-10:])]
    assert sorted(mine) == sorted([(ids["app_a"], ids["org_a"]), (ids["app_b"], ids["org_b"])])
    assert answer["failedOrgs"] == []
    assert {i["connectorId"]: i["orgId"] for i in by_id["items"]} == {ids["app_a"]: ids["org_a"], ids["app_b"]: ids["org_b"]}
