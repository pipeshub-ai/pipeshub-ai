"""A service-account agent uses only the sources shared with its org, on every graph store.

The agent runs on one identity (its creator, unless bound to a service account) while anyone in
the org may use it, so of that identity's sources it keeps only the org's team connectors, and a
collection only when it is bound. The same rule refuses such sources when the agent is saved.

Runs in backend-matrix on both graph jobs (NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL,
ARANGO_IT_PASSWORD); the PostgreSQL graph store runs the same checks from
``postgres/test_service_account_knowledge.py``.
"""
from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.api.routes.agent import InvalidRequestError, _assert_service_account_knowledge
from app.config.constants.arangodb import CollectionNames, Connectors
from app.core.org_context import for_org
from app.modules.agents.knowledge_scope import service_account_knowledge
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.connector_instances import fetch_user_connector_instances
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "service_account_knowledge_it"
APPS = CollectionNames.APPS.value
ORGS = CollectionNames.ORGS.value
AGENTS = CollectionNames.AGENT_INSTANCES.value
KNOWLEDGE = CollectionNames.AGENT_KNOWLEDGE.value
logger = logging.getLogger("service-account-knowledge-it")


async def check_service_account_knowledge(graph: IGraphDBProvider) -> None:
    run = uuid.uuid4().hex[:10]
    org, other_org = run.rjust(24, "0"), run.rjust(24, "1")
    creator = f"creator-{run}"
    pg, drive, kb = Connectors.POSTGRESQL.value, Connectors.GOOGLE_DRIVE.value, Connectors.KNOWLEDGE_BASE.value
    # name -> (key, type, scope, org, created by)
    apps = {
        "pg_team": (f"pg-team-{run}", pg, "team", org, creator),
        "pg_mine": (f"pg-mine-{run}", pg, "personal", org, creator),
        "drive_team": (f"drive-team-{run}", drive, "team", org, creator),
        "drive_mine": (f"drive-mine-{run}", drive, "personal", org, creator),
        "drive_colleague": (f"drive-colleague-{run}", drive, "personal", org, f"colleague-{run}"),
        "collection": (f"kb-{run}", kb, "team", org, creator),
        "other_org": (f"pg-other-{run}", pg, "team", other_org, f"other-{run}"),
    }
    key = {name: spec[0] for name, spec in apps.items()}
    agent_key = f"agent-{run}"
    linked = ("pg_team", "other_org")
    try:
        for app_key, app_type, scope, app_org, created_by in apps.values():
            with for_org(app_org, job="test"):
                await graph.batch_upsert_nodes([{
                    "_key": app_org, "name": app_org, "accountType": "enterprise", "isActive": True, "createdAtTimestamp": 1,
                }], ORGS)
                await graph.batch_upsert_nodes([{
                    "_key": app_key, "orgId": app_org, "name": f"Name {app_key}", "type": app_type, "appGroup": app_type,
                    "scope": scope, "createdBy": created_by, "isActive": True, "isConfigured": True,
                    "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
                }], APPS)
                await graph.batch_create_edges([{
                    "from_id": app_org, "from_collection": ORGS, "to_id": app_key, "to_collection": APPS, "createdAtTimestamp": 1,
                }], collection=CollectionNames.ORG_APP_RELATION.value)

        with for_org(org, job="test"):
            # An agent whose knowledge names one of its org's apps and one of another org's.
            await graph.batch_upsert_nodes([{
                "_key": agent_key, "name": "agent", "description": "d", "startMessage": "s", "systemPrompt": "p",
                "models": [], "createdBy": creator, "isDeleted": False, "isServiceAccount": True,
                "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
            }], AGENTS)
            await graph.batch_upsert_nodes([{
                "_key": f"k-{name}-{run}", "connectorId": key[name], "filters": "{}", "createdBy": creator,
                "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
            } for name in linked], KNOWLEDGE)
            await graph.batch_create_edges([{
                "from_id": agent_key, "from_collection": AGENTS, "to_id": f"k-{name}-{run}", "to_collection": KNOWLEDGE,
                "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
            } for name in linked], collection=CollectionNames.AGENT_HAS_KNOWLEDGE.value)
            # Shared with the org, so the agent list reads it too.
            await graph.batch_create_edges([{
                "from_id": org, "from_collection": ORGS, "to_id": agent_key, "to_collection": AGENTS,
                "type": "ORG", "role": "READER", "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
            }], collection=CollectionNames.PERMISSION.value)
            agent = await graph.get_agent(agent_key, org)
            listed = [a for a in await graph.get_all_agents(f"user-{run}", org) if a.get("_key") == agent_key]
            assert agent is not None and len(listed) == 1
            for read in (agent, listed[0]):
                projected = {k["connectorId"]: (k["type"], k["name"]) for k in read["knowledge"]}
                assert projected == {
                    key["pg_team"]: (pg, f"Name {key['pg_team']}"),
                    # Another org's app is unresolved: neither its type nor its name crosses over.
                    key["other_org"]: ("UNKNOWN", key["other_org"]),
                }
            # The id filter reads only the org's apps among the ids asked.
            by_id = await graph.get_org_apps(
                org, active_only=False, app_ids=[key["pg_team"], key["other_org"], f"missing-{run}"],
            )
        assert [a.get("_key") or a.get("id") for a in by_id] == [key["pg_team"]]

        knowledge = [{"connectorId": app_key, "type": app_type} for app_key, app_type, *_ in apps.values()]
        with for_org(org, job="test"):
            own = await fetch_user_connector_instances(graph, creator, org, logger)
            unbound = await service_account_knowledge(graph, knowledge, creator, org, logger, bound=False)
            bound = await service_account_knowledge(graph, knowledge, creator, org, logger, bound=True)
            # Saving: a personal source is refused, a collection too unless bound, the org's connectors pass.
            with pytest.raises(InvalidRequestError, match=f"Personal sources \\(Name {key['drive_mine']}\\)"):
                await _assert_service_account_knowledge([key["drive_team"], key["drive_mine"]], graph, org, creator, bound=True)
            # Someone else's source is not named.
            with pytest.raises(InvalidRequestError, match=r"Personal sources \(one that someone else owns\)"):
                await _assert_service_account_knowledge([key["drive_colleague"]], graph, org, creator, bound=True)
            with pytest.raises(InvalidRequestError, match="Collections"):
                await _assert_service_account_knowledge([key["collection"]], graph, org, creator, bound=False)
            await _assert_service_account_knowledge([key["pg_team"], key["drive_team"], key["collection"]], graph, org, creator, bound=True)
            # Another organisation's connector, or an id that names nothing, is refused.
            for foreign in (key["other_org"], f"missing-{run}"):
                with pytest.raises(InvalidRequestError, match="not found in your organization"):
                    await _assert_service_account_knowledge([key["drive_team"], foreign], graph, org, creator, bound=True)
        # The creator reads their own sources when they run an agent themselves...
        assert {key["pg_mine"], key["drive_mine"]} <= {d.get("_key") or d.get("id") for d in own}
        # ...but an agent the whole org runs on their identity does not.
        assert [k["connectorId"] for k in unbound] == [key["pg_team"], key["drive_team"]]
        assert [k["connectorId"] for k in bound] == [key["pg_team"], key["drive_team"], key["collection"]]
    finally:
        for o in (org, other_org):
            with contextlib.suppress(Exception), for_org(o, job="test"):
                if isinstance(graph, Neo4jProvider):
                    await graph.client.execute_query("MATCH (n) WHERE n.id ENDS WITH $run DETACH DELETE n", parameters={"run": run})
                    continue
                await graph.delete_nodes_and_edges([s[0] for s in apps.values() if s[3] == o], APPS)
                if o == org:
                    await graph.delete_nodes_and_edges([agent_key], AGENTS)
                    await graph.delete_nodes([f"k-{name}-{run}" for name in linked], KNOWLEDGE)
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


async def test_a_service_account_agent_keeps_only_what_is_shared_with_the_org(graph: IGraphDBProvider) -> None:
    await check_service_account_knowledge(graph)
