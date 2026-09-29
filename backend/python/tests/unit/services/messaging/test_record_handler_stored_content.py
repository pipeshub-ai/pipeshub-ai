"""The record handler's storage step: a deleted record's own upload, and deleteStoredDocuments."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import EventTypes
from app.exceptions.indexing_exceptions import IndexingError, ProcessingError

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


class TestDeletedRecordsUpload:
    @pytest.mark.asyncio
    async def test_the_vectors_go_then_the_upload(self):
        handler, pipeline = _handler()
        payload = {"recordId": "r1", "orgId": "org-1", "virtualRecordId": "vr1", "uploadDocumentId": DOC_ID}

        events = await _run(handler, EventTypes.DELETE_RECORD.value, payload)

        assert len(events) == 2
        pipeline.bulk_delete_embeddings.assert_awaited_once_with(["vr1"])
        pipeline.purge_stored_documents.assert_awaited_once_with("org-1", [DOC_ID])

    @pytest.mark.asyncio
    async def test_a_record_without_an_upload_touches_no_storage(self):
        handler, pipeline = _handler()

        await _run(handler, EventTypes.DELETE_RECORD.value, {"recordId": "r1", "orgId": "org-1", "virtualRecordId": "vr1"})

        pipeline.purge_stored_documents.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_upload_still_stored_fails_the_event_so_it_is_retried(self):
        handler, pipeline = _handler()
        pipeline.purge_stored_documents = AsyncMock(return_value=[DOC_ID])
        payload = {"recordId": "r1", "orgId": "org-1", "virtualRecordId": "vr1", "uploadDocumentId": DOC_ID}

        with pytest.raises(IndexingError):
            await _run(handler, EventTypes.DELETE_RECORD.value, payload)


    @pytest.mark.asyncio
    async def test_an_upload_without_an_org_is_dead_lettered_not_skipped(self):
        handler, pipeline = _handler()

        with pytest.raises(ProcessingError):
            await _run(handler, EventTypes.DELETE_RECORD.value, {"recordId": "r1", "virtualRecordId": "vr1", "uploadDocumentId": DOC_ID})
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
    async def test_a_file_a_record_still_lists_is_kept_and_retried(self):
        """Published before the graph delete: the records may still be there."""
        handler, pipeline = _handler()
        other = "65f1c0ffee0123456789abce"
        handler.event_processor.graph_provider.get_uploaded_document_ids = AsyncMock(return_value=[other])

        with pytest.raises(IndexingError):
            await _run(
                handler,
                EventTypes.DELETE_STORED_DOCUMENTS.value,
                {"orgId": "org-1", "connectorId": "kb-1", "documentIds": [DOC_ID, other]},
            )

        handler.event_processor.graph_provider.get_uploaded_document_ids.assert_awaited_once_with("kb-1")
        pipeline.purge_stored_documents.assert_awaited_once_with("org-1", [DOC_ID])

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
