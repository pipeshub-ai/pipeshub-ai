"""A service-account agent uses only the databases shared with its org, on every graph store.

The agent runs on one identity (its creator, unless bound to a service account) while anyone in
the org may use it, so of that identity's databases only the org's team ones stay in its knowledge.

Runs in backend-matrix on both graph jobs (NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL,
ARANGO_IT_PASSWORD); the PostgreSQL graph store runs the same check from
``postgres/test_service_account_databases.py``.
"""
from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.core.org_context import for_org
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.connector_instances import fetch_user_connector_instances
from app.utils.execute_query import shared_sql_knowledge
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "service_account_databases_it"
APPS = CollectionNames.APPS.value
ORGS = CollectionNames.ORGS.value
logger = logging.getLogger("service-account-databases-it")


async def check_service_account_databases(graph: IGraphDBProvider) -> None:
    run = uuid.uuid4().hex[:10]
    org, other_org = run.rjust(24, "0"), run.rjust(24, "1")
    creator = f"creator-{run}"
    # name -> (key, scope, org, created by)
    apps = {
        "team": (f"pg-team-{run}", "team", org, creator),
        "mine": (f"pg-mine-{run}", "personal", org, creator),
        "colleague": (f"pg-colleague-{run}", "personal", org, f"colleague-{run}"),
        "other_org": (f"pg-other-{run}", "team", other_org, f"other-{run}"),
    }
    try:
        for key, scope, app_org, created_by in apps.values():
            with for_org(app_org, job="test"):
                await graph.batch_upsert_nodes([{
                    "_key": app_org, "name": app_org, "accountType": "enterprise", "isActive": True, "createdAtTimestamp": 1,
                }], ORGS)
                await graph.batch_upsert_nodes([{
                    "_key": key, "orgId": app_org, "name": key, "type": Connectors.POSTGRESQL.value, "appGroup": "Database",
                    "scope": scope, "createdBy": created_by, "isActive": True, "isConfigured": True,
                    "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
                }], APPS)
                await graph.batch_create_edges([{
                    "from_id": app_org, "from_collection": ORGS, "to_id": key, "to_collection": APPS, "createdAtTimestamp": 1,
                }], collection=CollectionNames.ORG_APP_RELATION.value)

        knowledge = [{"connectorId": key, "type": Connectors.POSTGRESQL.value} for key, *_ in apps.values()]
        knowledge.append({"connectorId": "drive", "type": Connectors.GOOGLE_DRIVE.value})
        with for_org(org, job="test"):
            own = await fetch_user_connector_instances(graph, creator, org, logger)
            kept = await shared_sql_knowledge(graph, knowledge, creator, org, logger)
        # The creator reads their own database when they run an agent themselves...
        assert {apps["team"][0], apps["mine"][0]} <= {d.get("_key") or d.get("id") for d in own}
        # ...but an agent the whole org runs on their identity does not.
        assert [k["connectorId"] for k in kept] == [apps["team"][0], "drive"]
    finally:
        for o in (org, other_org):
            with contextlib.suppress(Exception), for_org(o, job="test"):
                if isinstance(graph, Neo4jProvider):
                    await graph.client.execute_query("MATCH (n) WHERE n.id ENDS WITH $run DETACH DELETE n", parameters={"run": run})
                    continue
                await graph.delete_nodes_and_edges([key for key, _, app_org, _ in apps.values() if app_org == o], APPS)
                await graph.delete_nodes([o], ORGS)


@pytest.fixture(params=["neo4j", "arango"])
async def graph(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[IGraphDBProvider]:
    try:
        provider = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB))
    except Exception as exc:
        backend_unavailable(request.param, exc)
    yield provider
    if (disconnect := getattr(provider, "disconnect", None)) is not None:
        await disconnect()


async def test_a_service_account_agent_keeps_only_the_orgs_databases(graph: IGraphDBProvider) -> None:
    await check_service_account_databases(graph)
