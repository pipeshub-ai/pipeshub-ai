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
    RecordRelations,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.core.sync.sync_coordinator import SyncLease
from app.connectors.services.event_service import EventService
from app.models.entities import (
    AppUserGroup,
    MessageRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
    RelatedExternalRecord,
    TicketRecord,
)
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

    def ticket(self, name: str, *, links_to: str) -> tuple[TicketRecord, list[Permission]]:
        created = self.now - DAY_MS
        record = TicketRecord(
            org_id=self.org_id,
            record_name=name,
            record_type=RecordType.TICKET,
            external_record_id=f"{name}-{self.connector_id}",
            external_revision_id="1",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id=self.connector_id,
            external_record_group_id=f"C-{self.connector_id}",
            record_group_type=RecordGroupType.SLACK_CHANNEL,
            indexing_status=ProgressStatus.COMPLETED.value,
            related_external_records=[RelatedExternalRecord(
                external_record_id=f"{links_to}-{self.connector_id}",
                record_type=RecordType.TICKET,
                relation_type=RecordRelations.BLOCKS,
            )],
            created_at=created,
            updated_at=created,
            source_created_at=created,
            source_updated_at=created,
        )
        return record, [self.grant(self.alice)]

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
        user_groups = await sync.graph.get_nodes_by_filters(
            CollectionNames.GROUPS.value, {"connectorId": sync.connector_id}
        ) or []
        await sync.graph.delete_nodes_and_edges(
            [g.get("_key") or g.get("id") for g in user_groups], CollectionNames.GROUPS.value
        )
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


async def test_a_rename_in_a_full_sync_leaves_the_grants_alone(sync: _Sync) -> None:
    """A metadata or content update carries no permissions (Drive, Dropbox,
    OneDrive and SharePoint call it for a renamed or changed file). Being the
    only write of a record in a full sync, it must not replace the grants."""
    await _first_sync(sync, inherit=False)

    async def script(s: _Sync) -> None:
        await s.processor.on_new_record_groups([s.channel(readers=(s.alice, s.bob))])
        renamed, _ = s.message("m1", inherit=False)
        renamed.record_name = "m1 (renamed)"
        renamed.external_revision_id = "2"
        await s.processor.on_record_metadata_update(renamed)
        await s.processor.on_new_records(
            [s.message(name, readers=(s.alice, s.bob), inherit=False) for name in ("m2", "m3")]
        )

    task, _ = await sync.full_sync(script)
    await asyncio.wait_for(task, timeout=60)

    assert (await sync.links(RECORDS, await sync.record_id("m1")))["grants"] == 2
    assert await sync.can_read(sync.alice, "m1") and await sync.can_read(sync.bob, "m1")


async def test_a_link_to_a_placeholder_survives_the_unlink(sync: _Sync) -> None:
    """A linked issue the sync never reads itself exists only as a placeholder."""
    await sync.processor.on_new_record_groups([sync.channel(readers=(sync.alice,))])
    await sync.processor.on_new_records([sync.ticket("t1", links_to="t-elsewhere")])

    async def links_out() -> int:
        edges = await sync.graph.get_edges_from_node(
            f"{RECORDS}/{await sync.record_id('t1')}", CollectionNames.RECORD_RELATIONS.value
        )
        return len(edges or [])

    assert await links_out() == 1

    async def script(s: _Sync) -> None:
        await s.processor.on_new_record_groups([s.channel(readers=(s.alice,))])
        await s.processor.on_new_records([s.ticket("t1", links_to="t-elsewhere")])

    task, _ = await sync.full_sync(script)
    await asyncio.wait_for(task, timeout=60)

    assert await links_out() == 1, "the link to the placeholder was unlinked"


async def test_a_channel_whose_members_cannot_be_read_fails_closed(sync: _Sync) -> None:
    """Slack answers not_in_channel or missing_scope on every sync for such a
    channel. It is written with no grants: nobody reads it until its members
    can be read again, so a removed member never keeps access unseen. It is
    still seen, so the sync's unlink does not take its links."""
    await _first_sync(sync)

    async def script(s: _Sync) -> None:
        await s.processor.on_new_record_groups([s.channel(readers=())])
        await s.processor.on_new_records([s.message(n) for n in ("m1", "m2", "m3")])

    task, _ = await sync.full_sync(script)
    await asyncio.wait_for(task, timeout=60)

    for name in ("m1", "m2", "m3"):
        assert not await sync.can_read(sync.alice, name), name
        assert not await sync.can_read(sync.bob, name), name
        links = await sync.links(RECORDS, await sync.record_id(name))
        assert links["belongs_to"] == 1 and links["inherits"] == 1, (name, links)


