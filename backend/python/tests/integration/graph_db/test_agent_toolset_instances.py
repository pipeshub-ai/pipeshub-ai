"""MG-8 (P2.2): the agent toolset migration on a real Neo4j and a real ArangoDB.

Requires the servers of ``tests/integration/compose/graph-db.yml`` (or any Neo4j 5 and ArangoDB 3.12
at ``NEO4J_TEST_URI`` / ``ARANGO_TEST_URL``); skipped when they are absent.
Run: pytest tests/integration/graph_db/test_agent_toolset_instances.py -m integration

What only the real servers show: the providers' edge and document shapes the migration reads, the
guarded replace on both backends, the toolset schema ArangoDB enforces, and that a reverted node has
no ``instanceId`` field (not a ``null`` one).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.config.constants.arangodb import CollectionNames
from app.harness.tool_identity.migration import LEDGER_PATH, AgentToolsetMigration
from tests.integration.graph_db.test_agent_engine_profile import (  # noqa: F401 - fixtures
    arango_provider,
    neo4j_provider,
    provider,
)
from tests.unit.harness.tool_identity.support import FakeConfig, instance

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

AGENTS = CollectionNames.AGENT_INSTANCES.value
TOOLSETS = CollectionNames.AGENT_TOOLSETS.value
HAS_TOOLSET = CollectionNames.AGENT_HAS_TOOLSET.value
USERS = CollectionNames.USERS.value


class _Seeded:
    def __init__(self, graph: IGraphDBProvider, run: str) -> None:
        self.provider = graph
        self.run = run
        self.org = f"org-{run}"
        self.keys: dict[str, list[str]] = {AGENTS: [], TOOLSETS: [], USERS: []}

    def key(self, name: str) -> str:
        return f"p22-{self.run}-{name}"

    async def agent(self, name: str, *toolsets: dict[str, Any]) -> None:
        user, agent = self.key(f"{name}-user"), self.key(name)
        await self.provider.batch_upsert_nodes([{"_key": user, "email": f"{user}@example.test", "orgId": self.org}], USERS)
        await self.provider.batch_upsert_nodes([{
            "_key": agent, "name": name, "description": "d", "startMessage": "s", "systemPrompt": "p", "models": [],
            "createdBy": user, "createdAtTimestamp": 1, "updatedAtTimestamp": 1, "isDeleted": False,
        }], AGENTS)
        nodes = [
            {
                "_key": self.key(t["key"]), "name": t["name"], "displayName": t["name"].title(), "type": "app",
                "userId": user, "createdBy": user, "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
                **({"instanceId": t["instance"]} if t.get("instance") else {}),
            }
            for t in toolsets
        ]
        await self.provider.batch_upsert_nodes(nodes, TOOLSETS)
        await self.provider.batch_create_edges(
            [{"_from": f"{AGENTS}/{agent}", "_to": f"{TOOLSETS}/{n['_key']}", "createdAtTimestamp": 1, "updatedAtTimestamp": 1} for n in nodes],
            HAS_TOOLSET,
        )
        self.keys[USERS].append(user)
        self.keys[AGENTS].append(agent)
        self.keys[TOOLSETS].extend(n["_key"] for n in nodes)

    async def node(self, name: str) -> dict[str, Any]:
        found = await self.provider.get_document(self.key(name), TOOLSETS)
        assert found is not None
        return found

    async def cleanup(self) -> None:
        for agent in self.keys[AGENTS]:
            await self.provider.delete_edges_from(agent, AGENTS, HAS_TOOLSET)
        for collection, keys in self.keys.items():
            if keys:
                await self.provider.delete_nodes(keys, collection)


async def test_mg8_dry_run_apply_idempotence_revert_and_the_ambiguity_report(provider: IGraphDBProvider) -> None:  # noqa: F811
    world = _Seeded(provider, uuid.uuid4().hex[:8])
    try:
        await world.agent("one", {"key": "jira", "name": "jira"}, {"key": "slack", "name": "slack", "instance": "slack-1"})
        await world.agent("two", {"key": "gh", "name": "github"}, {"key": "jira2", "name": "jira"})
        config = FakeConfig([
            instance("jira-1", "jira", world.org),
            instance("gh-1", "github", world.org),
            instance("gh-2", "github", world.org),
        ])
        clock = iter(range(10_000, 20_000))
        migration = AgentToolsetMigration(provider, config, now_ms=lambda: next(clock))  # type: ignore[arg-type]
        mine = {world.key("one"), world.key("two")}

        dry = await migration.dry_run()
        assert {r["toolsetKey"] for r in dry.rekeyed if r["agentKey"] in mine} == {world.key("jira")}
        held = {h["agentKey"]: h["unresolved"] for h in dry.held if h["agentKey"] in mine}
        assert held == {world.key("two"): [{"toolsetKey": world.key("gh"), "toolset": "github", "reason": "ambiguous", "candidates": ["gh-1", "gh-2"]}]}
        assert "instanceId" not in await world.node("jira")
        assert LEDGER_PATH not in config.values

        applied = await migration.apply()
        assert {r["toolsetKey"] for r in applied.rekeyed if r["agentKey"] in mine} == {world.key("jira")}
        node = await world.node("jira")
        assert (node["instanceId"], node["instanceName"], node["displayName"]) == ("jira-1", "jira jira-1", "Jira")
        assert "instanceId" not in await world.node("jira2"), "the held agent is untouched"
        agent = await provider.get_agent(world.key("one"))
        assert agent is not None
        assert {t.get("instanceId") for t in agent["toolsets"]} == {"jira-1", "slack-1"}

        again = await migration.apply()
        assert [r for r in again.rekeyed if r["agentKey"] in mine] == []

        reverted = await migration.revert()
        assert {r["toolsetKey"] for r in reverted.reverted} >= {world.key("jira")}
        node = await world.node("jira")
        assert "instanceId" not in node and "instanceName" not in node
        assert (await world.node("slack"))["instanceId"] == "slack-1"
        assert (await migration.revert()).reverted == []
    finally:
        await world.cleanup()
