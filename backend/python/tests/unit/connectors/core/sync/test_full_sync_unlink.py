"""A full sync removes the links of what it did not see only once it has finished.

The prep step used to delete every link of the connector before the sync began.
A sync cut off partway then left everything it had not reached readable by
nobody. Now the processor notes what the sync writes and ``run_sync_task``
unlinks the rest only after ``run_sync`` returned on its own. The real-database
side is in tests/integration/graph_db/test_full_sync_unlink_real_backends.py.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import AppStatus, CollectionNames
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.constants import ConnectorStateKeys
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.core.sync.sync_coordinator import SyncLease
from app.connectors.core.sync.sync_runner import run_sync_task

_LOG = logging.getLogger("test")
_OWED = ConnectorStateKeys.PENDING_FULL_SYNC
RECORDS = CollectionNames.RECORDS.value


def _graph_provider() -> AsyncMock:
    gp = AsyncMock()
    gp.get_document = AsyncMock(return_value={"id": "c1", "status": "FULL_SYNCING"})
    gp.unlink_nodes_missed_by_full_sync = AsyncMock(return_value=(4, True))
    return gp


def _connector(run_sync, coverage: FullSyncCoverage | None = None) -> MagicMock:
    connector = MagicMock()
    connector.data_entities_processor = DataSourceEntitiesProcessor(_LOG, MagicMock(), MagicMock())
    connector.run_sync = run_sync
    connector.full_sync_coverage = MagicMock(return_value=coverage or FullSyncCoverage())
    return connector


def _writes_one_record(connector: MagicMock) -> Callable[[], Awaitable[None]]:
    async def run_sync() -> None:
        connector.data_entities_processor._note_full_sync_seen("c1", RECORDS, "r1")

    return run_sync


def _owed(gp: AsyncMock) -> bool:
    return any(c.args[2].get(_OWED) for c in gp.update_node.await_args_list)


@pytest.mark.asyncio
async def test_a_finished_full_sync_unlinks_what_it_did_not_see() -> None:
    gp = _graph_provider()
    connector = _connector(None, FullSyncCoverage(window_start_ms=1000, windowed_group_types=frozenset({"T"})))
    connector.run_sync = _writes_one_record(connector)

    await run_sync_task(connector, "c1", gp, _LOG, start_status=AppStatus.FULL_SYNCING.value)

    gp.unlink_nodes_missed_by_full_sync.assert_awaited_once()
    call = gp.unlink_nodes_missed_by_full_sync.await_args
    assert call.args[0] == "c1"
    assert call.args[1][RECORDS] == {"r1"}
    assert call.kwargs == {"window_start_ms": 1000, "windowed_group_types": frozenset({"T"})}
    assert not _owed(gp)
    assert connector.data_entities_processor._full_sync_run("c1") is None, "the run is forgotten"


@pytest.mark.asyncio
async def test_a_full_sync_that_raised_unlinks_nothing() -> None:
    gp = _graph_provider()
    connector = _connector(None)

    async def run_sync() -> None:
        connector.data_entities_processor._note_full_sync_seen("c1", RECORDS, "r1")
        raise RuntimeError("source went away")

    connector.run_sync = run_sync
    with pytest.raises(RuntimeError):
        await run_sync_task(connector, "c1", gp, _LOG, start_status=AppStatus.FULL_SYNCING.value)

    gp.unlink_nodes_missed_by_full_sync.assert_not_awaited()
    assert _owed(gp)
    assert connector.data_entities_processor._full_sync_run("c1") is None


@pytest.mark.asyncio
async def test_a_full_sync_cancelled_by_a_restart_unlinks_nothing() -> None:
    gp = _graph_provider()
    connector = _connector(None)
    started = asyncio.Event()

    async def run_sync() -> None:
        connector.data_entities_processor._note_full_sync_seen("c1", RECORDS, "r1")
        started.set()
        await asyncio.sleep(3600)

    connector.run_sync = run_sync
    task = asyncio.create_task(
        run_sync_task(connector, "c1", gp, _LOG, start_status=AppStatus.FULL_SYNCING.value,
                      lease=SyncLease("c1", "tok", 1), coordinator=AsyncMock())
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    gp.unlink_nodes_missed_by_full_sync.assert_not_awaited()
    assert _owed(gp)


@pytest.mark.asyncio
async def test_a_stopped_full_sync_unlinks_nothing() -> None:
    """A stop ends the run without raising, so it is read from the lease."""
    gp = _graph_provider()
    connector = _connector(None)
    lease = SyncLease("c1", "tok", 1)
    started = asyncio.Event()

    async def run_sync() -> None:
        connector.data_entities_processor._note_full_sync_seen("c1", RECORDS, "r1")
        started.set()
        await asyncio.sleep(3600)

    connector.run_sync = run_sync
    task = asyncio.create_task(
        run_sync_task(connector, "c1", gp, _LOG, start_status=AppStatus.FULL_SYNCING.value,
                      lease=lease, coordinator=AsyncMock())
    )
    await started.wait()
    lease.stop_requested.set()
    await task

    gp.unlink_nodes_missed_by_full_sync.assert_not_awaited()
    assert _owed(gp)


@pytest.mark.asyncio
async def test_an_incremental_sync_unlinks_nothing() -> None:
    gp = _graph_provider()
    connector = _connector(None)
    connector.run_sync = _writes_one_record(connector)

    await run_sync_task(connector, "c1", gp, _LOG)

    gp.unlink_nodes_missed_by_full_sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_sync_that_skipped_part_of_the_source_unlinks_nothing() -> None:
    gp = _graph_provider()
    connector = _connector(None, FullSyncCoverage(complete=False))
    connector.run_sync = _writes_one_record(connector)

    await run_sync_task(connector, "c1", gp, _LOG, start_status=AppStatus.FULL_SYNCING.value)

    gp.unlink_nodes_missed_by_full_sync.assert_not_awaited()
    assert not _owed(gp)


@pytest.mark.asyncio
async def test_a_sync_that_wrote_nothing_unlinks_nothing() -> None:
    """Most likely a sync that failed without raising: unlinking everything
    would empty the connector."""
    gp = _graph_provider()

    async def run_sync() -> None:
        return None

    await run_sync_task(_connector(run_sync), "c1", gp, _LOG, start_status=AppStatus.FULL_SYNCING.value)

    gp.unlink_nodes_missed_by_full_sync.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [(0, False), RuntimeError("db down")])
async def test_an_unlink_that_failed_owes_another_full_sync(outcome) -> None:
    """What the sync did not see is still linked, revoked access included."""
    gp = _graph_provider()
    if isinstance(outcome, Exception):
        gp.unlink_nodes_missed_by_full_sync = AsyncMock(side_effect=outcome)
    else:
        gp.unlink_nodes_missed_by_full_sync = AsyncMock(return_value=outcome)
    connector = _connector(None)
    connector.run_sync = _writes_one_record(connector)

    await run_sync_task(connector, "c1", gp, _LOG, start_status=AppStatus.FULL_SYNCING.value)

    assert _owed(gp)


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["begin_full_sync_run", "end_full_sync_run"])
async def test_a_tracking_failure_still_finalizes(step: str) -> None:
    """IDLE is written and the lease released even if the tracking itself raises;
    otherwise the connector stays FULL_SYNCING until the lease expires."""
    gp = _graph_provider()
    connector = _connector(None)
    connector.run_sync = _writes_one_record(connector)
    setattr(connector.data_entities_processor, step, MagicMock(side_effect=RuntimeError("tracking")))
    coordinator = AsyncMock()
    lease = SyncLease("c1", "tok", 1)

    with contextlib.suppress(RuntimeError):
        await run_sync_task(connector, "c1", gp, _LOG, start_status=AppStatus.FULL_SYNCING.value,
                            lease=lease, coordinator=coordinator)

    statuses = [c.args[2].get("status") for c in gp.update_node.await_args_list]
    assert AppStatus.IDLE.value in statuses
    coordinator.end.assert_awaited_once_with(lease)


class TestWhatTheProcessorNotes:
    """Seen only once its write committed; first visit replaces, later ones add."""

    def _processor(self, transaction) -> DataSourceEntitiesProcessor:
        store = MagicMock()
        store.transaction = transaction
        return DataSourceEntitiesProcessor(_LOG, store, MagicMock())

    @staticmethod
    def _transaction(fail: bool = False) -> Callable[[], AbstractAsyncContextManager[MagicMock]]:
        @contextlib.asynccontextmanager
        async def transaction() -> AsyncIterator[MagicMock]:
            yield MagicMock()
            if fail:
                raise RuntimeError("commit failed")

        return transaction

    @pytest.mark.asyncio
    async def test_a_node_counts_as_seen_once_its_transaction_commits(self) -> None:
        processor = self._processor(self._transaction())
        run = processor.begin_full_sync_run("c1")

        async with processor._sync_transaction():
            processor._note_full_sync_seen("c1", RECORDS, "r1")
            assert not run.has(RECORDS, "r1"), "not before the commit"
            assert not processor._first_full_sync_visit("c1", RECORDS, "r1"), "but known within it"

        assert run.has(RECORDS, "r1")
        assert not processor._first_full_sync_visit("c1", RECORDS, "r1")

    @pytest.mark.asyncio
    async def test_a_write_that_rolled_back_is_not_seen(self) -> None:
        """Else the retry would add to the old grants instead of replacing them."""
        processor = self._processor(self._transaction(fail=True))
        run = processor.begin_full_sync_run("c1")

        with pytest.raises(RuntimeError):
            async with processor._sync_transaction():
                processor._note_full_sync_seen("c1", RECORDS, "r1")

        assert not run.has(RECORDS, "r1")
        assert processor._first_full_sync_visit("c1", RECORDS, "r1")

    def test_nothing_is_noted_without_a_running_full_sync(self) -> None:
        processor = self._processor(self._transaction())
        processor._note_full_sync_seen("c1", RECORDS, "r1")
        assert not processor._first_full_sync_visit("c1", RECORDS, "r1")

    def test_full_sync_running_follows_the_run(self) -> None:
        processor = self._processor(self._transaction())
        assert processor.full_sync_running("c1") is False
        run = processor.begin_full_sync_run("c1")
        assert processor.full_sync_running("c1") is True
        assert processor.full_sync_running("other") is False
        processor.end_full_sync_run("c1", run)
        assert processor.full_sync_running("c1") is False

    def test_runs_of_two_connectors_on_one_shared_processor_stay_apart(self) -> None:
        processor = self._processor(self._transaction())
        a = processor.begin_full_sync_run("a")
        b = processor.begin_full_sync_run("b")
        processor._note_full_sync_seen("a", RECORDS, "r1")
        assert a.has(RECORDS, "r1") and not b.has(RECORDS, "r1")
        processor.end_full_sync_run("a", a)
        assert processor._full_sync_run("a") is None and processor._full_sync_run("b") is b

    @staticmethod
    def _membership_store(*results: bool) -> MagicMock:
        tx = MagicMock()
        tx.create_user_group_membership = AsyncMock(side_effect=list(results))
        tx.get_user_by_source_id = AsyncMock(
            side_effect=lambda source_id, _connector: MagicMock(id=f"user-{source_id}", email=f"{source_id}@x")
        )
        tx.get_nodes_by_filters = AsyncMock(return_value=[{"_key": "g1"}])
        # Read by batch_upsert_user_groups once #4126 reuses the stored id.
        tx.get_user_group_by_external_id = AsyncMock(return_value=None)
        tx.get_edges_to_node = AsyncMock(return_value=[])
        tx.batch_create_edges = AsyncMock()
        tx.replace_edges_to = AsyncMock()
        tx.delete_edges_to = AsyncMock()
        return tx

    def _membership_processor(self, tx: MagicMock) -> DataSourceEntitiesProcessor:
        @contextlib.asynccontextmanager
        async def transaction() -> AsyncIterator[MagicMock]:
            yield tx

        return self._processor(transaction)

    @pytest.mark.asyncio
    async def test_memberships_only_add_while_the_sync_runs(self) -> None:
        """ServiceNow adds organisational-group members one user page at a time.
        Deleting ahead of the full list would lock out everyone not yet re-added
        if a later page failed, so nothing is deleted during the sync."""
        tx = self._membership_store(True, True)
        processor = self._membership_processor(tx)
        run = processor.begin_full_sync_run("c1")

        assert await processor.create_user_group_membership("u1", "dept-1", "c1")
        assert await processor.create_user_group_membership("u2", "dept-1", "c1")

        tx.delete_edges_to.assert_not_awaited()
        tx.replace_edges_to.assert_not_awaited()
        assert not run.has(CollectionNames.GROUPS.value, "g1"), "seen only once replaced"

    @pytest.mark.asyncio
    async def test_after_the_sync_each_group_gets_exactly_the_members_it_read(self) -> None:
        tx = self._membership_store(True, True)
        processor = self._membership_processor(tx)
        run = processor.begin_full_sync_run("c1")
        await processor.create_user_group_membership("u1", "dept-1", "c1")
        await processor.create_user_group_membership("u2", "dept-1", "c1")

        await processor.finish_full_sync_memberships("c1", run)

        tx.replace_edges_to.assert_awaited_once()
        group_id, collection, edges, edge_collection = tx.replace_edges_to.await_args.args
        assert (group_id, collection, edge_collection) == (
            "g1", CollectionNames.GROUPS.value, CollectionNames.PERMISSION.value
        )
        assert len(edges) == 2
        assert run.has(CollectionNames.GROUPS.value, "g1")

    @pytest.mark.asyncio
    async def test_a_membership_that_did_not_land_keeps_the_groups_members(self) -> None:
        """The store answers False for a missing user or an error it swallowed:
        the list read is not complete, so the group is not replaced."""
        tx = self._membership_store(True, False)
        processor = self._membership_processor(tx)
        run = processor.begin_full_sync_run("c1")
        await processor.create_user_group_membership("u1", "dept-1", "c1")
        await processor.create_user_group_membership("u2", "dept-1", "c1")

        await processor.finish_full_sync_memberships("c1", run)

        tx.replace_edges_to.assert_not_awaited()
        assert run.has(CollectionNames.GROUPS.value, "g1"), "kept as it is, so not unlinked either"

    @pytest.mark.asyncio
    async def test_a_group_written_without_members_and_given_none_gets_an_empty_list(self) -> None:
        """A department with no users this sync: its member edges are replaced by
        none, and it counts as seen, so the grants naming it are not unlinked."""
        from app.models.entities import AppUserGroup

        tx = self._membership_store()
        tx.batch_upsert_user_groups = AsyncMock()
        processor = self._membership_processor(tx)
        run = processor.begin_full_sync_run("c1")
        await processor.batch_upsert_user_groups(
            [AppUserGroup(app_name="SERVICENOW", connector_id="c1", source_user_group_id="dept-1", name="Sales")]
        )

        await processor.finish_full_sync_memberships("c1", run)

        tx.replace_edges_to.assert_awaited_once()
        assert tx.replace_edges_to.await_args.args[2] == []
        assert run.has(CollectionNames.GROUPS.value, "g1")

    @pytest.mark.asyncio
    async def test_every_copy_of_a_group_gets_the_members_and_counts_as_seen(self) -> None:
        """Syncs before #4126 wrote a new node per sync for one department; the
        grants naming it may be on any of them."""
        tx = self._membership_store(True)
        tx.get_nodes_by_filters = AsyncMock(return_value=[{"_key": "g1"}, {"id": "g2"}])
        processor = self._membership_processor(tx)
        run = processor.begin_full_sync_run("c1")
        await processor.create_user_group_membership("u1", "dept-1", "c1")

        await processor.finish_full_sync_memberships("c1", run)

        assert sorted(c.args[0] for c in tx.replace_edges_to.await_args_list) == ["g1", "g2"]
        assert run.has(CollectionNames.GROUPS.value, "g1") and run.has(CollectionNames.GROUPS.value, "g2")

    @pytest.mark.asyncio
    async def test_an_unsure_list_gives_every_copy_the_members_stored_on_any(self) -> None:
        """A grant may have been rewritten onto the copy that lacks the members;
        with no complete list, every copy gets the union, and nothing is removed."""
        tx = self._membership_store(True, False)
        tx.get_nodes_by_filters = AsyncMock(return_value=[{"_key": "old"}, {"_key": "new"}])
        stored = {
            "groups/old": [{"_from": "users/alice", "type": "USER", "role": "READER"},
                           {"from_id": "bob", "from_collection": "users", "type": "USER"}],
            "groups/new": [],
        }
        tx.get_edges_to_node = AsyncMock(side_effect=lambda node, _collection, **_kw: stored[node])
        processor = self._membership_processor(tx)
        run = processor.begin_full_sync_run("c1")
        await processor.create_user_group_membership("u1", "dept-1", "c1")
        await processor.create_user_group_membership("u2", "dept-1", "c1")

        await processor.finish_full_sync_memberships("c1", run)

        tx.replace_edges_to.assert_not_awaited()
        written = {c.args[0][0]["to_id"]: {(e["from_collection"], e["from_id"]) for e in c.args[0]}
                   for c in tx.batch_create_edges.await_args_list}
        assert written == {"old": {("users", "alice"), ("users", "bob")}, "new": {("users", "alice"), ("users", "bob")}}
        assert run.has(CollectionNames.GROUPS.value, "old") and run.has(CollectionNames.GROUPS.value, "new")

    @pytest.mark.asyncio
    async def test_a_failed_read_of_the_stored_members_raises_and_marks_nothing_seen(self) -> None:
        """Taken for "no members", the copy holding a grant would keep nobody and
        be marked seen; raised, the runner skips the unlink and owes a full sync."""
        tx = self._membership_store(True, False)
        tx.get_nodes_by_filters = AsyncMock(return_value=[{"_key": "old"}, {"_key": "new"}])
        tx.get_edges_to_node = AsyncMock(side_effect=RuntimeError("graph read failed"))
        processor = self._membership_processor(tx)
        run = processor.begin_full_sync_run("c1")
        await processor.create_user_group_membership("u1", "dept-1", "c1")
        await processor.create_user_group_membership("u2", "dept-1", "c1")

        with pytest.raises(RuntimeError):
            await processor.finish_full_sync_memberships("c1", run)

        assert tx.get_edges_to_node.await_args.kwargs == {"raise_on_error": True}
        tx.batch_create_edges.assert_not_awaited()
        tx.replace_edges_to.assert_not_awaited()
        assert not run.has(CollectionNames.GROUPS.value, "old")
        assert not run.has(CollectionNames.GROUPS.value, "new")

    @pytest.mark.asyncio
    async def test_outside_a_full_sync_memberships_are_not_tracked(self) -> None:
        tx = self._membership_store(True)
        processor = self._membership_processor(tx)
        assert await processor.create_user_group_membership("u1", "dept-1", "c1")
        tx.get_user_by_source_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_memberships_are_not_replaced_when_the_sync_raised() -> None:
    gp = _graph_provider()
    connector = _connector(None)
    connector.data_entities_processor.finish_full_sync_memberships = AsyncMock()

    async def run_sync() -> None:
        connector.data_entities_processor._note_full_sync_seen("c1", RECORDS, "r1")
        raise RuntimeError("the next user page failed")

    connector.run_sync = run_sync
    with pytest.raises(RuntimeError):
        await run_sync_task(connector, "c1", gp, _LOG, start_status=AppStatus.FULL_SYNCING.value)

    connector.data_entities_processor.finish_full_sync_memberships.assert_not_awaited()


@pytest.mark.asyncio
async def test_memberships_are_replaced_before_the_unlink() -> None:
    gp = _graph_provider()
    connector = _connector(None)
    order: list[str] = []
    connector.data_entities_processor.finish_full_sync_memberships = AsyncMock(
        side_effect=lambda *_a: order.append("memberships")
    )
    gp.unlink_nodes_missed_by_full_sync = AsyncMock(side_effect=lambda *_a, **_k: order.append("unlink") or (0, True))
    connector.run_sync = _writes_one_record(connector)

    await run_sync_task(connector, "c1", gp, _LOG, start_status=AppStatus.FULL_SYNCING.value)

    assert order == ["memberships", "unlink"]
