"""Agent handles on a real Neo4j and a real ArangoDB: the unique (orgId, handle) constraint /
index, the three provider methods, the create race through AgentService, and the backfill.

Environment: PCC_NEO4J_URI, PCC_NEO4J_PASSWORD, PCC_ARANGO_URL, PCC_ARANGO_PASSWORD
(see tests/integration/graph_db/README.md); PCC_GATE=1 turns a missing backend into a failure.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.migrations.agent_handles_migration import AgentHandlesMigrationService
from app.modules.agents.service.agent_service import AgentService
from app.modules.agents.service.errors import HandleTakenError
from app.modules.agents.service.models import AgentActor, AgentSpec, ChatProvenance
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.errors import UniqueConstraintViolation
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

from ._backends import arango_env as load_arango_env
from ._backends import neo4j_env as load_neo4j_env
from ._backends import unavailable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "agent_handles_it"
AGENTS = CollectionNames.AGENT_INSTANCES.value
USERS = CollectionNames.USERS.value
logger = logging.getLogger("agent-handles-it")


@pytest.fixture(params=["neo4j", "arango"])
async def backend(request, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[object, str]]:
    org_id = f"org-it-{uuid.uuid4().hex[:10]}"
    if request.param == "neo4j":
        env = load_neo4j_env()
        monkeypatch.setenv("NEO4J_URI", env.uri)
        monkeypatch.setenv("NEO4J_USERNAME", env.user)
        monkeypatch.setenv("NEO4J_PASSWORD", env.password)
        monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
        provider = Neo4jProvider(logger, MagicMock())
        try:
            if not await asyncio.wait_for(provider.connect(), timeout=60):
                raise ConnectionError("connect returned False")
        except Exception as exc:
            unavailable(f"Neo4j not available at {env.uri}: {exc}")
        await provider.ensure_schema()
        try:
            yield provider, org_id
        finally:
            await provider.client.execute_query(
                "MATCH (n) WHERE n.orgId STARTS WITH $org DETACH DELETE n", parameters={"org": org_id}
            )
            await provider.disconnect()
        return

    env = load_arango_env()
    config_service = MagicMock()
    config_service.get_config = AsyncMock(
        return_value={"url": env.url, "username": env.user, "password": env.password, "db": ARANGO_DB}
    )
    provider = ArangoHTTPProvider(logger, config_service)
    try:
        if not await asyncio.wait_for(provider.connect(), timeout=60):
            raise ConnectionError("connect returned False")
        await provider.ensure_schema()
    except Exception as exc:
        unavailable(f"ArangoDB not available at {env.url}: {exc}")
    try:
        yield provider, org_id
    finally:
        await provider.http_client.execute_aql(
            "FOR e IN permission FILTER STARTS_WITH(e._from, 'users/' + @org) REMOVE e IN permission", {"org": org_id}
        )
        await provider.http_client.execute_aql(
            f"FOR d IN {AGENTS} FILTER STARTS_WITH(d.orgId, @org) OR STARTS_WITH(d.createdBy, @org) REMOVE d IN {AGENTS}",
            {"org": org_id},
        )
        await provider.http_client.execute_aql(
            f"FOR d IN {USERS} FILTER STARTS_WITH(d.orgId, @org) REMOVE d IN {USERS}", {"org": org_id}
        )


def _agent(key: str, org: str | None, handle: str | None = None, name: str | None = None, created_by: str = "u", ts: int = 1) -> dict:
    doc = {
        "id": key, "name": name or f"Agent {key}", "description": "d", "startMessage": "hi", "systemPrompt": "sp",
        "models": [], "createdBy": created_by, "createdAtTimestamp": ts, "updatedAtTimestamp": ts, "isDeleted": False,
    }
    if org:
        doc["orgId"] = org
    if handle:
        doc["handle"] = handle
    return doc


async def _user(p, org: str, name: str) -> str:
    key = f"{org}-{name}"
    await p.batch_upsert_nodes(
        [{"id": key, "userId": f"mongo-{key}", "orgId": org, "email": f"{key}@example.com", "isActive": True}], USERS,
    )
    return key


class TestSchemaObjects:
    async def test_the_unique_constraint_or_index_exists(self, backend) -> None:
        p, _ = backend
        if isinstance(p, Neo4jProvider):
            rows = await p.client.execute_query(
                "SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties "
                "WHERE name = 'agent_org_handle_unique' RETURN type, labelsOrTypes, properties"
            )
            assert [(r["type"], r["labelsOrTypes"], r["properties"]) for r in rows] == [
                ("UNIQUENESS", ["AgentInstance"], ["orgId", "handle"])
            ]
            return
        client = p.http_client
        session = await client._get_session()
        async with session.get(f"{client.base_url}/_db/{client.database}/_api/index?collection={AGENTS}") as resp:
            indexes = (await resp.json())["indexes"]
        match = [i for i in indexes if i["fields"] == ["orgId", "handle"]]
        assert len(match) == 1 and match[0]["unique"] is True and match[0]["sparse"] is True

    async def test_ensure_schema_is_idempotent(self, backend) -> None:
        p, _ = backend

        assert await p.ensure_schema() in (True, None)


class TestUniqueness:
    async def test_second_agent_with_the_same_org_and_handle_is_rejected(self, backend) -> None:
        p, org = backend
        await p.batch_upsert_nodes([_agent(f"{org}-a", org, "taken-handle")], AGENTS)

        with pytest.raises(UniqueConstraintViolation):
            await p.batch_upsert_nodes([_agent(f"{org}-b", org, "taken-handle")], AGENTS)

    async def test_the_same_handle_in_another_org_is_fine(self, backend) -> None:
        p, org = backend
        await p.batch_upsert_nodes([_agent(f"{org}-a", org, "shared-name")], AGENTS)

        assert await p.batch_upsert_nodes([_agent(f"{org}-x-b", f"{org}-x", "shared-name")], AGENTS) is True

    async def test_agents_without_a_handle_never_collide(self, backend) -> None:
        p, org = backend

        assert await p.batch_upsert_nodes([_agent(f"{org}-{i}", org) for i in range(3)], AGENTS) is True

        ids = {r["id"] for r in await p.list_agents_missing_handle(500)}
        assert {f"{org}-{i}" for i in range(3)} <= ids

    async def test_update_into_a_taken_handle_is_rejected(self, backend) -> None:
        p, org = backend
        await p.batch_upsert_nodes([_agent(f"{org}-a", org, "first-one"), _agent(f"{org}-b", org, "second-one")], AGENTS)

        with pytest.raises(UniqueConstraintViolation):
            await p.update_node(f"{org}-b", AGENTS, {"handle": "first-one"})
        assert (await p.get_document(f"{org}-b", AGENTS))["handle"] == "second-one"

    async def test_concurrent_writers_of_one_handle_have_exactly_one_winner(self, backend) -> None:
        p, org = backend

        results = await asyncio.gather(
            *(p.batch_upsert_nodes([_agent(f"{org}-race-{i}", org, "race-handle")], AGENTS) for i in range(8)),
            return_exceptions=True,
        )

        assert sum(r is True for r in results) == 1
        assert sum(isinstance(r, UniqueConstraintViolation) for r in results) == 7
        assert await p.search_agent_handles(org, "race-handle") == ["race-handle"]

    async def test_a_bad_handle_never_reaches_the_graph(self, backend) -> None:
        p, org = backend

        with pytest.raises(Exception, match="handle|pattern|validation|schema"):
            await p.batch_upsert_nodes([_agent(f"{org}-bad", org, "Bad Handle")], AGENTS)


class TestProviderMethods:
    async def test_get_agent_by_handle_is_org_scoped_and_includes_soft_deleted(self, backend) -> None:
        p, org = backend
        deleted = {**_agent(f"{org}-a", org, "kept-handle"), "isDeleted": True}
        await p.batch_upsert_nodes([deleted], AGENTS)

        found = await p.get_agent_by_handle(org, "kept-handle")

        assert found["_key"] == f"{org}-a"
        assert await p.get_agent_by_handle(f"{org}-other", "kept-handle") is None
        assert await p.get_agent_by_handle(org, "no-such") is None

    async def test_search_agent_handles_filters_by_prefix_org_and_limit(self, backend) -> None:
        p, org = backend
        await p.batch_upsert_nodes(
            [_agent(f"{org}-{h}", org, h) for h in ("sales-bot", "sales-bot-2", "sales-bot-3", "other-bot")]
            + [_agent(f"{org}-x-sb", f"{org}-x", "sales-bot")],
            AGENTS,
        )

        assert await p.search_agent_handles(org, "sales-bot") == ["sales-bot", "sales-bot-2", "sales-bot-3"]
        assert await p.search_agent_handles(org, "sales-bot", 2) == ["sales-bot", "sales-bot-2"]
        assert await p.search_agent_handles(org, "zzz") == []

    async def test_list_agents_missing_handle_derives_the_org_and_orders_oldest_first(self, backend) -> None:
        p, org = backend
        creator = await _user(p, org, "creator")
        await p.batch_upsert_nodes(
            [
                _agent(f"{org}-new", None, created_by=creator, ts=30, name="Newest"),
                _agent(f"{org}-old", None, created_by=creator, ts=10, name="Oldest"),
                _agent(f"{org}-own", org, created_by="nobody", ts=20, name="Own org"),
                _agent(f"{org}-has", org, "has-handle", created_by=creator, ts=5),
                _agent(f"{org}-orphan", None, created_by="nobody", ts=1, name="Orphan"),
            ],
            AGENTS,
        )

        rows = [r for r in await p.list_agents_missing_handle(500) if r["id"].startswith(org)]

        assert [(r["id"], r["orgId"]) for r in rows] == [(f"{org}-old", org), (f"{org}-own", org), (f"{org}-new", org)]
        assert len(await p.list_agents_missing_handle(1)) == 1


class TestAgentService:
    async def _actor(self, p, org: str, name: str = "alice") -> AgentActor:
        key = await _user(p, org, name)
        return AgentActor(user_key=key, user_id=f"mongo-{key}", org_id=org)

    async def test_concurrent_creates_of_one_name_get_distinct_handles(self, backend) -> None:
        p, org = backend
        actor = await self._actor(p, org)
        service = AgentService(p, MagicMock(), logger)

        created = await asyncio.gather(*(service.create(actor, AgentSpec(name="Twin Bot")) for _ in range(4)))

        handles = sorted(c.handle for c in created)
        assert handles == ["twin-bot", "twin-bot-2", "twin-bot-3", "twin-bot-4"]
        assert len({c.agent_key for c in created}) == 4
        stored = await p.search_agent_handles(org, "twin-bot")
        assert stored == handles

    async def test_explicit_taken_handle_is_a_409_and_creates_nothing(self, backend) -> None:
        p, org = backend
        actor = await self._actor(p, org)
        service = AgentService(p, MagicMock(), logger)
        await service.create(actor, AgentSpec(name="Owner Bot"))

        with pytest.raises(HandleTakenError) as exc:
            await service.create(actor, AgentSpec(name="Another", handle="owner-bot"))

        assert exc.value.detail["suggestion"] == "owner-bot-2"
        assert await p.search_agent_handles(org, "owner-bot") == ["owner-bot"]
        assert await p.search_agent_handles(org, "another") == []

    async def test_created_agent_is_stored_with_org_and_origin(self, backend) -> None:
        p, org = backend
        actor = await self._actor(p, org)

        created = await AgentService(p, MagicMock(), logger).create(actor, AgentSpec(name="Stored Bot"))

        fetched = await p.get_agent(created.agent_key, org)
        assert (fetched["orgId"], fetched["handle"], fetched["createdVia"]) == (org, "stored-bot", "ui")
        assert (await p.get_agent_by_handle(org, "stored-bot"))["_key"] == created.agent_key
        listed = await p.get_all_agents(actor.user_key, org, page=1, limit=20)
        assert [a["handle"] for a in listed["agents"]] == ["stored-bot"]

    async def test_a_second_create_from_one_draft_returns_the_first_agent(self, backend) -> None:
        p, org = backend
        actor = await self._actor(p, org)
        service = AgentService(p, MagicMock(), logger)
        draft = ChatProvenance(conversation_id=f"{org}-conv", message_id=f"{org}-msg")

        first = await service.create(actor, AgentSpec(name="Draft Bot"), origin="chat", provenance=draft)
        again = await service.create(actor, AgentSpec(name="Draft Bot"), origin="chat", provenance=draft)

        assert (again.agent_key, again.handle) == (first.agent_key, "draft-bot")
        assert await p.search_agent_handles(org, "draft-bot") == ["draft-bot"]


class TestMigration:
    async def test_backfill_assigns_handles_resumably_and_idempotently(self, backend) -> None:
        p, org = backend
        creator = await _user(p, org, "creator")
        await p.batch_upsert_nodes(
            [
                _agent(f"{org}-1", None, created_by=creator, ts=1, name="Sales Bot"),
                _agent(f"{org}-2", None, created_by=creator, ts=2, name="sales bot"),
                _agent(f"{org}-3", None, created_by=creator, ts=3, name="Assistant"),
            ],
            AGENTS,
        )
        config = MagicMock()
        config.get_config = AsyncMock(return_value=None)
        config.set_config = AsyncMock()

        first = await AgentHandlesMigrationService(p, config, logger).migrate()
        second = await AgentHandlesMigrationService(p, config, logger).migrate()

        assert first["success"] is True and second["agents_updated"] == 0
        mine = await p.search_agent_handles(org, "")
        assert mine == ["assistant-agent", "sales-bot", "sales-bot-2"]
        assert (await p.get_agent_by_handle(org, "sales-bot"))["_key"] == f"{org}-1"
        assert (await p.get_agent_by_handle(org, "sales-bot-2"))["orgId"] == org
        config.set_config.assert_awaited()
