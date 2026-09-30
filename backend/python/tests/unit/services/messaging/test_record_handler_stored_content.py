"""The record handler's storage step: a deleted record's own upload, and deleteStoredDocuments."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import EventTypes
from app.exceptions.indexing_exceptions import IndexingError, ProcessingError
from app.utils.time_conversion import get_epoch_timestamp_in_ms

DOC_ID = "65f1c0ffee0123456789abcd"


def _handler():
    from app.services.messaging.kafka.handlers.record import RecordEventHandler

    event_processor = MagicMock()
    event_processor.graph_provider = AsyncMock()
    event_processor.processor = MagicMock()
    pipeline = AsyncMock()
    pipeline.bulk_delete_embeddings = AsyncMock(return_value={"success": True})
    pipeline.purge_stored_documents = AsyncMock(return_value=[])
    event_processor.processor.indexing_pipeline = pipeline
    handler = RecordEventHandler(
        logger=MagicMock(), config_service=AsyncMock(), event_processor=event_processor, producer=AsyncMock()
    )
    return handler, pipeline


async def _run(handler, event_type, payload):
    return [event async for event in handler.process_event(event_type, payload)]


class TestDeleteRecordLeavesStorageToItsOwnEvent:
    @pytest.mark.asyncio
    async def test_a_record_delete_touches_only_the_vectors(self):
        """Uploads are scheduled before the graph delete, as deleteStoredDocuments."""
        handler, pipeline = _handler()

        await _run(handler, EventTypes.DELETE_RECORD.value, {"recordId": "r1", "orgId": "org-1", "virtualRecordId": "vr1"})

        pipeline.bulk_delete_embeddings.assert_awaited_once_with(["vr1"])
        pipeline.purge_stored_documents.assert_not_awaited()


class TestDeleteStoredDocumentsEvent:
    @pytest.mark.asyncio
    async def test_purges_the_listed_documents(self):
        handler, pipeline = _handler()

        events = await _run(
            handler, EventTypes.DELETE_STORED_DOCUMENTS.value, {"orgId": "org-1", "documentIds": [DOC_ID]}
        )

        assert [e.event for e in events] == ["parsing_complete", "indexing_complete"]
        pipeline.purge_stored_documents.assert_awaited_once_with("org-1", [DOC_ID])

    @pytest.mark.asyncio
    async def test_it_waits_for_the_records_delete_without_spending_an_attempt(self, monkeypatch):
        """The event is published before the graph delete, so the records are usually still there."""
        handler, pipeline = _handler()
        listings = iter([[DOC_ID], [DOC_ID], []])
        handler.event_processor.graph_provider.get_uploaded_document_ids = AsyncMock(
            side_effect=lambda *a, **k: next(listings)
        )
        monkeypatch.setattr("app.services.messaging.kafka.handlers.record.asyncio.sleep", AsyncMock())

        events = await _run(
            handler, EventTypes.DELETE_STORED_DOCUMENTS.value,
            {"orgId": "org-1", "connectorId": "kb-1", "documentIds": [DOC_ID]},
        )

        assert len(events) == 2
        assert handler.event_processor.graph_provider.get_uploaded_document_ids.await_count == 3
        pipeline.purge_stored_documents.assert_awaited_once_with("org-1", [DOC_ID])

    @pytest.mark.asyncio
    async def test_a_file_still_listed_after_the_wait_is_rescheduled_not_retried(self, monkeypatch):
        """No delivery attempt is spent, so a slow graph delete cannot lose the ids."""
        handler, pipeline = _handler()
        other = "65f1c0ffee0123456789abce"
        handler.event_processor.graph_provider.get_uploaded_document_ids = AsyncMock(return_value=[other])
        monkeypatch.setenv("STORED_DOCUMENTS_WAIT_SECONDS", "0")
        scheduled = get_epoch_timestamp_in_ms() - 60_000

        events = await _run(
            handler,
            EventTypes.DELETE_STORED_DOCUMENTS.value,
            {"orgId": "org-1", "connectorId": "kb-1", "documentIds": [DOC_ID, other], "scheduledAt": scheduled},
        )

        assert len(events) == 2
        pipeline.purge_stored_documents.assert_awaited_once_with("org-1", [DOC_ID])
        handler.producer.send_event.assert_awaited_once()
        sent = handler.producer.send_event.await_args.kwargs
        assert sent["event_type"] == EventTypes.DELETE_STORED_DOCUMENTS.value
        assert sent["payload"] == {
            "orgId": "org-1", "connectorId": "kb-1", "documentIds": [other], "scheduledAt": scheduled,
        }

    @pytest.mark.asyncio
    async def test_after_a_day_a_still_listed_file_is_kept_and_not_rescheduled(self, monkeypatch):
        """Records that still list the file a day on were never deleted; the file is theirs."""
        handler, pipeline = _handler()
        handler.event_processor.graph_provider.get_uploaded_document_ids = AsyncMock(return_value=[DOC_ID])
        monkeypatch.setenv("STORED_DOCUMENTS_WAIT_SECONDS", "0")
        long_ago = get_epoch_timestamp_in_ms() - 25 * 3600 * 1000

        await _run(
            handler,
            EventTypes.DELETE_STORED_DOCUMENTS.value,
            {"orgId": "org-1", "connectorId": "kb-1", "documentIds": [DOC_ID], "scheduledAt": long_ago},
        )

        handler.producer.send_event.assert_not_awaited()
        pipeline.purge_stored_documents.assert_awaited_once_with("org-1", [])

    @pytest.mark.asyncio
    async def test_once_the_records_are_gone_every_file_is_purged(self):
        handler, pipeline = _handler()
        handler.event_processor.graph_provider.get_uploaded_document_ids = AsyncMock(return_value=[])

        await _run(
            handler,
            EventTypes.DELETE_STORED_DOCUMENTS.value,
            {"orgId": "org-1", "connectorId": "kb-1", "documentIds": [DOC_ID]},
        )

        pipeline.purge_stored_documents.assert_awaited_once_with("org-1", [DOC_ID])

    @pytest.mark.asyncio
    async def test_documents_still_stored_raise_for_a_retry(self):
        handler, pipeline = _handler()
        pipeline.purge_stored_documents = AsyncMock(return_value=[DOC_ID])

        with pytest.raises(IndexingError):
            await _run(handler, EventTypes.DELETE_STORED_DOCUMENTS.value, {"orgId": "org-1", "documentIds": [DOC_ID]})

    @pytest.mark.asyncio
    async def test_an_event_without_an_org_is_dead_lettered_at_once(self):
        handler, _ = _handler()

        with pytest.raises(ProcessingError):
            await _run(handler, EventTypes.DELETE_STORED_DOCUMENTS.value, {"documentIds": [DOC_ID]})
