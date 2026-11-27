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
from app.connectors.core.base.connector import connector_service
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.core.constants import ConnectorStateKeys
from app.connectors.core.sync import sync_dispatcher
from app.connectors.core.sync.sync_dispatcher import SubmitResult, SyncSpec
from app.connectors.core.sync.sync_runner import run_sync_task, write_app_status
from app.connectors.services.event_service import EventService
from app.connectors.sources.dropbox.connector import DropboxConnector
from app.core.org_context import for_org
from app.models.entities import AppUserGroup, FileRecord, RecordType
from app.models.permission import EntityType, Permission, PermissionType
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.messaging.kafka.handlers import entity as entity_handler
from app.services.messaging.kafka.handlers.entity import EntityEventService
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


class _Connector:
    """A connector whose indexing filters exclude nothing: these tests are about sync ordering."""

    async def records_excluded_by_indexing_filters(self, records: list) -> set[str]:
        return set()


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

    class Connector(_Connector):
        async def reindex_records(self, records: list) -> None:
            assert [r.id for r in records] == [world.record().id]
            read = world.grants("alice", "bob")  # the source as this reindex reads it, while bob briefly had access
            # A sync of its own (its own task and context) reads the source after bob's access is gone, and writes first.
            await asyncio.create_task(sync_after_bob_lost_access_again(), context=contextvars.Context())
            await world.processor.on_new_records([(world.record(), read)], replace_permissions=True)

    await world.reindex(Connector())

    assert await world.readers() == {world.users["alice"]}