async def _department(sync: _Sync) -> tuple[Callable[[], AppUserGroup], Callable[[], Awaitable[set[str]]]]:
    """A ServiceNow-style department with alice and bob as members, written as
    the connector does: the group without members, then one membership per user."""
    users = CollectionNames.USERS.value
    await sync.graph.batch_create_edges(
        [{"from_id": key, "from_collection": users, "to_id": sync.connector_id,
          "to_collection": CollectionNames.APPS.value, "sourceUserId": f"src-{key}",
          "syncState": "NOT_STARTED", "lastSyncUpdate": sync.now}
         for key in (sync.alice, sync.bob)],
        collection=CollectionNames.USER_APP_RELATION.value,
    )

    def department() -> AppUserGroup:
        # One id across writes, so both backends upsert the same node.
        return AppUserGroup(id=f"dept-{sync.connector_id}", app_name=Connectors.SERVICENOW,
                            connector_id=sync.connector_id, source_user_group_id="dept-1",
                            name="Sales", org_id=sync.org_id)

    async def members() -> set[str]:
        group = await sync.graph.get_user_group_by_external_id(sync.connector_id, "dept-1")
        edges = await sync.graph.get_edges_to_node(
            f"{CollectionNames.GROUPS.value}/{group.id}", CollectionNames.PERMISSION.value
        )
        return {(e.get("_from") or e.get("from_id") or "").split("/")[-1] for e in edges or []}

    await sync.processor.batch_upsert_user_groups([department()])
    for key in (sync.alice, sync.bob):
        assert await sync.processor.create_user_group_membership(f"src-{key}", "dept-1", sync.connector_id)
    assert await members() == {sync.alice, sync.bob}
    return department, members


async def test_a_member_removed_from_an_organisational_group_is_gone_after_a_full_sync(sync: _Sync) -> None:
    department, members = await _department(sync)

    async def script(s: _Sync) -> None:
        await s.processor.batch_upsert_user_groups([department()])
        # Bob left the department at the source.
        await s.processor.create_user_group_membership(f"src-{s.alice}", "dept-1", s.connector_id)

    task, _ = await sync.full_sync(script)
    await asyncio.wait_for(task, timeout=60)

    assert await members() == {sync.alice}


async def test_a_user_page_that_fails_leaves_the_members_not_yet_read(sync: _Sync) -> None:
    """ServiceNow reads users 100 to a page and adds memberships page by page."""
    department, members = await _department(sync)

    async def script(s: _Sync) -> None:
        await s.processor.batch_upsert_user_groups([department()])
        await s.processor.create_user_group_membership(f"src-{s.alice}", "dept-1", s.connector_id)
        raise RuntimeError("ServiceNow API error on the next user page")

    task, _ = await sync.full_sync(script)
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(task, timeout=60)

    assert await members() == {sync.alice, sync.bob}


async def test_a_sync_stopped_after_the_first_member_keeps_the_rest(sync: _Sync) -> None:
    department, members = await _department(sync)
    first_written = asyncio.Event()

    async def script(s: _Sync) -> None:
        await s.processor.batch_upsert_user_groups([department()])
        await s.processor.create_user_group_membership(f"src-{s.alice}", "dept-1", s.connector_id)
        first_written.set()
        await asyncio.Event().wait()

    task, lease = await sync.full_sync(script)
    await asyncio.wait_for(first_written.wait(), timeout=60)
    lease.stop_requested.set()
    await asyncio.wait_for(task, timeout=60)

    assert await members() == {sync.alice, sync.bob}


async def test_a_membership_that_does_not_land_keeps_the_groups_members(sync: _Sync) -> None:
    """The store answers False (here a user it cannot find); the list this sync
    read is not complete, so the group keeps the members it has."""
    department, members = await _department(sync)

    async def script(s: _Sync) -> None:
        await s.processor.batch_upsert_user_groups([department()])
        await s.processor.create_user_group_membership(f"src-{s.alice}", "dept-1", s.connector_id)
        assert not await s.processor.create_user_group_membership("src-unknown", "dept-1", s.connector_id)

    task, _ = await sync.full_sync(script)
    await asyncio.wait_for(task, timeout=60)

    assert await members() == {sync.alice, sync.bob}


