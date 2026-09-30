"""The record handler's storage step: a deleted record's own upload, and deleteStoredDocuments."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import EventTypes
from app.exceptions.indexing_exceptions import ProcessingError
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

        pipeline.bulk_delete_embeddings.assert_awaited_once_with(["vr1"], org_id="org-1")
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
    async def test_a_file_still_listed_is_rescheduled_with_a_delay_not_retried(self):
        """No wait holding a permit, no delivery attempt spent: a fresh, delayed event."""
        handler, pipeline = _handler()
        other = "65f1c0ffee0123456789abce"
        handler.event_processor.graph_provider.get_uploaded_document_ids = AsyncMock(return_value=[other])
        scheduled = get_epoch_timestamp_in_ms() - 60_000
        before = time.time()

        events = await _run(
            handler,
            EventTypes.DELETE_STORED_DOCUMENTS.value,
            {"orgId": "org-1", "connectorId": "kb-1", "documentIds": [DOC_ID, other],
             "scheduledAt": scheduled, "_retry_tracking_id": "old"},
        )

        assert len(events) == 2
        handler.event_processor.graph_provider.get_uploaded_document_ids.assert_awaited_once_with(
            "kb-1", among=[DOC_ID, other]
        )
        pipeline.purge_stored_documents.assert_awaited_once_with("org-1", [DOC_ID])
        sent = handler.producer.send_event.await_args.kwargs
        assert sent["event_type"] == EventTypes.DELETE_STORED_DOCUMENTS.value
        payload = sent["payload"]
        assert payload["documentIds"] == [other]
        assert payload["scheduledAt"] == scheduled
        assert payload["reschedules"] == 1
        assert "_retry_tracking_id" not in payload
        assert before + 15 <= payload["_retry_not_before"] <= time.time() + 15

    @pytest.mark.asyncio
    async def test_the_delay_grows_and_is_capped(self):
        handler, _ = _handler()
        handler.event_processor.graph_provider.get_uploaded_document_ids = AsyncMock(return_value=[DOC_ID])

        await _run(
            handler,
            EventTypes.DELETE_STORED_DOCUMENTS.value,
            {"orgId": "org-1", "connectorId": "kb-1", "documentIds": [DOC_ID], "reschedules": 9},
        )

        payload = handler.producer.send_event.await_args.kwargs["payload"]
        assert payload["reschedules"] == 10
        assert payload["_retry_not_before"] - time.time() <= 300

    @pytest.mark.asyncio
    async def test_an_unreadable_graph_reschedules_every_file(self):
        """A failed read must not spend attempts: the ids are the only handle on the files."""
        handler, pipeline = _handler()
        handler.event_processor.graph_provider.get_uploaded_document_ids = AsyncMock(side_effect=RuntimeError("timeout"))

        await _run(
            handler,
            EventTypes.DELETE_STORED_DOCUMENTS.value,
            {"orgId": "org-1", "connectorId": "kb-1", "documentIds": [DOC_ID]},
        )

        pipeline.purge_stored_documents.assert_not_awaited()
        assert handler.producer.send_event.await_args.kwargs["payload"]["documentIds"] == [DOC_ID]

    @pytest.mark.asyncio
    async def test_after_a_day_a_still_listed_file_is_kept_and_not_rescheduled(self, monkeypatch):
        """Records that still list the file a day on were never deleted; the file is theirs."""
        handler, pipeline = _handler()
        handler.event_processor.graph_provider.get_uploaded_document_ids = AsyncMock(return_value=[DOC_ID])
        long_ago = get_epoch_timestamp_in_ms() - 25 * 3600 * 1000

        await _run(
            handler,
            EventTypes.DELETE_STORED_DOCUMENTS.value,
            {"orgId": "org-1", "connectorId": "kb-1", "documentIds": [DOC_ID], "scheduledAt": long_ago},
        )

        handler.producer.send_event.assert_not_awaited()
        pipeline.purge_stored_documents.assert_not_awaited()

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
    async def test_files_storage_could_not_remove_are_rescheduled_with_a_delay(self):
        """Raising would spend the delivery attempts and then discard the ids for good."""
        handler, pipeline = _handler()
        pipeline.purge_stored_documents = AsyncMock(return_value=[DOC_ID])

        events = await _run(handler, EventTypes.DELETE_STORED_DOCUMENTS.value, {"orgId": "org-1", "documentIds": [DOC_ID]})

        assert len(events) == 2
        assert handler.producer.send_event.await_args.kwargs["payload"]["_retry_not_before"] > time.time()
        sent = handler.producer.send_event.await_args.kwargs
        assert sent["payload"]["documentIds"] == [DOC_ID]
        assert isinstance(sent["payload"]["scheduledAt"], int)

    @pytest.mark.asyncio
    async def test_an_event_without_an_org_is_dead_lettered_at_once(self):
        handler, _ = _handler()

        with pytest.raises(ProcessingError):
            await _run(handler, EventTypes.DELETE_STORED_DOCUMENTS.value, {"documentIds": [DOC_ID]})


class TestStorageFailuresAreNeverDropped:
    @pytest.mark.asyncio
    async def test_after_a_day_a_file_storage_could_not_remove_is_still_rescheduled(self):
        """Its record is gone; the event holds the only copy of its id."""
        handler, pipeline = _handler()
        pipeline.purge_stored_documents = AsyncMock(return_value=[DOC_ID])
        long_ago = get_epoch_timestamp_in_ms() - 25 * 3600 * 1000

        await _run(
            handler,
            EventTypes.DELETE_STORED_DOCUMENTS.value,
            {"orgId": "org-1", "documentIds": [DOC_ID], "scheduledAt": long_ago},
        )

        assert handler.producer.send_event.await_args.kwargs["payload"]["documentIds"] == [DOC_ID]

    @pytest.mark.asyncio
    async def test_after_a_day_an_unreadable_graph_still_reschedules(self):
        """A failed read is no evidence the delete never happened."""
        handler, pipeline = _handler()
        handler.event_processor.graph_provider.get_uploaded_document_ids = AsyncMock(side_effect=RuntimeError("timeout"))
        long_ago = get_epoch_timestamp_in_ms() - 25 * 3600 * 1000

        await _run(
            handler,
            EventTypes.DELETE_STORED_DOCUMENTS.value,
            {"orgId": "org-1", "connectorId": "kb-1", "documentIds": [DOC_ID], "scheduledAt": long_ago},
        )

        pipeline.purge_stored_documents.assert_not_awaited()
        assert handler.producer.send_event.await_args.kwargs["payload"]["documentIds"] == [DOC_ID]
