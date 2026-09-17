"""Records put in line for indexing carry a platform-owned queue clock.

The stranded-record sweep (indexing_main._republish_stranded_records) ages rows
on it. Connectors may fill created_at/updated_at with source-system time, so a
Jira issue last edited a year ago looked stranded the moment it was synced.
The clock moves only when a record is put in line for an event: a write that
publishes nothing (a metadata refresh) must not postpone recovery of a record
whose event was lost.
"""
from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.models.entities import FileRecord
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.unit.connectors.core.test_data_processor import (
    _make_processor,
    _make_record,
    _make_tx_store,
)

A_YEAR_AGO = get_epoch_timestamp_in_ms() - 365 * 24 * 3600 * 1000


def _source_timestamped(**overrides: object) -> FileRecord:
    return _make_record(
        created_at=A_YEAR_AGO,
        updated_at=A_YEAR_AGO,
        source_created_at=A_YEAR_AGO,
        source_updated_at=A_YEAR_AGO,
        **overrides,
    )


def _processor_with(tx_store: AsyncMock) -> DataSourceEntitiesProcessor:
    proc = _make_processor()
    transaction = AsyncMock()
    transaction.__aenter__ = AsyncMock(return_value=tx_store)
    transaction.__aexit__ = AsyncMock(return_value=False)
    proc.data_store_provider.transaction.return_value = transaction
    return proc


def _upserted(tx_store: AsyncMock) -> list:
    return [record for call in tx_store.batch_upsert_records.await_args_list for record in call.args[0]]


class TestQueuedAtStamp:
    @pytest.mark.asyncio
    async def test_a_new_record_is_stamped_before_its_event_is_published(self) -> None:
        tx_store = _make_tx_store()
        proc = _processor_with(tx_store)
        before = get_epoch_timestamp_in_ms()

        await proc.on_new_records([(_source_timestamped(), [])])

        stored = _upserted(tx_store)[0]
        assert stored.queued_at is not None and stored.queued_at >= before
        assert stored.to_arango_base_record()["queuedAtTimestamp"] == stored.queued_at
        assert stored.updated_at == A_YEAR_AGO, "the connector's own clock is kept"
        proc.messaging_producer.send_messages.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_content_change_that_requeues_a_record_is_stamped(self) -> None:
        tx_store = _make_tx_store()
        existing = _source_timestamped(indexing_status=ProgressStatus.COMPLETED.value, external_revision_id="r1")
        existing.id = "rec-1"
        tx_store.get_record_by_external_id.return_value = existing
        proc = _processor_with(tx_store)

        await proc.on_new_records([(_source_timestamped(external_revision_id="r2"), [])])

        assert _upserted(tx_store)[0].queued_at is not None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("revision", ["r1", "r2"], ids=["same-revision", "new-revision"])
    async def test_a_metadata_only_update_keeps_the_stored_stamp(self, revision: str) -> None:
        tx_store = _make_tx_store()
        existing = _source_timestamped(indexing_status=ProgressStatus.QUEUED.value, external_revision_id="r1")
        existing.id = "rec-1"
        tx_store.get_record_by_external_id.return_value = existing
        proc = _processor_with(tx_store)

        await proc.on_record_metadata_update(
            _source_timestamped(record_name="renamed", external_revision_id=revision)
        )

        upserted = _upserted(tx_store)
        assert upserted, "the metadata write itself still happens"
        # Omitted, not null: the upsert merges, so the stored stamp survives.
        assert all("queuedAtTimestamp" not in record.to_arango_base_record() for record in upserted)
        proc.messaging_producer.send_messages.assert_not_awaited()
