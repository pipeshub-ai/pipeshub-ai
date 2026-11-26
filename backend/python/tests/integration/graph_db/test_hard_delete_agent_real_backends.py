"""``hard_delete_agent`` removes the agent's whole subgraph on a real Neo4j and ArangoDB, and nothing another agent still uses.

The agent holds knowledge, a toolset with a tool, and an MCP server with a tool; a second agent shares a second MCP server
with it. After the first agent is deleted, its knowledge, toolset, MCP server and their tools are gone; the shared MCP server,
its tool and the second agent stay. Neo4j left the MCP servers and their tools behind.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
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

ARANGO_DB = "hard_delete_agent_it"
C = CollectionNames
NODES = (C.AGENT_INSTANCES.value, C.AGENT_KNOWLEDGE.value, C.AGENT_TOOLSETS.value, C.AGENT_TOOLS.value, C.AGENT_MCP_SERVERS.value)

logger = logging.getLogger("hard-delete-agent-it")


def _edge(fc: str, fi: str, tc: str, ti: str) -> dict:
    return {"from_id": fi, "from_collection": fc, "to_id": ti, "to_collection": tc, "createdAtTimestamp": get_epoch_timestamp_in_ms()}


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
        ids = {k: f"{k}-hda-{run}" for k in ("agent", "other", "knowledge", "toolset", "tool", "mcp", "mcp_tool", "shared_mcp", "shared_tool")}
        coll = {"agent": C.AGENT_INSTANCES.value, "other": C.AGENT_INSTANCES.value, "knowledge": C.AGENT_KNOWLEDGE.value,
                "toolset": C.AGENT_TOOLSETS.value, "tool": C.AGENT_TOOLS.value, "mcp": C.AGENT_MCP_SERVERS.value,
                "mcp_tool": C.AGENT_TOOLS.value, "shared_mcp": C.AGENT_MCP_SERVERS.value, "shared_tool": C.AGENT_TOOLS.value}

        async def remove() -> None:
            for name, key in ids.items():
                with contextlib.suppress(Exception):
                    await graph.delete_nodes_and_edges([key], coll[name])
        cleanup.push_async_callback(remove)

        now = get_epoch_timestamp_in_ms()
        agent = {"description": "d", "startMessage": "s", "systemPrompt": "p", "models": [], "createdBy": "u", "createdAtTimestamp": now,
                 "updatedAtTimestamp": now}
        assert await graph.batch_upsert_nodes([{"_key": ids["agent"], "name": "a", **agent}, {"_key": ids["other"], "name": "b", **agent}],
                                              C.AGENT_INSTANCES.value)
        assert await graph.batch_upsert_nodes([{"_key": ids["knowledge"], "connectorId": "c", "filters": "{}", "createdBy": "u", "createdAtTimestamp": now}],
                                              C.AGENT_KNOWLEDGE.value)
        assert await graph.batch_upsert_nodes([{"_key": ids["toolset"], "name": "slack", "displayName": "Slack", "type": "app", "userId": "u",
                                                "createdBy": "u", "createdAtTimestamp": now}], C.AGENT_TOOLSETS.value)
        assert await graph.batch_upsert_nodes([{"_key": ids[k], "name": k, "fullName": f"x.{k}", "toolsetName": "x", "createdBy": "u", "createdAtTimestamp": now}
                                               for k in ("tool", "mcp_tool", "shared_tool")], C.AGENT_TOOLS.value)
        assert await graph.batch_upsert_nodes([{"_key": ids[k], "name": k, "displayName": k, "instanceId": f"i-{k}", "userId": "u", "createdBy": "u",
                                                "createdAtTimestamp": now} for k in ("mcp", "shared_mcp")], C.AGENT_MCP_SERVERS.value)
        for edges, collection in (
            ([_edge(C.AGENT_INSTANCES.value, ids["agent"], C.AGENT_KNOWLEDGE.value, ids["knowledge"])], C.AGENT_HAS_KNOWLEDGE.value),
            ([_edge(C.AGENT_INSTANCES.value, ids["agent"], C.AGENT_TOOLSETS.value, ids["toolset"])], C.AGENT_HAS_TOOLSET.value),
            ([_edge(C.AGENT_TOOLSETS.value, ids["toolset"], C.AGENT_TOOLS.value, ids["tool"])], C.TOOLSET_HAS_TOOL.value),
            ([_edge(C.AGENT_INSTANCES.value, a, C.AGENT_MCP_SERVERS.value, m) for a, m in
              ((ids["agent"], ids["mcp"]), (ids["agent"], ids["shared_mcp"]), (ids["other"], ids["shared_mcp"]))], C.AGENT_HAS_MCP_SERVER.value),
            ([_edge(C.AGENT_MCP_SERVERS.value, m, C.AGENT_TOOLS.value, t) for m, t in
              ((ids["mcp"], ids["mcp_tool"]), (ids["shared_mcp"], ids["shared_tool"]))], C.MCP_SERVER_HAS_TOOL.value),
        ):
            assert await graph.batch_create_edges(edges, collection)
        yield graph, {**ids, **{f"{k}__coll": v for k, v in coll.items()}}


async def test_the_agent_subgraph_goes_and_what_another_agent_uses_stays(world: tuple[IGraphDBProvider, dict[str, str]]) -> None:
    graph, ids = world
    result = await graph.hard_delete_agent(ids["agent"])
    assert result["agents_deleted"] == 1 and result["mcp_servers_deleted"] == 1
    present = {name: await graph.get_document(ids[name], ids[f"{name}__coll"]) is not None
               for name in ("agent", "other", "knowledge", "toolset", "tool", "mcp", "mcp_tool", "shared_mcp", "shared_tool")}
    assert present == {"agent": False, "knowledge": False, "toolset": False, "tool": False, "mcp": False, "mcp_tool": False,
                       "other": True, "shared_mcp": True, "shared_tool": True}
