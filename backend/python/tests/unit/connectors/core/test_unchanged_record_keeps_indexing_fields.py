"""An unchanged, already-indexed record keeps what indexing stored on it.

A sync that saves such a record again publishes nothing for it, so indexing never
writes its statuses and checksum back. ``_process_record`` takes them from the row
it read in the transaction, the same row whose COMPLETED status decides the skip.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import (
    Connectors,
    DeleteSource,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.models.entities import FileRecord, Record, RecordType

pytestmark = pytest.mark.asyncio


def _processor() -> DataSourceEntitiesProcessor:
    proc = DataSourceEntitiesProcessor(MagicMock(), MagicMock(), AsyncMock())
    proc.org_id = "org-1"
    return proc


def _stored(status: str, **overrides) -> Record:
    fields = {
        "id": "stored-1", "org_id": "org-1", "record_name": "a.pdf", "record_type": RecordType.FILE,
        "external_record_id": "ext-1", "external_revision_id": "rev-1", "version": 1,
        "origin": OriginTypes.CONNECTOR, "connector_name": Connectors.GOOGLE_DRIVE, "connector_id": "c1",
        "indexing_status": status, "parsing_status": ProgressStatus.COMPLETED.value,
        "extraction_status": ProgressStatus.COMPLETED.value, "md5_hash": "stored-md5",
        "size_in_bytes": 2048, "storage_document_id": "doc-1",
    }
    fields.update(overrides)
    return Record(**fields)


def _incoming(revision: str = "rev-1") -> FileRecord:
    return FileRecord(
        org_id="org-1", record_name="a.pdf", record_type=RecordType.FILE, external_record_id="ext-1",
        external_revision_id=revision, version=0, origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.GOOGLE_DRIVE, connector_id="c1", is_file=True,
    )


def _store(existing: Record) -> AsyncMock:
    store = AsyncMock()
    store.get_record_by_external_id = AsyncMock(return_value=existing)
    store.get_record_group_by_external_id = AsyncMock(return_value=None)
    store.restore_records = AsyncMock(return_value=[existing.id])
    return store


async def test_unchanged_completed_record_keeps_indexing_fields() -> None:
    processed, _ = await _processor()._process_record(
        _incoming(), [], _store(_stored(ProgressStatus.COMPLETED.value))
    )

    assert processed.indexing_status == ProgressStatus.COMPLETED.value
    assert processed.parsing_status == ProgressStatus.COMPLETED.value
    assert processed.extraction_status == ProgressStatus.COMPLETED.value
    assert processed.md5_hash == "stored-md5"
    assert processed.size_in_bytes == 2048
    assert processed.storage_document_id == "doc-1"


async def test_changed_completed_record_is_reindexed_from_scratch() -> None:
    processed, _ = await _processor()._process_record(
        _incoming("rev-2"), [], _store(_stored(ProgressStatus.COMPLETED.value))
    )

    assert processed.indexing_status == ProgressStatus.NOT_STARTED.value
    assert processed.parsing_status == ProgressStatus.NOT_STARTED.value
    assert processed.md5_hash is None


@pytest.mark.parametrize(
    "status",
    [ProgressStatus.FAILED.value, ProgressStatus.QUEUED.value, ProgressStatus.IN_PROGRESS.value],
)
async def test_unchanged_record_not_completed_starts_clean(status: str) -> None:
    processed, _ = await _processor()._process_record(_incoming(), [], _store(_stored(status)))

    assert processed.parsing_status == ProgressStatus.NOT_STARTED.value
    assert processed.extraction_status == ProgressStatus.NOT_STARTED.value
    assert processed.md5_hash is None


async def test_unchanged_record_restored_for_reindexing_starts_clean() -> None:
    existing = _stored(
        ProgressStatus.COMPLETED.value, is_deleted=True,
        delete_source=DeleteSource.CONNECTOR, delete_batch_id="b-1",
    )
    processed, _ = await _processor()._process_record(_incoming(), [], _store(existing))

    assert processed.indexing_status == ProgressStatus.NOT_STARTED.value
    assert processed.parsing_status == ProgressStatus.NOT_STARTED.value
    assert processed.md5_hash is None
