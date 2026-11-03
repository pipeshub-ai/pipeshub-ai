"""Deleting a collection removes it from every agent that uses it, on a real Neo4j and ArangoDB.

An agent names a source by `connectorId`, not by an edge, so the unit tests can
only claim the delete reaches those entries. This checks that `get_agent`, which
the agent builder and chat read, no longer lists the collection, that no
agent-to-knowledge edge is left behind, and that the agent's other sources stay.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_collection_delete_agent_knowledge_real_backends.py -m integration
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
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

ARANGO_DB = "collection_delete_agents_it"
APPS = CollectionNames.APPS.value
AGENTS = CollectionNames.AGENT_INSTANCES.value
KNOWLEDGE = CollectionNames.AGENT_KNOWLEDGE.value
HAS_KNOWLEDGE = CollectionNames.AGENT_HAS_KNOWLEDGE.value

logger = logging.getLogger("collection-delete-agents-it")


@pytest.fixture(params=["neo4j", "arango"])
async def graph(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[IGraphDBProvider]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            provider = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(provider, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        yield provider


async def _seed(graph: IGraphDBProvider, run: str) -> dict[str, str]:
    now = get_epoch_timestamp_in_ms()
    ids = {
        "org": f"org-colagents-{run}",
        "doomed": f"kb-doomed-{run}",
        "kept": f"kb-kept-{run}",
        "agent_both": f"agent-both-{run}",
        "agent_doomed": f"agent-doomed-{run}",
        "k_both_doomed": f"k-both-doomed-{run}",
        "k_both_kept": f"k-both-kept-{run}",
        "k_doomed": f"k-doomed-{run}",
    }
    assert await graph.batch_upsert_nodes([
        {
            "_key": ids[key], "orgId": ids["org"], "name": name, "type": Connectors.KNOWLEDGE_BASE.value,
            "appGroup": "Local Storage", "scope": "personal", "isActive": True,
            "createdBy": f"user-{run}", "createdAtTimestamp": now, "updatedAtTimestamp": now,
        }
        for key, name in (("doomed", "Doomed"), ("kept", "Kept"))
    ], APPS)
    assert await graph.batch_upsert_nodes([
        {
            "_key": ids[key], "name": key, "description": "d", "startMessage": "s", "systemPrompt": "p",
            "models": [], "createdBy": f"user-{run}", "createdAtTimestamp": now, "updatedAtTimestamp": now,
            "isDeleted": False,
        }
        for key in ("agent_both", "agent_doomed")
    ], AGENTS)
    links = (
        ("agent_both", "k_both_doomed", "doomed"),
        ("agent_both", "k_both_kept", "kept"),
        ("agent_doomed", "k_doomed", "doomed"),
    )
    assert await graph.batch_upsert_nodes([
        {
            "_key": ids[k], "connectorId": ids[app], "filters": "{}",
            "createdBy": f"user-{run}", "createdAtTimestamp": now, "updatedAtTimestamp": now,
        }
        for _, k, app in links
    ], KNOWLEDGE)
    await graph.batch_create_edges([
        {
            "from_id": ids[agent], "from_collection": AGENTS, "to_id": ids[k], "to_collection": KNOWLEDGE,
            "createdAtTimestamp": now, "updatedAtTimestamp": now,
        }
        for agent, k, _ in links
    ], HAS_KNOWLEDGE)
    return ids


async def _sources(graph: IGraphDBProvider, agent_key: str) -> set[str]:
    agent = await graph.get_agent(agent_key)
    assert agent is not None
    return {k["connectorId"] for k in agent.get("knowledge") or []}


@pytest.mark.asyncio
async def test_deleting_a_collection_removes_it_from_every_agent(graph: IGraphDBProvider) -> None:
    ids = await _seed(graph, uuid.uuid4().hex[:10])
    try:
        assert await _sources(graph, ids["agent_both"]) == {ids["doomed"], ids["kept"]}
        assert await _sources(graph, ids["agent_doomed"]) == {ids["doomed"]}

        result = await graph.delete_connector_instance(ids["doomed"], ids["org"])
        assert result.get("success") is True, result

        assert await _sources(graph, ids["agent_both"]) == {ids["kept"]}
        assert await _sources(graph, ids["agent_doomed"]) == set()
        for key in ("k_both_doomed", "k_doomed"):
            assert await graph.get_document(ids[key], KNOWLEDGE) is None
        assert await graph.get_document(ids["k_both_kept"], KNOWLEDGE) is not None
        assert len(await graph.get_edges_from_node(f"{AGENTS}/{ids['agent_both']}", HAS_KNOWLEDGE)) == 1
        assert await graph.get_edges_from_node(f"{AGENTS}/{ids['agent_doomed']}", HAS_KNOWLEDGE) == []
        assert await graph.check_connector_in_use(ids["doomed"]) == []
    finally:
        with contextlib.suppress(Exception):
            await graph.delete_nodes_and_edges([ids["agent_both"], ids["agent_doomed"]], AGENTS)
            await graph.delete_nodes_and_edges([ids["k_both_doomed"], ids["k_both_kept"], ids["k_doomed"]], KNOWLEDGE)
            await graph.delete_nodes_and_edges([ids["doomed"], ids["kept"]], APPS)