async def test_a_moved_record_gets_its_fetched_grants_in_a_full_sync(sync: _Sync) -> None:
    """A move that carries the record's complete permission list (Local FS)
    replaces its grants; one that carries none (GitHub, GitLab) only adds."""
    await _first_sync(sync, inherit=False)

    async def script(s: _Sync) -> None:
        await s.processor.on_new_record_groups([s.channel(readers=(s.alice, s.bob))])
        moved, _ = s.message("m1-renamed", inherit=False)
        await s.processor.on_records_moved(
            [(f"m1-{s.connector_id}", moved, [s.grant(s.alice)])], permissions_fetched=True
        )
        renamed, _ = s.message("m2-renamed", inherit=False)
        await s.processor.on_records_moved([(f"m2-{s.connector_id}", renamed, [])])
        await s.processor.on_new_records([s.message("m3", readers=(s.alice, s.bob), inherit=False)])

    task, _ = await sync.full_sync(script)
    await asyncio.wait_for(task, timeout=60)

    assert await sync.can_read(sync.alice, "m1-renamed")
    assert not await sync.can_read(sync.bob, "m1-renamed"), "bob's grant was revoked at the source"
    assert await sync.can_read(sync.alice, "m2-renamed") and await sync.can_read(sync.bob, "m2-renamed")


async def _department_grant(sync: _Sync) -> set[str]:
    """Who holds a grant on the channel (the department is shared it, as a
    knowledge base shares articles with ServiceNow's organisational groups)."""
    channel = await sync.graph.get_record_group_by_external_id(sync.connector_id, f"C-{sync.connector_id}")
    edges = await sync.graph.get_edges_to_node(
        f"{RECORD_GROUPS}/{channel.id}", CollectionNames.PERMISSION.value
    )
    return {(e.get("_from") or e.get("from_id") or "").split("/")[-1] for e in edges or []}


def _channel_shared_with_department(s: _Sync) -> tuple[RecordGroup, list[Permission]]:
    channel, _ = s.channel(readers=())
    return channel, [Permission(external_id="dept-1", entity_type=EntityType.GROUP, type=PermissionType.READ)]


async def test_a_department_that_lost_its_last_member_keeps_its_grants(sync: _Sync) -> None:
    """No membership call names it this sync; it must not be unlinked as unseen,
    which would also take the grants written to it this sync."""
    department, members = await _department(sync)

    async def script(s: _Sync) -> None:
        await s.processor.batch_upsert_user_groups([department()])
        await s.processor.on_new_record_groups([_channel_shared_with_department(s)])

    task, _ = await sync.full_sync(script)
    await asyncio.wait_for(task, timeout=60)

    group = await sync.graph.get_user_group_by_external_id(sync.connector_id, "dept-1")
    assert group.id in await _department_grant(sync)
    assert await members() == set(), "nobody is in it any more"


async def test_a_department_that_never_had_users_keeps_its_grants(sync: _Sync) -> None:
    def department() -> AppUserGroup:
        return AppUserGroup(id=f"dept-{sync.connector_id}", app_name=Connectors.SERVICENOW,
                            connector_id=sync.connector_id, source_user_group_id="dept-1",
                            name="Sales", org_id=sync.org_id)

    async def script(s: _Sync) -> None:
        await s.processor.batch_upsert_user_groups([department()])
        await s.processor.on_new_record_groups([_channel_shared_with_department(s)])

    for _ in range(2):  # the first sync writes it, the second must not unlink it
        task, _lease = await sync.full_sync(script)
        await asyncio.wait_for(task, timeout=60)

    group = await sync.graph.get_user_group_by_external_id(sync.connector_id, "dept-1")
    assert group.id in await _department_grant(sync)


async def _department_copies(sync: _Sync) -> list[str]:
    docs = await sync.graph.get_nodes_by_filters(
        CollectionNames.GROUPS.value, {"connectorId": sync.connector_id, "externalGroupId": "dept-1"}
    )
    return sorted(d.get("_key") or d.get("id") for d in docs or [])


async def _members_of(sync: _Sync, group_id: str) -> set[str]:
    edges = await sync.graph.get_edges_to_node(
        f"{CollectionNames.GROUPS.value}/{group_id}", CollectionNames.PERMISSION.value
    )
    return {(e.get("_from") or e.get("from_id") or "").split("/")[-1] for e in edges or []}


