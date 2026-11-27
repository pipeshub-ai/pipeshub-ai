"""A reindex's connector writes commit only while no sync of the connector started since the reindex began.

The interleaving itself, on each graph store: tests/integration/graph_db/test_reindex_sync_ordering_real_backend.py.
"""
from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import AppStatus
from app.connectors.core.base.connector.connector_service import connector_sync_lock
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    USER_LINK_MARK_TTL_MS,
    DataSourceEntitiesProcessor,
    ReindexSuperseded,
    unless_synced_since,
)
from app.connectors.core.sync.sync_runner import _run_sync_alone
from app.connectors.services.event_service import EventService
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

IDLE = {"status": AppStatus.IDLE.value, "lastSyncStartedAtTimestamp": 5, "lastSyncFinishedAtTimestamp": 6}
STAMP = {"lastSyncStartedAtTimestamp": 5, "lastSyncFinishedAtTimestamp": 6}


def _processor(unchanged: bool) -> tuple[DataSourceEntitiesProcessor, MagicMock, list]:
    tx = MagicMock(lock_app_if_unchanged=AsyncMock(return_value=unchanged), batch_update_nodes=AsyncMock(return_value=True))
    opened: list = []

    @contextlib.asynccontextmanager
    async def transaction(**options: object) -> AsyncIterator[MagicMock]:
        opened.append(options)
        yield tx

    processor = DataSourceEntitiesProcessor(MagicMock(), MagicMock(transaction=transaction), AsyncMock())
    return processor, tx, opened


async def _write(processor: DataSourceEntitiesProcessor) -> None:
    async with processor._sync_transaction():
        pass


async def test_a_write_commits_while_the_app_document_is_unchanged() -> None:
    processor, tx, opened = _processor(unchanged=True)
    async with unless_synced_since(AsyncMock(get_document=AsyncMock(return_value=IDLE)), "c1") as stamp:
        await _write(processor)
    tx.lock_app_if_unchanged.assert_awaited_once_with("c1", STAMP)
    assert opened == [{"explicit": True}] and not stamp.tripped


@pytest.mark.parametrize("app", [None, {**IDLE, "status": AppStatus.SYNCING.value}, {**IDLE, "status": AppStatus.FULL_SYNCING.value}])
async def test_a_sync_running_when_the_reindex_began_refuses_every_write(app: dict | None) -> None:
    processor, tx, _ = _processor(unchanged=True)
    async with unless_synced_since(AsyncMock(get_document=AsyncMock(return_value=app)), "c1") as stamp:
        with pytest.raises(ReindexSuperseded):
            await _write(processor)
    tx.lock_app_if_unchanged.assert_not_awaited()
    assert stamp.tripped


@pytest.mark.parametrize("sync_started_since", [False, True])
async def test_a_user_link_pass_in_progress_when_the_reindex_began_refuses_every_write(sync_started_since: bool) -> None:
    """The pass may run in another process, and a sync there may start (and end) after the pass did: its mark still counts."""
    now = get_epoch_timestamp_in_ms()
    in_progress = {**IDLE, "lastSyncStartedAtTimestamp": now + int(sync_started_since), "userLinkStartedAtTimestamp": now}
    processor, tx, _ = _processor(unchanged=True)
    async with unless_synced_since(AsyncMock(get_document=AsyncMock(return_value=in_progress)), "c9") as stamp:
        with pytest.raises(ReindexSuperseded):
            await _write(processor)
    tx.lock_app_if_unchanged.assert_not_awaited()
    assert stamp.tripped and stamp.expected is None


async def test_a_user_link_mark_older_than_its_ttl_is_not_a_pass_in_progress() -> None:
    """A running pass rewrites its mark; one that ended or crashed leaves it, and reindexes write again once it is that old."""
    stale = {**IDLE, "userLinkStartedAtTimestamp": get_epoch_timestamp_in_ms() - USER_LINK_MARK_TTL_MS}
    processor, tx, _ = _processor(unchanged=True)
    async with unless_synced_since(AsyncMock(get_document=AsyncMock(return_value=stale)), "c10") as stamp:
        await _write(processor)
    tx.lock_app_if_unchanged.assert_awaited_once_with("c10", STAMP)
    assert not stamp.tripped


async def test_a_sync_started_since_or_running_in_this_process_refuses_the_write() -> None:
    processor, _, _ = _processor(unchanged=False)
    async with unless_synced_since(AsyncMock(get_document=AsyncMock(return_value=IDLE)), "c2") as stamp:
        with pytest.raises(ReindexSuperseded):
            await _write(processor)
    assert stamp.tripped

    processor, _, _ = _processor(unchanged=True)
    async with unless_synced_since(AsyncMock(get_document=AsyncMock(return_value=IDLE)), "c3") as stamp:
        async with connector_sync_lock("c3"):
            with pytest.raises(ReindexSuperseded):
                await _write(processor)
    assert stamp.tripped


