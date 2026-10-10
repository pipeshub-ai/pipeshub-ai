"""delete_chat_content_reader_edges and its migration, on a real Neo4j and a real ArangoDB.

  PCC_GATE=1 PCC_NEO4J_URI=... PCC_NEO4J_PASSWORD=... PCC_ARANGO_URL=... PCC_ARANGO_PASSWORD=... \\
    pytest tests/integration/graph_db/test_chat_grant_edges_cleanup_real_backends.py -m integration
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.migrations.chat_grant_edges_cleanup_migration import (
    ChatGrantEdgesCleanupMigrationService,
)
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

from ._backends import arango_env as load_arango_env
from ._backends import neo4j_env as load_neo4j_env
from ._backends import unavailable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "chat_grant_cleanup_it"
FLAG = "/migrations/chat_grant_edges_cleanup_v1"

logger = logging.getLogger("chat-grant-cleanup-it")


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
                "MATCH (n) WHERE n.orgId STARTS WITH $org OR n.id STARTS WITH $org DETACH DELETE n", parameters={"org": org_id}
            )
            await provider.disconnect()
        return

    env = load_arango_env()
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={
        "url": env.url, "username": env.user, "password": env.password, "db": ARANGO_DB,
    })
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
            "FOR e IN permission FILTER CONTAINS(e._from, @k) OR CONTAINS(e._to, @k) REMOVE e IN permission",
            {"k": f"{org_id}-"},
        )
        await provider.http_client.execute_aql(
            "FOR d IN organizations FILTER STARTS_WITH(d._key, @org) REMOVE d IN organizations", {"org": org_id}
        )
        for collection in ("users", "records", "artifacts", "teams"):
            await provider.http_client.execute_aql(
                f"FOR d IN {collection} FILTER STARTS_WITH(d.orgId, @org) REMOVE d IN {collection}", {"org": org_id}
            )


class _Graph:
    def __init__(self, provider, org_id: str) -> None:
        self.p = provider
        self.org = org_id
        self.edges: dict[str, tuple[str, str]] = {}

    def key(self, name: str) -> str:
        return f"{self.org}-{name}"

    async def _node(self, ident: str, collection: str, **props) -> str:
        k = self.key(ident)
        await self.p.batch_upsert_nodes([{"id": k, "orgId": self.org, **props}], collection)
        return k

    async def user(self, name: str) -> str:
        k = self.key(name)
        return await self._node(name, CollectionNames.USERS.value, userId=k, email=f"{k}@example.com", isActive=True)

    async def record(self, name: str, connector: str, *, conversation_id: str | None = None) -> str:
        rid = await self._node(
            name, CollectionNames.RECORDS.value, recordName=name, recordType="FILE", origin="UPLOAD",
            connectorName=connector, connectorId=self.key("conn"), externalRecordId=name, isDeleted=False,
            mimeType="text/plain", createdAtTimestamp=1, updatedAtTimestamp=1,
        )
        if conversation_id is not None:
            await self._node(name, CollectionNames.ARTIFACTS.value, name=name, conversationId=conversation_id)
        return rid

    async def edge(self, label: str, src: str, src_col: str, dst: str, kind: str, role: str | None) -> None:
        edge = {
            "from_id": src, "from_collection": src_col,
            "to_id": dst, "to_collection": CollectionNames.RECORDS.value,
            "type": kind, "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
        }
        if role is not None:
            edge["role"] = role
        await self.p.batch_create_edges([edge], CollectionNames.PERMISSION.value)
        self.edges[label] = (src, dst)

    async def exists(self, label: str) -> bool:
        src, dst = self.edges[label]
        if isinstance(self.p, Neo4jProvider):
            rows = await self.p.client.execute_query(
                "MATCH ({id: $s})-[p:PERMISSION]->(:Record {id: $d}) RETURN count(p) AS n", {"s": src, "d": dst}
            )
            return rows[0]["n"] > 0
        rows = await self.p.http_client.execute_aql(
            "FOR p IN permission FILTER CONTAINS(p._from, @s) AND p._to == @d COLLECT WITH COUNT INTO n RETURN n",
            {"s": src, "d": f"records/{dst}"},
        )
        return rows[0] > 0

    async def total(self) -> int:
        if isinstance(self.p, Neo4jProvider):
            rows = await self.p.client.execute_query(
                "MATCH ()-[p:PERMISSION]->(r:Record) WHERE r.orgId = $o RETURN count(p) AS n", {"o": self.org}
            )
            return rows[0]["n"]
        rows = await self.p.http_client.execute_aql(
            "FOR p IN permission FILTER CONTAINS(p._to, @o) COLLECT WITH COUNT INTO n RETURN n", {"o": self.org}
        )
        return rows[0]


U, R = CollectionNames.USERS.value, CollectionNames.RECORDS.value


async def _seed_all_kinds(g: _Graph) -> None:
    ua, ub = await g.user("ua"), await g.user("ub")
    team = await g._node("team", CollectionNames.TEAMS.value, name="t")
    org = g.key("orgnode")
    await g.p.batch_upsert_nodes(
        [{"id": org, "accountType": "enterprise", "name": "o", "isActive": True}],
        CollectionNames.ORGS.value,
    )
    att = await g.record("att", "ATTACHMENTS")
    art = await g.record("art", "CODING_SANDBOX", conversation_id="conv-1")
    art_empty = await g.record("artempty", "SLACK", conversation_id="")
    kbrec = await g.record("kbrec", "KB")

    await g.edge("att_reader", ub, U, att, "USER", "READER")
    await g.edge("art_reader", ub, U, art, "USER", "READER")
    await g.edge("att_owner", ua, U, att, "USER", "OWNER")
    await g.edge("art_owner", ua, U, art, "USER", "OWNER")
    await g.edge("att_writer", await g.user("uc"), U, att, "USER", "WRITER")
    await g.edge("kb_reader", ub, U, kbrec, "USER", "READER")
    await g.edge("art_empty_reader", ub, U, art_empty, "USER", "READER")
    await g.edge("att_org", org, "organizations", att, "ORG", "READER")
    await g.edge("art_team", team, CollectionNames.TEAMS.value, art, "TEAM", "READER")


DELETED = ["att_reader", "art_reader"]
KEPT = ["att_owner", "art_owner", "att_writer", "kb_reader", "art_empty_reader", "att_org", "art_team"]


@pytest.mark.asyncio
async def test_only_user_reader_chat_edges_are_deleted(backend) -> None:
    provider, org = backend
    g = _Graph(provider, org)
    await _seed_all_kinds(g)

    assert await provider.delete_chat_content_reader_edges() == 2

    for label in DELETED:
        assert not await g.exists(label), label
    for label in KEPT:
        assert await g.exists(label), label


@pytest.mark.asyncio
async def test_batches_and_idempotent(backend) -> None:
    provider, org = backend
    g = _Graph(provider, org)
    owner = await g.user("owner")
    att = await g.record("att", "ATTACHMENTS")
    art = await g.record("art", "CODING_SANDBOX", conversation_id="c")
    readers = [await g.user(f"r{i}") for i in range(3)]
    await g.edge("owner", owner, U, att, "USER", "OWNER")
    await provider.batch_create_edges(
        [
            {"from_id": u, "from_collection": U, "to_id": t, "to_collection": R,
             "type": "USER", "role": "READER", "createdAtTimestamp": 1, "updatedAtTimestamp": 1}
            for u, t in [(readers[0], att), (readers[1], att), (readers[2], att), (readers[0], art), (readers[1], art)]
        ],
        CollectionNames.PERMISSION.value,
    )
    assert await g.total() == 6

    assert await provider.delete_chat_content_reader_edges(batch_size=2) == 5
    assert await g.total() == 1
    assert await g.exists("owner")
    assert await provider.delete_chat_content_reader_edges(batch_size=2) == 0


@pytest.mark.asyncio
async def test_migration_sets_flag_only_after_success_and_skips_when_set(backend) -> None:
    provider, org = backend
    g = _Graph(provider, org)
    await _seed_all_kinds(g)
    flags: dict = {}
    config = MagicMock()
    config.get_config = AsyncMock(side_effect=lambda key, *a, **k: flags.get(key))
    config.set_config = AsyncMock(side_effect=lambda key, value: flags.__setitem__(key, value))

    failing = MagicMock()
    failing.delete_chat_content_reader_edges = AsyncMock(side_effect=RuntimeError("boom"))
    result = await ChatGrantEdgesCleanupMigrationService(failing, config, logger).migrate()
    assert result["success"] is False and FLAG not in flags

    result = await ChatGrantEdgesCleanupMigrationService(provider, config, logger, batch_size=1).migrate()
    assert result["success"] is True and result["deleted"] == 2
    assert flags[FLAG]["done"] is True and flags[FLAG]["deleted"] == 2
    assert not await g.exists("att_reader")

    # A reader edge re-created after the flag is set is left alone: the flag short-circuits.
    await g.edge("late", await g.user("late"), U, g.key("att"), "USER", "READER")
    again = await ChatGrantEdgesCleanupMigrationService(provider, config, logger).migrate()
    assert again["skipped"] is True and await g.exists("late")