async def test_a_department_written_with_a_fresh_id_each_sync_keeps_its_grant(sync: _Sync) -> None:
    """As the ServiceNow connector does without #4126: a new id every sync, so
    each sync may add a copy of the department, and the grant and the members
    may land on different copies."""
    users = CollectionNames.USERS.value
    await sync.graph.batch_create_edges(
        [{"from_id": sync.alice, "from_collection": users, "to_id": sync.connector_id,
          "to_collection": CollectionNames.APPS.value, "sourceUserId": f"src-{sync.alice}",
          "syncState": "NOT_STARTED", "lastSyncUpdate": sync.now}],
        collection=CollectionNames.USER_APP_RELATION.value,
    )

    async def script(s: _Sync) -> None:
        await s.processor.batch_upsert_user_groups([AppUserGroup(
            app_name=Connectors.SERVICENOW, connector_id=s.connector_id,
            source_user_group_id="dept-1", name="Sales", org_id=s.org_id,
        )])
        await s.processor.create_user_group_membership(f"src-{s.alice}", "dept-1", s.connector_id)
        await s.processor.on_new_record_groups([_channel_shared_with_department(s)])

    for _ in range(2):
        task, _lease = await sync.full_sync(script)
        await asyncio.wait_for(task, timeout=60)

    copies = await _department_copies(sync)
    assert set(copies) & await _department_grant(sync), "the department's grant on the channel was unlinked"
    for copy in copies:
        assert await _members_of(sync, copy) == {sync.alice}, copy


async def test_an_older_copy_of_a_department_is_not_unlinked(sync: _Sync) -> None:
    """A copy left by an earlier sync, holding a grant, is handled like the
    current one: given the members this sync read, and counted as seen."""
    department, _members = await _department(sync)
    old_copy = f"old-{sync.connector_id}"
    # Written straight to the graph, as a sync before #4126 left it.
    await sync.graph.batch_upsert_user_groups([department().model_copy(update={"id": old_copy})])

    async def script(s: _Sync) -> None:
        await s.processor.batch_upsert_user_groups([department()])
        await s.processor.create_user_group_membership(f"src-{s.alice}", "dept-1", s.connector_id)
        await s.processor.on_new_record_groups([_channel_shared_with_department(s)])

    task, _lease = await sync.full_sync(script)
    await asyncio.wait_for(task, timeout=60)

    copies = await _department_copies(sync)
    assert old_copy in copies
    assert set(copies) & await _department_grant(sync)
    for copy in copies:
        assert await _members_of(sync, copy) == {sync.alice}, copy


async def test_an_unsure_member_list_keeps_access_through_whichever_copy_holds_the_grant(sync: _Sync) -> None:
    """Two nodes share the department's external id; alice and bob are on the
    older one. This sync records alice, then a membership write fails, and the
    channel's grant ends up on the newer node, which has no members."""
    department, _members = await _department(sync)
    newer = f"newer-{sync.connector_id}"
    await sync.graph.batch_upsert_user_groups([department().model_copy(update={"id": newer})])
    await sync.processor.on_new_record_groups([sync.channel(readers=())])
    await sync.processor.on_new_records([sync.message("m1")])

    async def script(s: _Sync) -> None:
        await s.processor.batch_upsert_user_groups([department()])
        await s.processor.create_user_group_membership(f"src-{s.alice}", "dept-1", s.connector_id)
        assert not await s.processor.create_user_group_membership("src-unknown", "dept-1", s.connector_id)
        await s.processor.on_new_record_groups([s.channel(readers=())])
        await s.processor.on_new_records([s.message("m1")])
        # The grant resolved by external id onto the newer copy.
        channel = await s.graph.get_record_group_by_external_id(s.connector_id, f"C-{s.connector_id}")
        await s.graph.batch_create_edges(
            [Permission(external_id="dept-1", entity_type=EntityType.GROUP, type=PermissionType.READ)
             .to_arango_permission(newer, CollectionNames.GROUPS.value, channel.id, RECORD_GROUPS)],
            collection=CollectionNames.PERMISSION.value,
        )

    task, _lease = await sync.full_sync(script)
    await asyncio.wait_for(task, timeout=60)

    assert newer in await _department_grant(sync)
    assert await sync.can_read(sync.alice, "m1")
    assert await sync.can_read(sync.bob, "m1")
