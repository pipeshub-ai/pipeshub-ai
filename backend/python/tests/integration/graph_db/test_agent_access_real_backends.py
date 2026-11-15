"""Agent and agent template access on a real Neo4j and ArangoDB.

A template's ``can_edit`` / ``can_delete`` in the list and in ``get_template`` say what ``update_agent_template`` / ``delete_agent_template``
will do: both need the user's own OWNER grant, so a direct writer and a team owner are told no. And an agent's permission check before an
edit or a delete reads the grants as the caller's transaction sees them: a grant written earlier in it counts, one removed in it does not.
The PostgreSQL provider runs the same cases in ``postgres/test_kb_listings.py`` and ``postgres/test_agents.py``.

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

ARANGO_DB = "agent_access_it"
ORGS, USERS, TEAMS = CollectionNames.ORGS.value, CollectionNames.USERS.value, CollectionNames.TEAMS.value
AGENTS, TEMPLATES, PERMISSION = CollectionNames.AGENT_INSTANCES.value, CollectionNames.AGENT_TEMPLATES.value, CollectionNames.PERMISSION.value

logger = logging.getLogger("agent-access-it")


def _edge(from_coll: str, from_id: str, to_coll: str, to_id: str, kind: str, role: str) -> dict:
    now = get_epoch_timestamp_in_ms()
    return {"from_id": from_id, "from_collection": from_coll, "to_id": to_id, "to_collection": to_coll, "type": kind, "role": role,
            "createdAtTimestamp": now, "updatedAtTimestamp": now}


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
        ids = {k: f"{k}-agentaccess-{run}" for k in ("org", "owner", "writer", "teamowner", "team", "tpl", "agent")}
        seeded = {ORGS: [ids["org"]], USERS: [ids["owner"], ids["writer"], ids["teamowner"]], TEAMS: [ids["team"]], TEMPLATES: [ids["tpl"]], AGENTS: [ids["agent"]]}

        async def remove() -> None:
            for coll, keys in seeded.items():
                with contextlib.suppress(Exception):
                    await graph.delete_nodes_and_edges(keys, coll)
        cleanup.push_async_callback(remove)
        now = get_epoch_timestamp_in_ms()
        assert await graph.batch_upsert_nodes([{"_key": ids["org"], "accountType": "enterprise", "isActive": True, "name": "agent access"}], ORGS)
        assert await graph.batch_upsert_nodes([{"_key": ids[u], "userId": f"uid-{ids[u]}", "orgId": ids["org"], "email": f"{u}-{run}@example.com", "isActive": True}
                                               for u in ("owner", "writer", "teamowner")], USERS)
        assert await graph.batch_upsert_nodes([{"_key": ids["team"], "name": f"team {run}", "orgId": ids["org"], "createdBy": ids["owner"],
                                                "createdAtTimestamp": now, "updatedAtTimestamp": now}], TEAMS)
        assert await graph.batch_upsert_nodes([{"_key": ids["tpl"], "name": "tpl", "description": "d", "startMessage": "s", "systemPrompt": "p", "orgId": ids["org"],
                                                "isActive": True, "isDeleted": False, "createdBy": ids["owner"], "createdAtTimestamp": now}], TEMPLATES)
        assert await graph.batch_upsert_nodes([{"_key": ids["agent"], "name": "agent", "description": "d", "startMessage": "s", "systemPrompt": "p", "models": [],
                                                "createdBy": ids["owner"], "createdAtTimestamp": now, "updatedAtTimestamp": now, "isDeleted": False}], AGENTS)
        assert await graph.batch_create_edges([
            _edge(USERS, ids["owner"], TEMPLATES, ids["tpl"], "USER", "OWNER"),
            _edge(USERS, ids["writer"], TEMPLATES, ids["tpl"], "USER", "WRITER"),
            _edge(USERS, ids["teamowner"], TEAMS, ids["team"], "USER", "READER"),
            _edge(TEAMS, ids["team"], TEMPLATES, ids["tpl"], "TEAM", "OWNER"),
        ], PERMISSION)
        yield graph, ids


async def test_a_templates_listed_edit_and_delete_rights_are_what_the_writes_allow(world: tuple[IGraphDBProvider, dict[str, str]]) -> None:
    graph, ids = world
    for user, expected in (("writer", False), ("teamowner", False), ("owner", True)):      # a direct writer and a team owner may not; the owner may
        listed = next(t for t in await graph.get_all_agent_templates(ids[user]) if t["_key"] == ids["tpl"])
        opened = await graph.get_template(ids["tpl"], ids[user])
        assert (listed["can_edit"], listed["can_delete"], opened["can_edit"], opened["can_delete"]) == (expected,) * 4, user
        assert bool(await graph.update_agent_template(ids["tpl"], {"name": f"by {user}"}, ids[user])) is expected, user
    for user in ("writer", "teamowner"):
        assert not await graph.delete_agent_template(ids["tpl"], ids[user]), user
    assert await graph.delete_agent_template(ids["tpl"], ids["owner"])


async def test_an_agents_permission_check_reads_through_the_callers_transaction(world: tuple[IGraphDBProvider, dict[str, str]]) -> None:
    graph, ids = world
    txn = await graph.begin_transaction(read=[USERS, ORGS, AGENTS, PERMISSION], write=[AGENTS, PERMISSION], explicit=True)
    try:
        assert await graph.check_agent_permission(ids["agent"], ids["writer"], ids["org"], transaction=txn) is None
        assert await graph.batch_create_edges([_edge(USERS, ids["writer"], AGENTS, ids["agent"], "USER", "OWNER")], PERMISSION, transaction=txn)
        perm = await graph.check_agent_permission(ids["agent"], ids["writer"], ids["org"], transaction=txn)
        assert perm and perm["user_role"] == "OWNER"                                                       # a grant written earlier in the transaction counts
        assert await graph.update_agent(ids["agent"], {"name": "renamed"}, ids["writer"], ids["org"], transaction=txn)
        assert await graph.delete_edge(ids["writer"], USERS, ids["agent"], AGENTS, PERMISSION, transaction=txn)
        assert await graph.check_agent_permission(ids["agent"], ids["writer"], ids["org"], transaction=txn) is None      # and one removed in it does not
        assert not await graph.delete_agent(ids["agent"], ids["writer"], ids["org"], transaction=txn)
    finally:
        await graph.rollback_transaction(txn)
    agent = await graph.get_document(ids["agent"], AGENTS)
    assert agent["name"] == "agent" and not agent.get("isDeleted")                                         # rolled back: nothing kept


async def test_an_agents_permission_update_checks_the_owner_in_the_callers_transaction(world: tuple[IGraphDBProvider, dict[str, str]]) -> None:
    graph, ids = world
    txn = await graph.begin_transaction(read=[USERS, ORGS, AGENTS, PERMISSION, TEAMS], write=[AGENTS, PERMISSION], explicit=True)
    try:
        assert await graph.batch_create_edges([_edge(USERS, ids["writer"], AGENTS, ids["agent"], "USER", "OWNER"),
                                               _edge(USERS, ids["teamowner"], AGENTS, ids["agent"], "USER", "READER")], PERMISSION, transaction=txn)
        granted = await graph.update_agent_permission(ids["agent"], ids["writer"], ids["org"], [f"uid-{ids['teamowner']}"], [], "WRITER", transaction=txn)
        assert granted and granted.get("reason") != "Agent not found or no permission", granted          # an owner grant written earlier in the transaction counts
        assert await graph.delete_edge(ids["writer"], USERS, ids["agent"], AGENTS, PERMISSION, transaction=txn)
        refused = await graph.update_agent_permission(ids["agent"], ids["writer"], ids["org"], [f"uid-{ids['teamowner']}"], [], "READER", transaction=txn)
        assert refused == {"success": False, "reason": "Agent not found or no permission"}               # and one removed in it does not
    finally:
        await graph.rollback_transaction(txn)
