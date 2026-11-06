"""A connector save of unchanged content keeps what indexing stored on the record.

A sync that saves such a record again publishes nothing for it, so indexing never
writes its statuses and checksum back. ``_process_record`` takes them from the row
it read in the transaction, the same row whose status decides the skip. This holds
for every outcome indexing reached (a sync is not a retry) and for work already
queued or running (another event would index it twice).
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
    INDEXING_OUTCOME_STATUSES,
    INDEXING_PENDING_STATUSES,
    INDEXING_REOPENED_STATUSES,
    DataSourceEntitiesProcessor,
    _requests_index_event,
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


def _incoming(revision: str = "rev-1", status: str | None = None) -> FileRecord:
    record = FileRecord(
        org_id="org-1", record_name="a.pdf", record_type=RecordType.FILE, external_record_id="ext-1",
        external_revision_id=revision, version=0, origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.GOOGLE_DRIVE, connector_id="c1", is_file=True,
    )
    if status is not None:
        record.indexing_status = status
    return record


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


def test_every_status_has_one_rule_for_an_unchanged_save() -> None:
    """A status added without a rule would be reset, and queued again, on every sync."""
    groups = (INDEXING_OUTCOME_STATUSES, INDEXING_PENDING_STATUSES, INDEXING_REOPENED_STATUSES)
    assert set().union(*groups) == {status.value for status in ProgressStatus}
    assert sum(len(group) for group in groups) == len(ProgressStatus)


@pytest.mark.parametrize("status", sorted(INDEXING_OUTCOME_STATUSES | INDEXING_PENDING_STATUSES))
async def test_unchanged_record_keeps_its_outcome_or_pending_work(status: str) -> None:
    stored = _stored(status, reason="what indexing found", parsing_status=status)
    processed, _ = await _processor()._process_record(_incoming(), [], _store(stored))

    assert processed.indexing_status == status
    assert processed.parsing_status == status
    assert processed.reason == "what indexing found"
    assert processed.md5_hash == "stored-md5"
    assert processed.storage_document_id == "doc-1"


@pytest.mark.parametrize("status", sorted(INDEXING_OUTCOME_STATUSES | INDEXING_PENDING_STATUSES))
async def test_changed_record_is_indexed_again_whatever_it_held(status: str) -> None:
    processed, _ = await _processor()._process_record(_incoming("rev-2"), [], _store(_stored(status)))

    assert processed.indexing_status == ProgressStatus.NOT_STARTED.value
    assert processed.md5_hash is None


@pytest.mark.parametrize("status", sorted(INDEXING_REOPENED_STATUSES))
async def test_unchanged_record_not_yet_indexed_takes_the_status_the_save_asks_for(status: str) -> None:
    """Nothing indexed it, so the connector's QUEUED default asks for an event (stored NOT_STARTED
    until it is acknowledged): turning a filter back on, or a lost event, is answered by the sync."""
    processed, _ = await _processor()._process_record(_incoming(), [], _store(_stored(status)))

    assert processed.indexing_status == ProgressStatus.NOT_STARTED.value


async def test_turning_auto_indexing_off_stops_an_unchanged_record_not_yet_started() -> None:
    stored = _stored(ProgressStatus.QUEUED.value)
    incoming = _incoming(status=ProgressStatus.AUTO_INDEX_OFF.value)
    processed, _ = await _processor()._process_record(incoming, [], _store(stored))

    assert processed.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


@pytest.mark.parametrize(
    "status", sorted(INDEXING_OUTCOME_STATUSES | {ProgressStatus.IN_PROGRESS.value})
)
async def test_turning_auto_indexing_off_keeps_what_indexing_reached(status: str) -> None:
    incoming = _incoming(status=ProgressStatus.AUTO_INDEX_OFF.value)
    processed, _ = await _processor()._process_record(incoming, [], _store(_stored(status)))

    assert processed.indexing_status == status


async def test_unchanged_queued_record_keeps_its_queued_at() -> None:
    """The stranded-record sweep ages a QUEUED row on queuedAt: stamping it on every sync
    would keep postponing the recovery of a record whose event was lost."""
    stored = _stored(ProgressStatus.QUEUED.value, queued_at=1_000)
    processed, _ = await _processor()._process_record(_incoming(), [], _store(stored))

    assert processed.queued_at in (None, 1_000)


async def test_unchanged_record_restored_for_reindexing_starts_clean() -> None:
    existing = _stored(
        ProgressStatus.COMPLETED.value, is_deleted=True,
        delete_source=DeleteSource.CONNECTOR, delete_batch_id="b-1",
    )
    processed, _ = await _processor()._process_record(_incoming(), [], _store(existing))

    assert processed.indexing_status == ProgressStatus.NOT_STARTED.value
    assert processed.parsing_status == ProgressStatus.NOT_STARTED.value
    assert processed.md5_hash is None


async def test_unchanged_password_protected_record_keeps_its_verdict_and_reason() -> None:
    stored = _stored(
        ProgressStatus.PASSWORD_PROTECTED.value, reason="This file is password-protected.",
    )
    processed, _ = await _processor()._process_record(_incoming(), [], _store(stored))

    assert processed.indexing_status == ProgressStatus.PASSWORD_PROTECTED.value
    assert processed.reason == "This file is password-protected."
    assert processed.md5_hash == "stored-md5"


async def test_changed_password_protected_record_starts_clean() -> None:
    stored = _stored(ProgressStatus.PASSWORD_PROTECTED.value, reason="This file is password-protected.")
    processed, _ = await _processor()._process_record(_incoming("rev-2"), [], _store(stored))

    assert processed.indexing_status != ProgressStatus.PASSWORD_PROTECTED.value
    assert processed.md5_hash is None


@pytest.mark.parametrize(
    ("asked", "expected"),
    [
        (None, ProgressStatus.NOT_STARTED.value),
        (ProgressStatus.AUTO_INDEX_OFF.value, ProgressStatus.AUTO_INDEX_OFF.value),
    ],
)
async def test_unchanged_unsupported_record_that_gains_a_supported_type_is_reopened(
    asked: str | None, expected: str
) -> None:
    stored = _stored(ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value, mime_type="application/octet-stream")
    incoming = _incoming(status=asked)
    incoming.mime_type = "application/pdf"
    processed, _ = await _processor()._process_record(incoming, [], _store(stored))

    assert processed.indexing_status == expected
    assert _requests_index_event(processed) is (expected == ProgressStatus.NOT_STARTED.value)
