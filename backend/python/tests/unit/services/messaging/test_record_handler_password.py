"""A file that needs a password ends PASSWORD_PROTECTED, not FAILED."""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    EventTypes,
    OriginTypes,
    ProgressStatus,
)
from app.exceptions.indexing_exceptions import (
    DocumentProcessingError,
    PasswordProtectedError,
)
from app.services.messaging.kafka.handlers.record import RecordEventHandler
from app.utils.user_errors import PASSWORD_PROTECTED
from tests.unit.services.messaging.test_record_handler import (
    _collect_events,
    _make_handler,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

PAYLOAD = {
    "recordId": "r1", "orgId": "org-1", "virtualRecordId": "vr1",
    "mimeType": "application/pdf", "extension": "pdf",
    "signedUrl": "https://example.com/file.pdf",
}


def _failing_record(handler, error: Exception, **extra) -> dict:
    gp = handler.event_processor.graph_provider
    record = {
        "_key": "r1", "virtualRecordId": "vr1", "orgId": "org-1", "md5Checksum": "m1",
        "origin": OriginTypes.UPLOAD.value, "indexingStatus": ProgressStatus.QUEUED.value,
        "mimeType": "application/pdf", **extra,
    }
    connector = {"_key": "conn-1", "name": "Acme Drive", "isActive": True, "isAgentActive": False}

    async def get_document(_doc_id, collection, **_k) -> dict:
        return dict(connector) if collection == CollectionNames.APPS.value else dict(record)

    gp.get_document = AsyncMock(side_effect=get_document)

    async def update_node(_doc_id, _collection, fields) -> bool:
        record.update(fields)
        return True

    gp.update_node = AsyncMock(side_effect=update_node)
    gp.update_queued_duplicates_status = AsyncMock(return_value=0)

    async def on_event(*_a, **_k) -> AsyncGenerator:
        record["indexingStatus"] = ProgressStatus.IN_PROGRESS.value
        yield {"event": "parsing_complete", "data": {"record_id": "r1"}}
        raise error

    handler.event_processor.on_event = MagicMock(side_effect=on_event)
    return record


async def _run(handler, *, final: bool | None = None) -> None:
    payload = dict(PAYLOAD) if final is None else {**PAYLOAD, "is_final_failure": final}
    with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock, return_value=b"pdf"):
        with pytest.raises(Exception):  # noqa: B017 - the handler re-raises for the consumer
            await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)


@pytest.mark.asyncio
async def test_an_upload_that_needs_a_password_ends_password_protected_with_the_upload_reason() -> None:
    handler = _make_handler()
    record = _failing_record(handler, PasswordProtectedError("needs a password"))

    # The first attempt: the error is terminal, so it does not wait for retries to run out.
    await _run(handler, final=False)

    assert record["indexingStatus"] == ProgressStatus.PASSWORD_PROTECTED.value
    assert record["reason"] == PASSWORD_PROTECTED
    assert "upload it again" in record["reason"]


@pytest.mark.asyncio
async def test_a_locked_record_ends_with_all_three_status_fields_as_written() -> None:
    """Both pipelines set parsingStatus IN_PROGRESS before parsing, and the handler copies the final
    indexing status onto it. The rollback AQL (docs 2.5) has to know every field this reaches.
    Classification does not apply to a file no one can read, so it is SKIPPED, not a failure."""
    handler = _make_handler()
    record = _failing_record(
        handler, PasswordProtectedError("needs a password"),
        parsingStatus=ProgressStatus.IN_PROGRESS.value, extractionStatus=ProgressStatus.NOT_STARTED.value,
    )

    await _run(handler, final=True)

    assert {k: record.get(k) for k in ("indexingStatus", "parsingStatus", "extractionStatus")} == {
        "indexingStatus": ProgressStatus.PASSWORD_PROTECTED.value,
        "parsingStatus": ProgressStatus.PASSWORD_PROTECTED.value,
        "extractionStatus": ProgressStatus.SKIPPED.value,
    }
    assert record["processingStartedAt"] is None


@pytest.mark.asyncio
async def test_an_ordinary_failure_still_fails_classification_too() -> None:
    handler = _make_handler()
    record = _failing_record(handler, DocumentProcessingError("could not parse"), parsingStatus="IN_PROGRESS")

    await _run(handler, final=True)

    assert record["extractionStatus"] == ProgressStatus.FAILED.value
    assert record["parsingStatus"] == ProgressStatus.FAILED.value


@pytest.mark.asyncio
async def test_a_synced_item_gets_the_synced_wording() -> None:
    handler = _make_handler()
    record = _failing_record(
        handler, PasswordProtectedError("needs a password"),
        origin=OriginTypes.CONNECTOR.value, recordType="FILE", connectorName="DRIVE", connectorId="conn-1",
    )

    await _run(handler, final=True)

    assert record["indexingStatus"] == ProgressStatus.PASSWORD_PROTECTED.value
    assert "password-protected" in record["reason"]
    assert "upload it again" not in record["reason"]
    assert "Acme Drive" in record["reason"]
    assert "next sync" in record["reason"]


@pytest.mark.asyncio
async def test_queued_duplicates_get_the_same_status_not_failed() -> None:
    handler = _make_handler()
    _failing_record(handler, PasswordProtectedError("needs a password"))
    gp = handler.event_processor.graph_provider

    await _run(handler, final=True)

    gp.update_queued_duplicates_status.assert_awaited_once()
    args, kwargs = gp.update_queued_duplicates_status.call_args
    assert args[:3] == ("r1", ProgressStatus.PASSWORD_PROTECTED.value, "vr1")
    assert "password-protected" in kwargs["reason"]
    assert "failed to index" not in kwargs["reason"]


@pytest.mark.asyncio
async def test_an_ordinary_failure_still_ends_failed_and_fails_its_duplicates() -> None:
    handler = _make_handler()
    record = _failing_record(handler, DocumentProcessingError("could not parse"))
    gp = handler.event_processor.graph_provider

    await _run(handler, final=True)

    assert record["indexingStatus"] == ProgressStatus.FAILED.value
    assert gp.update_queued_duplicates_status.call_args.args[1] == ProgressStatus.FAILED.value


@pytest.mark.asyncio
async def test_error_text_that_mentions_a_password_does_not_make_a_file_password_protected() -> None:
    handler = _make_handler()
    record = _failing_record(
        handler, DocumentProcessingError("Failed to process: the password column is missing"),
    )

    await _run(handler, final=True)

    assert record["indexingStatus"] == ProgressStatus.FAILED.value


@pytest.mark.asyncio
async def test_the_typed_error_wrapped_by_another_is_still_found() -> None:
    handler = _make_handler()
    try:
        raise PasswordProtectedError("needs a password")
    except PasswordProtectedError as inner:
        wrapped = DocumentProcessingError("Failed to process document")
        wrapped.__cause__ = inner
    record = _failing_record(handler, wrapped)

    await _run(handler, final=True)

    assert record["indexingStatus"] == ProgressStatus.PASSWORD_PROTECTED.value


def test_a_settled_record_is_not_rewritten_by_a_duplicate_delivery() -> None:
    assert ProgressStatus.PASSWORD_PROTECTED.value in RecordEventHandler._SETTLED_STATUSES

