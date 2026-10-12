"""A full sync removes the links of what it did not see only after it finishes.

Against a real Neo4j and a real ArangoDB, through the production path: the
full-sync prep in ``EventService._start_sync_with_lease``, ``run_sync_task`` and
the real ``DataSourceEntitiesProcessor``. Only the connector is scripted.

Requires the graph stack from
deployment/docker-compose/docker-compose.integration.graph-db.yml (NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD; the defaults match it).

The prep used to delete every link of the connector before the sync re-read
anything. A sync cut off partway left whatever it had not reached readable by
nobody, and the connector's stats, which count through those links, dropped to
near zero until the sync caught up.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.core.sync.sync_coordinator import SyncLease
from app.connectors.services.event_service import EventService
from app.models.entities import MessageRecord, RecordGroup, RecordGroupType, RecordType
from app.models.permission import EntityType, Permission, PermissionType
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
logger = logging.getLogger("full-sync-unlink-it")

DAY_MS = 86_400_000
RECORDS = CollectionNames.RECORDS.value
RECORD_GROUPS = CollectionNames.RECORD_GROUPS.value

Script = Callable[["_Sync"], Awaitable[None]]


class _InlineCoordinator:
    """Runs the spawned sync as a real task the test can wait on."""

    shutting_down = False

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None

    async def spawn(self, lease: SyncLease, coro: Awaitable[None]) -> asyncio.Task:
        self.task = asyncio.get_running_loop().create_task(coro)
        lease.task = self.task
        return self.task

    async def end(self, lease: SyncLease) -> bool:
        return True


class _ScriptedConnector:
    """A connector whose run_sync is a script against the real processor."""

    def __init__(self, processor: DataSourceEntitiesProcessor, script: Script, sync: "_Sync",
                 window_start_ms: int | None = None) -> None:
        self.data_entities_processor = processor
        self._script = script
        self._sync = sync
        self._window_start_ms = window_start_ms

    async def run_sync(self) -> None:
        await self._script(self._sync)

    def full_sync_coverage(self) -> object:
        from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage

        return FullSyncCoverage(
            window_start_ms=self._window_start_ms,
            windowed_group_types=frozenset({RecordGroupType.SLACK_THREAD.value}),
        )

    async def cleanup(self) -> None:
        return None


@dataclass
class _Sync:
    """One connector in a real graph, with a channel and the users who read it."""

    graph: object
    processor: DataSourceEntitiesProcessor
    org_id: str
    connector_id: str
    alice: str
    bob: str
    now: int

    # --- what the source holds -------------------------------------------------

    def channel(self, *, readers: tuple[str, ...]) -> tuple[RecordGroup, list[Permission]]:
        group = RecordGroup(
            org_id=self.org_id,
            name="general",
            external_group_id=f"C-{self.connector_id}",
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=self.connector_id,
            group_type=RecordGroupType.SLACK_CHANNEL,
            source_created_at=self.now - 400 * DAY_MS,
            source_updated_at=self.now - 400 * DAY_MS,
        )
        return group, [self.grant(user) for user in readers]

    def thread(self, name: str, *, created: int) -> tuple[RecordGroup, list[Permission]]:
        group = RecordGroup(
            org_id=self.org_id,
            name=name,
            external_group_id=f"thread-{name}-{self.connector_id}",
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=self.connector_id,
            group_type=RecordGroupType.SLACK_THREAD,
            parent_external_group_id=f"C-{self.connector_id}",
            inherit_permissions=True,
            source_created_at=created,
            source_updated_at=created,
        )
        return group, []

    def message(self, name: str, *, created: int | None = None, thread: str | None = None,
                readers: tuple[str, ...] = (), inherit: bool = True) -> tuple[MessageRecord, list[Permission]]:
        created = created if created is not None else self.now - DAY_MS
        record = MessageRecord(
            org_id=self.org_id,
            record_name=name,
            record_type=RecordType.MESSAGE,
            external_record_id=f"{name}-{self.connector_id}",
            external_revision_id="1",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=self.connector_id,
            external_record_group_id=(
                f"thread-{thread}-{self.connector_id}" if thread else f"C-{self.connector_id}"
            ),
            record_group_type=RecordGroupType.SLACK_THREAD if thread else RecordGroupType.SLACK_CHANNEL,
            indexing_status=ProgressStatus.COMPLETED.value,
            inherit_permissions=inherit,
            created_at=created,
            updated_at=created,
            source_created_at=created,
            source_updated_at=created,
        )
        return record, [self.grant(user) for user in readers]

    def grant(self, user_key: str) -> Permission:
        return Permission(email=f"{user_key}@example.com", entity_type=EntityType.USER, type=PermissionType.READ)

    # --- what the graph says ---------------------------------------------------

    async def record_id(self, name: str) -> str:
        record = await self.graph.get_record_by_external_id(self.connector_id, f"{name}-{self.connector_id}")
        assert record is not None, name
        return record.id

    async def group_id(self, name: str) -> str:
        group = await self.graph.get_record_group_by_external_id(
            self.connector_id, f"thread-{name}-{self.connector_id}"
        )
        assert group is not None, name
        return group.id

    async def links(self, collection: str, node_id: str) -> dict[str, int]:
        node = f"{collection}/{node_id}"
        return {
            "belongs_to": len(await self.graph.get_edges_from_node(node, CollectionNames.BELONGS_TO.value) or []),
            "inherits": len(
                await self.graph.get_edges_from_node(node, CollectionNames.INHERIT_PERMISSIONS.value) or []
            ),
            "grants": len(await self.graph.get_edges_to_node(node, CollectionNames.PERMISSION.value) or []),
        }

    async def can_read(self, user_key: str, name: str) -> bool:
        result = await self.graph._check_record_permissions(await self.record_id(name), user_key)
        return bool((result or {}).get("permission"))

    async def stats_total(self) -> int:
        result = await self.graph.get_connector_stats(self.org_id, self.connector_id)
        assert result["success"], result
        return result["data"]["stats"]["total"]

    # --- running a full sync ---------------------------------------------------

    async def full_sync(self, script: Script, *, window_start_ms: int | None = None
                        ) -> tuple[asyncio.Task, SyncLease]:
        """Start a full sync the way the sync event does, and hand back its task."""
        service = EventService(logger, MagicMock(), self.graph)
        connector = _ScriptedConnector(self.processor, script, self, window_start_ms)
        coordinator = _InlineCoordinator()
        lease = SyncLease(self.connector_id, f"tok-{uuid.uuid4().hex[:6]}", 1)
        with patch.object(service, "_ensure_connector", AsyncMock(return_value=connector)):
            ack, handed_off = await service._start_sync_with_lease(
                "slack",
                {"orgId": self.org_id, "connectorId": self.connector_id, "fullSync": True},
                lease,
                coordinator,
            )
        assert ack and handed_off and coordinator.task is not None
        return coordinator.task, lease


async def _seed(graph: object) -> _Sync:
    run = uuid.uuid4().hex[:10]
    now = get_epoch_timestamp_in_ms()
    org_id, connector_id = f"org-{run}", f"slack-{run}"
    stamps = {"createdAtTimestamp": now, "updatedAtTimestamp": now}
    assert await graph.batch_upsert_nodes(
        [{"id": connector_id, "name": "Slack", "type": Connectors.SLACK_WORKSPACE.value,
          "appGroup": "Slack", "scope": "team", "isActive": True, **stamps}],
        collection=CollectionNames.APPS.value,
    )
    alice, bob = f"alice-{run}", f"bob-{run}"
    assert await graph.batch_upsert_nodes(
        [{"id": key, "userId": key, "orgId": org_id, "email": f"{key}@example.com", "isActive": True, **stamps}
         for key in (alice, bob)],
        collection=CollectionNames.USERS.value,
    )
    processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
    processor.org_id = org_id
    producer = MagicMock()
    producer.send_messages = AsyncMock(side_effect=lambda topic, messages: [True] * len(messages))
    processor.messaging_producer = producer
    return _Sync(graph, processor, org_id, connector_id, alice, bob, now)


async def _cleanup(sync: _Sync) -> None:
    with contextlib.suppress(Exception):
        records = await sync.graph.get_nodes_by_filters(RECORDS, {"connectorId": sync.connector_id}) or []
        groups = await sync.graph.get_nodes_by_filters(RECORD_GROUPS, {"connectorId": sync.connector_id}) or []
        await sync.graph.delete_nodes_and_edges([r.get("_key") or r.get("id") for r in records], RECORDS)
        await sync.graph.delete_nodes_and_edges([g.get("_key") or g.get("id") for g in groups], RECORD_GROUPS)
        await sync.graph.delete_nodes_and_edges([sync.alice, sync.bob], CollectionNames.USERS.value)
        await sync.graph.delete_nodes_and_edges([sync.connector_id], CollectionNames.APPS.value)


@pytest.fixture(params=["neo4j", "arango"])
async def sync(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Sync]:
    try:
        graph = await (
            connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
            else connect_arango(logger, "full_sync_unlink_it")
        )
    except Exception as exc:
        backend_unavailable(request.param, exc)
    seeded = await _seed(graph)
    try:
        yield seeded
    finally:
        await _cleanup(seeded)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            await disconnect()


async def _first_sync(sync: _Sync, *, inherit: bool = True) -> None:
    """The state an earlier sync left: three messages, both users reading all of them."""
    await sync.processor.on_new_record_groups([sync.channel(readers=(sync.alice, sync.bob))])
    await sync.processor.on_new_records(
        [sync.message(name, readers=(sync.alice, sync.bob), inherit=inherit) for name in ("m1", "m2", "m3")]
    )


async def test_a_full_sync_cut_short_leaves_what_it_had_not_reached_linked(sync: _Sync) -> None:
    await _first_sync(sync)
    reached_m1 = asyncio.Event()

    async def script(s: _Sync) -> None:
        await s.processor.on_new_record_groups([s.channel(readers=(s.alice, s.bob))])
        await s.processor.on_new_records([s.message("m1", readers=(s.alice, s.bob))])
        reached_m1.set()
        await asyncio.Event().wait()  # until the service restarts or someone stops it

    task, lease = await sync.full_sync(script)
    await asyncio.wait_for(reached_m1.wait(), timeout=60)
    lease.stop_requested.set()
    await asyncio.wait_for(task, timeout=60)

    for name in ("m2", "m3"):
        links = await sync.links(RECORDS, await sync.record_id(name))
        assert links["belongs_to"] == 1 and links["inherits"] == 1, (name, links)
        assert links["grants"] == 2, (name, links)
        assert await sync.can_read(sync.alice, name), f"alice lost {name} to an interrupted sync"
    assert await sync.stats_total() == 3


async def test_stats_do_not_drop_while_a_full_sync_runs(sync: _Sync) -> None:
    await _first_sync(sync)
    assert await sync.stats_total() == 3
    started, carry_on = asyncio.Event(), asyncio.Event()

    async def script(s: _Sync) -> None:
        started.set()
        await carry_on.wait()
        await s.processor.on_new_record_groups([s.channel(readers=(s.alice, s.bob))])
        await s.processor.on_new_records([s.message(n, readers=(s.alice, s.bob)) for n in ("m1", "m2", "m3")])

    task, _ = await sync.full_sync(script)
    await asyncio.wait_for(started.wait(), timeout=60)
    assert await sync.stats_total() == 3, "the stats counted none of the records while the sync ran"
    carry_on.set()
    await asyncio.wait_for(task, timeout=60)
    assert await sync.stats_total() == 3


async def test_a_completed_full_sync_unlinks_what_is_gone_and_keeps_what_is_outside_its_window(
    sync: _Sync,
) -> None:
    window_start = sync.now - 30 * DAY_MS
    old = sync.now - 90 * DAY_MS
    await sync.processor.on_new_record_groups(
        [sync.channel(readers=(sync.alice,)), sync.thread("old-thread", created=old)]
    )
    await sync.processor.on_new_records([
        sync.message("kept", created=sync.now - DAY_MS),
        sync.message("deleted-at-source", created=sync.now - DAY_MS),
        # Older than the window, and shared with bob directly.
        sync.message("old", created=old, readers=(sync.bob,)),
        # A recent reply in a thread that started before the window: Slack reads
        # it only through its thread, which the sync no longer reads.
        sync.message("late-reply", created=sync.now - DAY_MS, thread="old-thread"),
    ])

    async def script(s: _Sync) -> None:
        await s.processor.on_new_record_groups([s.channel(readers=(s.alice,))])
        await s.processor.on_new_records([s.message("kept", created=s.now - DAY_MS)])

    task, _ = await sync.full_sync(script, window_start_ms=window_start)
    await asyncio.wait_for(task, timeout=60)

    assert await sync.links(RECORDS, await sync.record_id("deleted-at-source")) == {
        "belongs_to": 0, "inherits": 0, "grants": 0,
    }
    assert await sync.can_read(sync.alice, "kept")

    old_links = await sync.links(RECORDS, await sync.record_id("old"))
    assert old_links["belongs_to"] == 1 and old_links["inherits"] == 1, old_links
    assert await sync.can_read(sync.alice, "old"), "outside the window, so still readable through its channel"
    assert old_links["grants"] == 0, "a grant the sync could not check is not kept"
    assert not await sync.can_read(sync.bob, "old")

    # Still under its channel (and, on ArangoDB, the org: an edge to a document
    # the test never wrote).
    thread_links = await sync.links(RECORD_GROUPS, await sync.group_id("old-thread"))
    assert thread_links["belongs_to"] >= 1 and thread_links["inherits"] == 1, thread_links
    reply_links = await sync.links(RECORDS, await sync.record_id("late-reply"))
    assert reply_links["belongs_to"] == 1 and reply_links["inherits"] == 1, reply_links
    assert await sync.can_read(sync.alice, "late-reply")


async def test_a_revoked_grant_goes_when_the_full_sync_rereads_the_record(sync: _Sync) -> None:
    # Record-level grants only, so the record's own permission edges decide.
    await _first_sync(sync, inherit=False)
    assert await sync.can_read(sync.bob, "m1")
    reread_m1, carry_on = asyncio.Event(), asyncio.Event()

    async def script(s: _Sync) -> None:
        await s.processor.on_new_record_groups([s.channel(readers=(s.alice, s.bob))])
        # Bob lost m1 at the source. One record can be read twice in a sync, each
        # time with part of its grants: the second read adds to the first.
        await s.processor.on_new_records([s.message("m1", readers=(s.alice,), inherit=False)])
        await s.processor.on_new_records([s.message("m1", readers=(s.alice,), inherit=False)])
        reread_m1.set()
        await carry_on.wait()
        await s.processor.on_new_records([
            s.message("m2", readers=(s.alice,), inherit=False),
            s.message("m2", readers=(s.bob,), inherit=False),
            s.message("m3", readers=(s.alice, s.bob), inherit=False),
        ])

    task, _ = await sync.full_sync(script)
    await asyncio.wait_for(reread_m1.wait(), timeout=60)
    assert not await sync.can_read(sync.bob, "m1"), "bob kept m1 after the sync re-read it"
    assert await sync.can_read(sync.alice, "m1")
    carry_on.set()
    await asyncio.wait_for(task, timeout=60)

    assert not await sync.can_read(sync.bob, "m1")
    assert await sync.can_read(sync.alice, "m1")
    assert await sync.can_read(sync.alice, "m2") and await sync.can_read(sync.bob, "m2")
    assert await sync.can_read(sync.bob, "m3")
