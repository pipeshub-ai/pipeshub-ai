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

    def test_runs_of_two_connectors_on_one_shared_processor_stay_apart(self) -> None:
        processor = self._processor(self._transaction())
        a = processor.begin_full_sync_run("a")
        b = processor.begin_full_sync_run("b")
        processor._note_full_sync_seen("a", RECORDS, "r1")
        assert a.has(RECORDS, "r1") and not b.has(RECORDS, "r1")
        processor.end_full_sync_run("a", a)
        assert processor._full_sync_run("a") is None and processor._full_sync_run("b") is b
