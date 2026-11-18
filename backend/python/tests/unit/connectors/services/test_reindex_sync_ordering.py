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
    DataSourceEntitiesProcessor,
    ReindexSuperseded,
    unless_synced_since,
)
from app.connectors.services.event_service import EventService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

IDLE = {"status": AppStatus.IDLE.value, "lastSyncStartedAtTimestamp": 5}


def _processor(unchanged: bool) -> tuple[DataSourceEntitiesProcessor, MagicMock, list]:
    tx = MagicMock(lock_app_if_unchanged=AsyncMock(return_value=unchanged))
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
    tx.lock_app_if_unchanged.assert_awaited_once_with("c1", IDLE)
    assert opened == [{"explicit": True}] and not stamp.tripped


@pytest.mark.parametrize("app", [None, {**IDLE, "status": AppStatus.SYNCING.value}, {**IDLE, "status": AppStatus.FULL_SYNCING.value}])
async def test_a_sync_running_when_the_reindex_began_refuses_every_write(app: dict | None) -> None:
    processor, tx, _ = _processor(unchanged=True)
    async with unless_synced_since(AsyncMock(get_document=AsyncMock(return_value=app)), "c1") as stamp:
        with pytest.raises(ReindexSuperseded):
            await _write(processor)
    tx.lock_app_if_unchanged.assert_not_awaited()
    assert stamp.tripped


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


async def test_outside_a_reindex_writes_are_unchecked() -> None:
    processor, tx, opened = _processor(unchanged=False)
    await _write(processor)
    tx.lock_app_if_unchanged.assert_not_awaited()
    assert opened == [{}]


def _record(record_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=record_id, is_placeholder=False, indexing_status="FAILED", record_type="FILE", mime_type="text/plain")


async def test_a_batch_a_sync_superseded_does_not_stop_the_reindex() -> None:
    graph = AsyncMock(get_document=AsyncMock(return_value={**IDLE, "status": AppStatus.SYNCING.value}))
    graph.get_records_by_status = AsyncMock(side_effect=[[_record(f"r{i}") for i in range(100)], [_record("r100")]])
    processor, _, _ = _processor(unchanged=True)
    seen: list[int] = []

    async def reindex_records(records: list) -> None:
        seen.append(len(records))
        await _write(processor)

    service = SimpleNamespace(graph_provider=graph, logger=MagicMock())
    await EventService._reindex_batches(
        service, SimpleNamespace(reindex_records=reindex_records), "drive", "c4", "o1", None, None, 0, None, ["FAILED"],
    )
    assert seen == [100, 1]


async def test_an_error_that_is_not_a_superseded_write_still_ends_the_reindex() -> None:
    graph = AsyncMock(get_document=AsyncMock(return_value=IDLE))
    graph.get_records_by_status = AsyncMock(return_value=[_record("r1")])
    service = SimpleNamespace(graph_provider=graph, logger=MagicMock())
    with pytest.raises(RuntimeError, match="source down"):
        await EventService._reindex_batches(
            service, SimpleNamespace(reindex_records=AsyncMock(side_effect=RuntimeError("source down"))),
            "drive", "c5", "o1", None, None, 0, None, ["FAILED"],
        )
