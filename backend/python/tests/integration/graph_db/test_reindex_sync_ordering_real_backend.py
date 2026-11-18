"""A reindex no longer overwrites a sync's newer access, on a real graph store.

The reindex reads a record's access from the source; a sync then reads the source again, after a grant was taken away, and
writes; then the reindex writes what it read. The sync's write must stand. Without a concurrent sync the reindex writes as before.

Runs on Neo4j and ArangoDB (NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD) and, where PG_CORE_URL names a
migrated database (PG_POOLER=none), on the PostgreSQL graph store.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import os
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import (
    AppStatus,
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.core.constants import ConnectorStateKeys
from app.connectors.core.sync.sync_runner import write_app_status
from app.connectors.services.event_service import EventService
from app.core.org_context import for_org
from app.models.entities import FileRecord, RecordType
from app.models.permission import EntityType, Permission, PermissionType
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "reindex_sync_ordering_it"
APPS, RECORDS, USERS = CollectionNames.APPS.value, CollectionNames.RECORDS.value, CollectionNames.USERS.value
logger = logging.getLogger("reindex-sync-ordering-it")


class _World:
    def __init__(self, graph: IGraphDBProvider, run: str) -> None:
        self.graph = graph
        self.run = run
        self.org = run.rjust(24, "0")
        self.conn = f"conn-{run}"
        self.users: dict[str, str] = {}
        processor = DataSourceEntitiesProcessor(MagicMock(), GraphDataStore(logger, graph), AsyncMock())
        processor.org_id = self.org
        processor.messaging_producer = SimpleNamespace(send_messages=AsyncMock(side_effect=lambda _t, m: [True] * len(m)))
        self.processor = processor

    async def seed(self) -> None:
        await self.graph.batch_upsert_nodes([{
            "_key": self.conn, "orgId": self.org, "name": self.conn, "type": "Drive", "appGroup": "Google Workspace",
            "scope": "team", "isActive": True, "status": AppStatus.IDLE.value, "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
        }], APPS)
        for name in ("alice", "bob"):
            key = f"{name}-{self.run}"
            self.users[name] = f"{key}@example.com"
            await self.graph.batch_upsert_nodes([{
                "_key": key, "email": self.users[name], "orgId": self.org, "isActive": True,
                "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
            }], USERS)

    def record(self) -> FileRecord:
        return FileRecord(
            id=f"rec-{self.run}", org_id=self.org, record_name="plan.txt", record_type=RecordType.FILE,
            external_record_id=f"ext-{self.run}", version=0, origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.GOOGLE_DRIVE, connector_id=self.conn, mime_type="text/plain",
            is_file=True, extension="txt", path="plan.txt", size_in_bytes=10,
        )

    def grants(self, *names: str) -> list[Permission]:
        return [Permission(email=self.users[n], type=PermissionType.READ, entity_type=EntityType.USER) for n in names]

    async def write(self, *names: str) -> None:
        """A connector write of the record with the access the source holds for it, replacing what is stored."""
        await self.processor.on_new_records([(self.record(), self.grants(*names))], replace_permissions=True)

    async def readers(self) -> set[str]:
        users = await self.graph.get_users_with_permission_to_node(f"rec-{self.run}", RECORDS, raise_on_error=True)
        return {u.email for u in users}

    async def reindex(self, connector: object) -> None:
        """The connector-wide reindex of the queued records, as the reindex event runs it."""
        service = SimpleNamespace(graph_provider=self.graph, logger=logger)
        await EventService._reindex_batches(
            service, connector, "drive", self.conn, self.org, None, None, 0, None, [ProgressStatus.QUEUED.value],
        )


async def _postgres() -> tuple[IGraphDBProvider, Any]:
    from app.core.db import DbConfig, KernelDB
    from app.services.graph_db.postgres.provider import PostgresGraphProvider

    db = KernelDB(DbConfig.from_env("connectors"))
    await db.start()
    return PostgresGraphProvider(logger, db), db


@pytest.fixture(params=["neo4j", "arango", "postgres"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_World]:
    async with contextlib.AsyncExitStack() as cleanup:
        if request.param == "postgres":
            if not os.environ.get("PG_CORE_URL"):
                pytest.skip("PG_CORE_URL is not set")
            monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "off")
            graph, db = await _postgres()
            cleanup.push_async_callback(db.close)
        else:
            try:
                graph = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB))
            except Exception as exc:
                backend_unavailable(request.param, exc)
            if (disconnect := getattr(graph, "disconnect", None)) is not None:
                cleanup.push_async_callback(disconnect)
        w = _World(graph, uuid.uuid4().hex[:10])

        async def remove() -> None:
            with contextlib.suppress(Exception), for_org(w.org, job="test"):
                if isinstance(graph, Neo4jProvider):
                    await graph.client.execute_query("MATCH (n) WHERE n.id ENDS WITH $run DETACH DELETE n", parameters={"run": w.run})
                    return
                await graph.delete_nodes_and_edges([f"rec-{w.run}"], RECORDS)
                await graph.delete_nodes([f"{n}-{w.run}" for n in ("alice", "bob")], USERS)
                await graph.delete_nodes([w.conn], APPS)

        cleanup.push_async_callback(remove)
        with for_org(w.org, job="test"):
            await w.seed()
            yield w


async def test_a_sync_that_takes_access_away_during_a_reindex_keeps_it_away(world: _World) -> None:
    await world.write("alice")
    assert await world.readers() == {world.users["alice"]}

    async def sync_after_bob_lost_access_again() -> None:
        with for_org(world.org, job="test"):
            await write_app_status(world.graph, logger, world.conn, AppStatus.SYNCING.value, run_started=True)
            await world.write("alice")
            await write_app_status(world.graph, logger, world.conn, AppStatus.IDLE.value)

    class Connector:
        async def reindex_records(self, records: list) -> None:
            assert [r.id for r in records] == [world.record().id]
            read = world.grants("alice", "bob")  # the source as this reindex reads it, while bob briefly had access
            # A sync of its own (its own task and context) reads the source after bob's access is gone, and writes first.
            await asyncio.create_task(sync_after_bob_lost_access_again(), context=contextvars.Context())
            await world.processor.on_new_records([(world.record(), read)], replace_permissions=True)

    await world.reindex(Connector())

    assert await world.readers() == {world.users["alice"]}


async def test_without_a_sync_the_reindex_writes_what_it_read(world: _World) -> None:
    await world.write("alice")

    class Connector:
        async def reindex_records(self, records: list) -> None:
            assert [r.id for r in records] == [world.record().id]
            await world.processor.on_new_records([(world.record(), world.grants("alice", "bob"))], replace_permissions=True)

    await world.reindex(Connector())

    assert await world.readers() == {world.users["alice"], world.users["bob"]}


async def test_the_app_document_check_holds_only_while_no_sync_started(world: _World) -> None:
    store = GraphDataStore(logger, world.graph)
    # Never synced: the start time is absent, which matches None.
    idle = {ConnectorStateKeys.LAST_SYNC_STARTED_AT: None, "status": AppStatus.IDLE.value}
    async with store.transaction(explicit=True) as tx:
        assert await tx.lock_app_if_unchanged(world.conn, idle)

    await write_app_status(world.graph, logger, world.conn, AppStatus.SYNCING.value, run_started=True)
    await write_app_status(world.graph, logger, world.conn, AppStatus.IDLE.value)
    async with store.transaction(explicit=True) as tx:
        assert not await tx.lock_app_if_unchanged(world.conn, idle)
    app = await world.graph.get_document(world.conn, APPS)
    assert app["status"] == AppStatus.IDLE.value and app[ConnectorStateKeys.LAST_SYNC_STARTED_AT]