async def test_a_sync_a_webhook_starts_during_a_reindex_keeps_the_access_it_took_away(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The webhook's sync goes through the sync start path (here run directly, as the sync-events consumer runs it), so
    the reindex sees it, as it sees a scheduled one."""
    await world.write("alice")
    synced = asyncio.Event()

    async def run_sync() -> None:
        with for_org(world.org, job="test"):
            await world.write("alice")  # the source after bob lost access again
        synced.set()

    webhook = object.__new__(DropboxConnector)
    webhook.logger, webhook.connector_id, webhook.connector_name = logger, world.conn, Connectors.DROPBOX
    webhook.data_entities_processor, webhook.data_store_provider = world.processor, world.processor.data_store_provider
    webhook.last_sync_summary = webhook.last_action_items = None
    webhook.run_sync = run_sync

    async def submit(spec: SyncSpec) -> SubmitResult:
        assert (spec.connector_id, spec.connector_name, spec.org_id) == (world.conn, Connectors.DROPBOX.value, world.org)
        with for_org(world.org, job="test"):
            await run_sync_task(webhook, world.conn, world.graph, logger)
        return SubmitResult.ACCEPTED

    monkeypatch.setattr(sync_dispatcher._Registry, "dispatcher", SimpleNamespace(submit=submit), raising=False)

    class Connector(_Connector):
        async def reindex_records(self, records: list) -> None:
            assert [r.id for r in records] == [world.record().id]
            read = world.grants("alice", "bob")
            # Delivered by the webhook route, outside this reindex.
            contextvars.Context().run(webhook.handle_webhook_notification, {})
            await synced.wait()
            # The whole run, through its end status, and any failure of it.
            await asyncio.gather(*getattr(connector_service, "_SYNC_REQUESTS", ()))
            app = await world.graph.get_document(world.conn, APPS)
            assert app["status"] == AppStatus.IDLE.value and app.get(ConnectorStateKeys.LAST_SYNC_STARTED_AT)
            await world.processor.on_new_records([(world.record(), read)], replace_permissions=True)

    await world.reindex(Connector())

    assert await world.readers() == {world.users["alice"]}


async def test_a_user_link_pass_in_another_process_during_a_reindex_keeps_the_grant_it_moved(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bob's address was hidden, so the record was shared with a stand-in group for his account; a user-link pass learns his
    address and moves the group's grant onto him. A reindex that read the source before the pass must not take it away."""
    group = AppUserGroup(
        app_name=Connectors.CONFLUENCE, connector_id=world.conn, source_user_group_id=f"acct-{world.run}",
        name=f"[Pseudo-User] acct-{world.run}", org_id=world.org,
    )
    await world.processor.on_new_user_groups([(group, [])])
    stand_in = Permission(external_id=group.source_user_group_id, type=PermissionType.READ, entity_type=EntityType.GROUP)
    await world.processor.on_new_records([(world.record(), [*world.grants("alice"), stand_in])], replace_permissions=True)
    assert await world.readers() == {world.users["alice"]}

    async def link_new_users() -> None:
        await world.processor.migrate_group_to_user_by_external_id(group.source_user_group_id, world.users["bob"], world.conn)

    # As from another process: the reindex sees neither the pass's sync lock (released before it writes) nor its count of started syncs.
    monkeypatch.setattr(connector_service, "syncs_started", lambda _connector_id: 0)
    linking = SimpleNamespace(
        app_container=SimpleNamespace(connectors_map={world.conn: SimpleNamespace(link_new_users=link_new_users)}),
        graph_provider=world.graph, logger=logger,
    )

    async def link_elsewhere() -> bool:
        with for_org(world.org, job="test"):
            return await EntityEventService._link_connector(linking, world.conn)

    class Connector(_Connector):
        data_entities_processor = SimpleNamespace(reindex_existing_records=AsyncMock())  # the superseded batch is re-queued as stored

        async def reindex_records(self, records: list) -> None:
            read = [*world.grants("alice"), stand_in]  # the source as this reindex read it, before the pass
            assert await asyncio.create_task(link_elsewhere(), context=contextvars.Context())
            assert await world.readers() == {world.users["alice"], world.users["bob"]}
            await world.processor.on_new_records([(world.record(), read)], replace_permissions=True)

    await world.reindex(Connector())

    assert await world.readers() == {world.users["alice"], world.users["bob"]}


@pytest.mark.parametrize("meanwhile", ["a sync elsewhere started and ended", "an overlapping pass elsewhere ended"])
async def test_a_reindex_that_began_during_a_user_link_pass_in_another_process_keeps_the_moved_grant(
    world: _World, monkeypatch: pytest.MonkeyPatch, meanwhile: str,
) -> None:
    """Before the reindex begins, a sync in a third process starts after the pass did and ends, or another pass of the
    connector ends; the reindex then reads the source, and writes while the pass is still running, after it moved the grant."""
    group = AppUserGroup(
        app_name=Connectors.CONFLUENCE, connector_id=world.conn, source_user_group_id=f"acct-{world.run}",
        name=f"[Pseudo-User] acct-{world.run}", org_id=world.org,
    )
    await world.processor.on_new_user_groups([(group, [])])
    stand_in = Permission(external_id=group.source_user_group_id, type=PermissionType.READ, entity_type=EntityType.GROUP)
    await world.processor.on_new_records([(world.record(), [*world.grants("alice"), stand_in])], replace_permissions=True)

    # As from other processes: neither the reindex nor another pass sees a pass's sync lock, nor the reindex its count of started syncs.
    monkeypatch.setattr(connector_service, "syncs_started", lambda _connector_id: 0)
    for module in (connector_service, entity_handler):
        monkeypatch.setattr(module, "connector_sync_lock", lambda _connector_id: asyncio.Lock())
    pass_started, reindex_read, moved, reindex_wrote = (asyncio.Event() for _ in range(4))

    async def link_new_users() -> None:
        pass_started.set()
        await reindex_read.wait()
        await world.processor.migrate_group_to_user_by_external_id(group.source_user_group_id, world.users["bob"], world.conn)
        moved.set()
        await reindex_wrote.wait()

    async def link_elsewhere(link: object) -> bool:
        linking = SimpleNamespace(
            app_container=SimpleNamespace(connectors_map={world.conn: SimpleNamespace(link_new_users=link)}),
            graph_provider=world.graph, logger=logger,
        )
        with for_org(world.org, job="test"):
            return await EntityEventService._link_connector(linking, world.conn)

    link_pass = asyncio.create_task(link_elsewhere(link_new_users), context=contextvars.Context())
    await pass_started.wait()
    with for_org(world.org, job="test"):
        if meanwhile == "a sync elsewhere started and ended":
            await asyncio.sleep(0.01)      # its start time is after the pass's
            await write_app_status(world.graph, logger, world.conn, AppStatus.SYNCING.value, run_started=True, raise_on_error=True)
            await world.graph.batch_upsert_nodes([{
                "id": world.conn, "status": AppStatus.IDLE.value,
                ConnectorStateKeys.LAST_SYNC_FINISHED_AT: (await world.graph.get_document(world.conn, APPS))["updatedAtTimestamp"] + 1,
            }], APPS)
        else:
            async def no_users() -> None:
                return None

            assert await asyncio.create_task(link_elsewhere(no_users), context=contextvars.Context())

    class Connector(_Connector):
        data_entities_processor = SimpleNamespace(reindex_existing_records=AsyncMock())  # the superseded batch is re-queued as stored

        async def reindex_records(self, records: list) -> None:
            read = [*world.grants("alice"), stand_in]  # read while the pass runs, before it moved the grant
            reindex_read.set()
            await moved.wait()
            try:
                await world.processor.on_new_records([(world.record(), read)], replace_permissions=True)
            finally:
                reindex_wrote.set()

    await world.reindex(Connector())
    assert await link_pass

    assert await world.readers() == {world.users["alice"], world.users["bob"]}


async def test_without_a_sync_the_reindex_writes_what_it_read(world: _World) -> None:
    await world.write("alice")

    class Connector(_Connector):
        async def reindex_records(self, records: list) -> None:
            assert [r.id for r in records] == [world.record().id]
            await world.processor.on_new_records([(world.record(), world.grants("alice", "bob"))], replace_permissions=True)

    await world.reindex(Connector())

    assert await world.readers() == {world.users["alice"], world.users["bob"]}


async def test_a_sync_queued_for_a_slot_does_not_refuse_the_reindex_write(world: _World) -> None:
    """A sync waiting for a slot has read nothing yet; its start moves the start time when it runs."""
    await world.write("alice")

    class Connector(_Connector):
        async def reindex_records(self, records: list) -> None:
            await write_app_status(world.graph, logger, world.conn, AppStatus.QUEUED.value)
            await world.processor.on_new_records([(world.record(), world.grants("alice", "bob"))], replace_permissions=True)

    await world.reindex(Connector())

    assert await world.readers() == {world.users["alice"], world.users["bob"]}


async def test_a_sync_whose_start_was_not_recorded_is_seen_by_its_end(world: _World) -> None:
    """Its start status write failed, in another process; the end it recorded still refuses the older write."""
    await world.write("alice")

    async def sync_elsewhere() -> None:
        with for_org(world.org, job="test"):
            await world.write("alice")
            await world.graph.update_node(world.conn, APPS, {
                "status": AppStatus.IDLE.value, ConnectorStateKeys.LAST_SYNC_FINISHED_AT: 7, "updatedAtTimestamp": 7,
            })

    class Connector(_Connector):
        async def reindex_records(self, records: list) -> None:
            read = world.grants("alice", "bob")
            await asyncio.create_task(sync_elsewhere(), context=contextvars.Context())
            await world.processor.on_new_records([(world.record(), read)], replace_permissions=True)

    await world.reindex(Connector())

    assert await world.readers() == {world.users["alice"]}


async def test_the_app_document_check_holds_only_while_no_sync_started(world: _World) -> None:
    store = GraphDataStore(logger, world.graph)
    # Never synced: the start and end times are absent, which matches None.
    idle = {ConnectorStateKeys.LAST_SYNC_STARTED_AT: None, ConnectorStateKeys.LAST_SYNC_FINISHED_AT: None}
    async with store.transaction(explicit=True) as tx:
        assert await tx.lock_app_if_unchanged(world.conn, idle)

    await write_app_status(world.graph, logger, world.conn, AppStatus.SYNCING.value, run_started=True)
    await write_app_status(world.graph, logger, world.conn, AppStatus.IDLE.value)
    async with store.transaction(explicit=True) as tx:
        assert not await tx.lock_app_if_unchanged(world.conn, idle)
    app = await world.graph.get_document(world.conn, APPS)
    assert app["status"] == AppStatus.IDLE.value and app[ConnectorStateKeys.LAST_SYNC_STARTED_AT]