async def test_a_sync_this_process_started_and_ended_since_refuses_the_write_whatever_the_store_holds() -> None:
    """Its status writes may all have failed: the store still holds what the reindex read."""
    processor, _, _ = _processor(unchanged=True)
    async with unless_synced_since(AsyncMock(get_document=AsyncMock(return_value=IDLE)), "c6") as stamp:
        await _run_sync_alone(SimpleNamespace(run_sync=AsyncMock()), "c6")
        with pytest.raises(ReindexSuperseded):
            await _write(processor)
    assert stamp.tripped


async def test_a_failed_read_of_the_app_document_is_an_error_not_a_superseded_batch() -> None:
    async def get_document(*_: object, raise_on_error: bool = False) -> dict | None:
        if raise_on_error:
            raise RuntimeError("store unavailable")
        return None

    with pytest.raises(RuntimeError, match="store unavailable"):
        async with unless_synced_since(AsyncMock(get_document=get_document), "c7"):
            pass


async def test_a_path_rewrite_during_a_reindex_is_checked() -> None:
    processor, tx, _ = _processor(unchanged=False)
    async with unless_synced_since(AsyncMock(get_document=AsyncMock(return_value=IDLE)), "c8") as stamp:
        with pytest.raises(ReindexSuperseded):
            await processor.batch_update_nodes([{"_key": "f1", "path": "/b/x"}], "files")
    tx.lock_app_if_unchanged.assert_awaited_once_with("c8", STAMP)
    assert stamp.tripped


async def test_outside_a_reindex_writes_are_unchecked() -> None:
    processor, tx, opened = _processor(unchanged=False)
    await _write(processor)
    tx.lock_app_if_unchanged.assert_not_awaited()
    assert opened == [{}]


def _connector(**attrs) -> SimpleNamespace:
    return SimpleNamespace(records_excluded_by_indexing_filters=AsyncMock(return_value=set()), **attrs)


def _record(record_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=record_id, is_placeholder=False, indexing_status="FAILED", record_type="FILE", mime_type="text/plain")


async def test_a_batch_a_sync_superseded_does_not_stop_the_reindex_and_the_next_one_writes() -> None:
    # The first batch begins while a sync runs; the second, after it ended, takes a stamp of its own.
    graph = AsyncMock(get_document=AsyncMock(side_effect=[{**IDLE, "status": AppStatus.SYNCING.value}, IDLE]))
    graph.get_records_by_status = AsyncMock(side_effect=[[_record(f"r{i}") for i in range(100)], [_record("r100")]])
    graph.get_typed_records_batch = AsyncMock(return_value={})
    processor, tx, _ = _processor(unchanged=True)
    written: list[int] = []

    async def reindex_records(records: list) -> None:
        await _write(processor)
        written.append(len(records))

    service = SimpleNamespace(graph_provider=graph, logger=MagicMock())
    await EventService._reindex_batches(
        service, _connector(reindex_records=reindex_records, data_entities_processor=processor),
        "drive", "c4", "o1", None, None, 0, None, ["FAILED"],
    )
    assert written == [1]
    tx.lock_app_if_unchanged.assert_awaited_once_with("c4", STAMP)


async def test_what_a_superseded_batch_left_unpublished_is_reindexed_as_stored() -> None:
    graph = AsyncMock(get_document=AsyncMock(return_value={**IDLE, "status": AppStatus.SYNCING.value}))
    graph.get_records_by_status = AsyncMock(return_value=[_record("r1"), _record("r2")])
    left = SimpleNamespace(id="r1", indexing_status="NOT_STARTED")
    graph.get_typed_records_batch = AsyncMock(return_value={"r1": left, "r2": SimpleNamespace(id="r2", indexing_status="QUEUED")})
    processor = MagicMock(reindex_existing_records=AsyncMock())
    written, _, _ = _processor(unchanged=True)

    async def reindex_records(records: list) -> None:
        await _write(written)  # refused: the connector stops before it publishes anything

    service = SimpleNamespace(graph_provider=graph, logger=MagicMock())
    await EventService._reindex_batches(
        service, _connector(reindex_records=reindex_records, data_entities_processor=processor),
        "drive", "c9", "o1", None, None, 0, None, ["FAILED"],
    )
    processor.reindex_existing_records.assert_awaited_once_with([left])


async def test_an_error_that_is_not_a_superseded_write_still_ends_the_reindex() -> None:
    graph = AsyncMock(get_document=AsyncMock(return_value=IDLE))
    graph.get_records_by_status = AsyncMock(return_value=[_record("r1")])
    service = SimpleNamespace(graph_provider=graph, logger=MagicMock())
    with pytest.raises(RuntimeError, match="source down"):
        await EventService._reindex_batches(
            service, _connector(reindex_records=AsyncMock(side_effect=RuntimeError("source down"))),
            "drive", "c5", "o1", None, None, 0, None, ["FAILED"],
        )
