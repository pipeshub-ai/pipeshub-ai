"""Every record event a sync publishes carries the id of that sync run."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.sync.run_context import (
    get_run_sync_id,
    reset_run_sync_id,
    set_run_sync_id,
)
from app.connectors.core.sync.sync_runner import run_sync_task


def _record() -> MagicMock:
    record = MagicMock()
    record.to_kafka_record.return_value = {"recordId": "r1"}
    return record


def test_a_record_event_inside_a_run_carries_its_id() -> None:
    processor = DataSourceEntitiesProcessor(MagicMock(), MagicMock(), MagicMock())
    token = set_run_sync_id("run-1")
    try:
        payload = processor._record_event_payload(_record())
    finally:
        reset_run_sync_id(token)

    assert payload == {"recordId": "r1", "runSyncId": "run-1"}


def test_a_record_event_outside_a_run_carries_none() -> None:
    processor = DataSourceEntitiesProcessor(MagicMock(), MagicMock(), MagicMock())

    assert processor._record_event_payload(_record())["runSyncId"] is None


async def test_a_sync_run_sets_one_id_for_the_run_and_clears_it_after() -> None:
    seen: list[str | None] = []
    connector = MagicMock()
    connector.run_sync = AsyncMock(side_effect=lambda: seen.append(get_run_sync_id()))

    await run_sync_task(connector, "c1", AsyncMock(), logging.getLogger("test"))

    assert seen[0] is not None
    assert get_run_sync_id() is None


async def test_a_reindex_event_published_during_a_run_carries_its_id() -> None:
    from app.connectors.services.kafka_service import KafkaService

    producer = MagicMock(start=AsyncMock(), send_message=AsyncMock(return_value=True))
    service = KafkaService(MagicMock(), MagicMock(), producer)
    token = set_run_sync_id("run-2")
    try:
        await service.send_event_to_kafka({"recordId": "r1", "orgId": "o1"})
    finally:
        reset_run_sync_id(token)

    assert producer.send_message.await_args.kwargs["message"]["payload"]["runSyncId"] == "run-2"
