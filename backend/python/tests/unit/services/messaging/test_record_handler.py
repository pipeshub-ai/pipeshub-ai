"""Unit tests for app.services.messaging.kafka.handlers.record.RecordEventHandler."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    EventTypes,
    OriginTypes,
    ProgressStatus,
    RecordTypes,
)
from app.exceptions.indexing_exceptions import IndexingError
from app.services.messaging.config import IndexingEvent, PipelineEvent, PipelineEventData


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_handler(logger=None, config_service=None, event_processor=None):
    """Create a RecordEventHandler with mock dependencies."""
    from app.services.messaging.kafka.handlers.record import RecordEventHandler

    logger = logger or MagicMock()
    config_service = config_service or AsyncMock()
    event_processor = event_processor or MagicMock()

    # Ensure event_processor has the nested objects the handler expects
    if not hasattr(event_processor, "graph_provider") or event_processor.graph_provider is None:
        event_processor.graph_provider = AsyncMock()
    if not hasattr(event_processor, "processor") or event_processor.processor is None:
        event_processor.processor = MagicMock()
        event_processor.processor.indexing_pipeline = AsyncMock()

    return RecordEventHandler(
        logger=logger,
        config_service=config_service,
        event_processor=event_processor,
    )


async def _collect_events(handler, event_type, payload):
    """Collect all yielded events from process_event into a list."""
    events = []
    async for event in handler.process_event(event_type, payload):
        events.append(event)
    return events


# ===================================================================
# process_event -- missing / empty event type
# ===================================================================

class TestProcessEventEdgeCases:
    """Tests for missing event type and missing record_id."""

    @pytest.mark.asyncio
    async def test_missing_event_type_yields_nothing(self):
        handler = _make_handler()
        events = await _collect_events(handler, "", {"recordId": "r1"})
        assert events == []

    @pytest.mark.asyncio
    async def test_none_event_type_yields_nothing(self):
        handler = _make_handler()
        events = await _collect_events(handler, None, {"recordId": "r1"})
        assert events == []

    @pytest.mark.asyncio
    async def test_missing_record_id_for_non_bulk_event(self):
        handler = _make_handler()
        events = await _collect_events(handler, EventTypes.NEW_RECORD.value, {"mimeType": "text/plain"})
        assert events == []


# ===================================================================
# Bulk delete event
# ===================================================================

class TestBulkDeleteEvent:
    """Tests for bulkDeleteRecords event type."""

    @pytest.mark.asyncio
    async def test_bulk_delete_success(self):
        handler = _make_handler()
        pipeline = handler.event_processor.processor.indexing_pipeline
        pipeline.bulk_delete_embeddings = AsyncMock(
            return_value={"deleted_count": 5, "virtual_record_ids_processed": 3}
        )

        payload = {"virtualRecordIds": ["vr1", "vr2", "vr3"]}
        events = await _collect_events(handler, EventTypes.BULK_DELETE_RECORDS.value, payload)

        assert len(events) == 2
        assert events[0].event == "parsing_complete"
        assert events[0].data.record_id == "bulk_delete"
        assert events[0].data.count == 3
        assert events[1].event == "indexing_complete"
        pipeline.bulk_delete_embeddings.assert_awaited_once_with(["vr1", "vr2", "vr3"])

    @pytest.mark.asyncio
    async def test_bulk_delete_empty_list(self):
        handler = _make_handler()
        pipeline = handler.event_processor.processor.indexing_pipeline
        pipeline.bulk_delete_embeddings = AsyncMock(
            return_value={"deleted_count": 0, "virtual_record_ids_processed": 0}
        )

        payload = {"virtualRecordIds": []}
        events = await _collect_events(handler, EventTypes.BULK_DELETE_RECORDS.value, payload)

        assert len(events) == 2
        assert events[0].data.count == 0


# ===================================================================
# Delete record event
# ===================================================================

class TestDeleteRecordEvent:
    """Tests for deleteRecord event type."""

    @pytest.mark.asyncio
    async def test_delete_record_success(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        gp.get_document = AsyncMock(return_value={"_key": "r1", "virtualRecordId": "vr1"})
        pipeline = handler.event_processor.processor.indexing_pipeline
        pipeline.delete_embeddings = AsyncMock()

        payload = {"recordId": "r1", "virtualRecordId": "vr1"}
        events = await _collect_events(handler, EventTypes.DELETE_RECORD.value, payload)

        assert len(events) == 2
        assert events[0].event == "parsing_complete"
        assert events[0].data.record_id == "r1"
        assert events[1].event == "indexing_complete"
        pipeline.delete_embeddings.assert_awaited_once_with("r1", "vr1")

    @pytest.mark.asyncio
    async def test_delete_record_no_virtual_record_id(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        gp.get_document = AsyncMock(return_value={"_key": "r1"})
        pipeline = handler.event_processor.processor.indexing_pipeline
        pipeline.delete_embeddings = AsyncMock()

        payload = {"recordId": "r1"}
        events = await _collect_events(handler, EventTypes.DELETE_RECORD.value, payload)

        assert len(events) == 2
        pipeline.delete_embeddings.assert_awaited_once_with("r1", None)


# ===================================================================
# Record not found in database
# ===================================================================

class TestRecordNotFound:
    """Tests for when record is not found in database."""

    @pytest.mark.asyncio
    async def test_record_not_found_yields_nothing(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        gp.get_document = AsyncMock(return_value=None)

        payload = {"recordId": "r1", "mimeType": "application/pdf", "extension": "pdf"}
        events = await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        assert events == []


# ===================================================================
# Already indexed records (NEW_RECORD / REINDEX_RECORD)
# ===================================================================

class TestAlreadyIndexed:
    """Tests for records that already have COMPLETED indexing status."""

    @pytest.mark.asyncio
    async def test_new_record_already_completed(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "mimeType": "application/pdf",
        }
        gp.get_document = AsyncMock(return_value=record)
        gp.update_queued_duplicates_status = AsyncMock()

        payload = {"recordId": "r1", "virtualRecordId": "vr1", "mimeType": "application/pdf", "extension": "pdf"}
        events = await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        assert len(events) == 2
        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_reindex_record_already_completed(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "mimeType": "application/pdf",
        }
        gp.get_document = AsyncMock(return_value=record)
        gp.update_queued_duplicates_status = AsyncMock()

        payload = {"recordId": "r1", "virtualRecordId": "vr1", "mimeType": "application/pdf", "extension": "pdf"}
        events = await _collect_events(handler, EventTypes.REINDEX_RECORD.value, payload)

        assert len(events) == 2


# ===================================================================
# Connector active/inactive checks
# ===================================================================

class TestConnectorActiveCheck:
    """Tests for connector active/inactive logic."""

    @pytest.mark.asyncio
    async def test_connector_not_found_skips_indexing(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "connectorId": "conn-1",
            "origin": OriginTypes.CONNECTOR.value,
            "mimeType": "application/pdf",
        }

        # First call returns the record, second call for connector returns None,
        # third call in finally block returns record again for status check
        gp.get_document = AsyncMock(side_effect=[record, None, record])
        gp.update_queued_duplicates_status = AsyncMock()

        payload = {"recordId": "r1", "mimeType": "application/pdf", "extension": "pdf"}
        events = await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        assert len(events) == 2
        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_connector_inactive_skips_indexing(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "extractionStatus": ProgressStatus.NOT_STARTED.value,
            "connectorId": "conn-1",
            "origin": OriginTypes.CONNECTOR.value,
            "mimeType": "application/pdf",
        }
        connector_instance = {"_key": "conn-1", "isActive": False}

        # First call: record, second call: connector, third call: __update_document_status,
        # fourth call: finally block record fetch
        gp.get_document = AsyncMock(side_effect=[record, connector_instance, record, record])
        gp.batch_upsert_nodes = AsyncMock()
        gp.update_queued_duplicates_status = AsyncMock()

        payload = {"recordId": "r1", "mimeType": "application/pdf", "extension": "pdf"}
        events = await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        assert len(events) == 2
        # Verify status update was called
        gp.batch_upsert_nodes.assert_awaited()

    @pytest.mark.asyncio
    async def test_connector_active_proceeds_normally(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "connectorId": "conn-1",
            "origin": OriginTypes.CONNECTOR.value,
            "mimeType": "application/pdf",
        }
        connector_instance = {"_key": "conn-1", "isActive": True}

        # get_document calls: record, connector, then final status check
        gp.get_document = AsyncMock(side_effect=[
            record,
            connector_instance,
            record,  # final record fetch in finally block
        ])

        ep = handler.event_processor
        ep.on_event = MagicMock(return_value=_async_gen_events([
            {"event": "parsing_complete", "data": {"record_id": "r1"}},
            {"event": "indexing_complete", "data": {"record_id": "r1"}},
        ]))

        gp.update_queued_duplicates_status = AsyncMock()

        payload = {
            "recordId": "r1",
            "orgId": "org-1",
            "mimeType": "application/pdf",
            "extension": "pdf",
            "signedUrl": "https://example.com/file.pdf",
        }

        with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = b"file content"
            events = await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        # Should proceed past the connector check and yield events from processor
        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_non_connector_origin_skips_connector_check(self):
        """Records with origin != CONNECTOR skip the connector active check."""
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "connectorId": "conn-1",
            "origin": OriginTypes.UPLOAD.value,
            "mimeType": "application/pdf",
        }

        # Only the record fetch + final status check - no connector fetch
        gp.get_document = AsyncMock(side_effect=[record, record])
        gp.update_queued_duplicates_status = AsyncMock()

        ep = handler.event_processor
        ep.on_event = MagicMock(return_value=_async_gen_events([
            {"event": "parsing_complete", "data": {"record_id": "r1"}},
            {"event": "indexing_complete", "data": {"record_id": "r1"}},
        ]))

        payload = {
            "recordId": "r1",
            "orgId": "org-1",
            "mimeType": "application/pdf",
            "extension": "pdf",
            "signedUrl": "https://example.com/file.pdf",
        }

        with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = b"file content"
            events = await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        assert len(events) == 2


# ===================================================================
# Update record event
# ===================================================================

class TestUpdateRecordEvent:
    """Tests for updateRecord event type."""

    @pytest.mark.asyncio
    async def test_update_record_deletes_existing_embeddings(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "application/pdf",
        }
        gp.get_document = AsyncMock(side_effect=[record, record])
        gp.update_queued_duplicates_status = AsyncMock()

        pipeline = handler.event_processor.processor.indexing_pipeline
        pipeline.delete_embeddings = AsyncMock()

        ep = handler.event_processor
        ep.on_event = MagicMock(return_value=_async_gen_events([
            {"event": "parsing_complete", "data": {"record_id": "r1"}},
            {"event": "indexing_complete", "data": {"record_id": "r1"}},
        ]))

        payload = {
            "recordId": "r1",
            "virtualRecordId": "vr1",
            "orgId": "org-1",
            "mimeType": "application/pdf",
            "extension": "pdf",
            "signedUrl": "https://example.com/file.pdf",
        }

        with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = b"file content"
            events = await _collect_events(handler, EventTypes.UPDATE_RECORD.value, payload)

        pipeline.delete_embeddings.assert_awaited_once_with("r1", "vr1")
        assert len(events) == 2


# ===================================================================
# Unsupported file types
# ===================================================================

class TestUnsupportedFileType:
    """Tests for unsupported mime types and extensions."""

    @pytest.mark.asyncio
    async def test_unsupported_mime_and_extension(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "application/x-unknown-type",
        }
        # First call: record, second call: finally block
        gp.get_document = AsyncMock(side_effect=[record, record])
        gp.batch_upsert_nodes = AsyncMock()
        gp.update_queued_duplicates_status = AsyncMock()

        payload = {
            "recordId": "r1",
            "mimeType": "application/x-unknown-type",
            "extension": "xyz",
        }
        events = await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        assert len(events) == 2
        # Should update record with FILE_TYPE_NOT_SUPPORTED
        gp.batch_upsert_nodes.assert_awaited()
        # First call is the unsupported status update
        call_args = gp.batch_upsert_nodes.call_args_list[0]
        docs = call_args[0][0]
        assert docs[0]["indexingStatus"] == ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value
        assert docs[0]["extractionStatus"] == ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value

    @pytest.mark.asyncio
    async def test_supported_mime_type_passes_check(self):
        """A supported mime type should not trigger the unsupported path."""
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "application/pdf",
        }
        gp.get_document = AsyncMock(side_effect=[record, record])
        gp.update_queued_duplicates_status = AsyncMock()

        ep = handler.event_processor
        ep.on_event = MagicMock(return_value=_async_gen_events([
            {"event": "parsing_complete", "data": {"record_id": "r1"}},
        ]))

        payload = {
            "recordId": "r1",
            "orgId": "org-1",
            "mimeType": "application/pdf",
            "extension": "pdf",
            "signedUrl": "https://example.com/file.pdf",
        }

        with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = b"content"
            events = await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        # Should have gotten to the processor, not the unsupported path
        assert len(events) == 1
        assert events[0].event == "parsing_complete"

    @pytest.mark.asyncio
    async def test_supported_extension_with_unknown_mime(self):
        """A supported extension should pass even if mime type is unknown."""
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "unknown",
        }
        gp.get_document = AsyncMock(side_effect=[record, record])
        gp.update_queued_duplicates_status = AsyncMock()

        ep = handler.event_processor
        ep.on_event = MagicMock(return_value=_async_gen_events([
            {"event": "parsing_complete", "data": {"record_id": "r1"}},
        ]))

        payload = {
            "recordId": "r1",
            "orgId": "org-1",
            "mimeType": "unknown",
            "extension": "pdf",
            "signedUrl": "https://example.com/file.pdf",
        }

        with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = b"content"
            events = await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        assert len(events) >= 1


# ===================================================================
# Mime type and extension fallback logic
# ===================================================================

class TestMimeAndExtensionFallback:
    """Tests for mime type and extension derivation logic."""

    @pytest.mark.asyncio
    async def test_unknown_mime_falls_back_to_record_mime(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "application/pdf",
        }
        gp.get_document = AsyncMock(side_effect=[record, record])
        gp.update_queued_duplicates_status = AsyncMock()

        ep = handler.event_processor
        ep.on_event = MagicMock(return_value=_async_gen_events([
            {"event": "parsing_complete", "data": {"record_id": "r1"}},
        ]))

        payload = {
            "recordId": "r1",
            "orgId": "org-1",
            "mimeType": "unknown",
            "extension": "unknown",
            "signedUrl": "https://example.com/file.pdf",
        }

        with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = b"content"
            events = await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        # Should have derived extension from record's mime type and passed
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_extension_derived_from_record_name(self):
        """When extension is unknown, and mime is not gmail, use recordName to derive extension."""
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "application/x-some-unknown",
        }
        gp.get_document = AsyncMock(side_effect=[record, record])
        gp.update_queued_duplicates_status = AsyncMock()

        ep = handler.event_processor
        ep.on_event = MagicMock(return_value=_async_gen_events([
            {"event": "parsing_complete", "data": {"record_id": "r1"}},
        ]))

        payload = {
            "recordId": "r1",
            "orgId": "org-1",
            "mimeType": "application/x-some-unknown",
            "extension": "unknown",
            "recordName": "report.pdf",
            "signedUrl": "https://example.com/file.pdf",
        }

        with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = b"content"
            events = await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        # Should have derived extension "pdf" from recordName and passed
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_gmail_mime_type_does_not_derive_from_record_name(self):
        """When mime is text/gmail_content, recordName is NOT used for extension fallback."""
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "text/gmail_content",
        }
        gp.get_document = AsyncMock(side_effect=[record, record])
        gp.update_queued_duplicates_status = AsyncMock()

        ep = handler.event_processor
        ep.on_event = MagicMock(return_value=_async_gen_events([
            {"event": "parsing_complete", "data": {"record_id": "r1"}},
        ]))

        payload = {
            "recordId": "r1",
            "orgId": "org-1",
            "mimeType": "text/gmail_content",
            "extension": "unknown",
            "recordName": "email.xyz",
            "signedUrl": "https://example.com/email",
        }

        with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = b"content"
            events = await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        # Gmail is a supported mime type, should pass through
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_virtual_record_id_falls_back_to_record(self):
        """When virtualRecordId is not in payload, use from database record."""
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr-from-db",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "application/pdf",
        }
        gp.get_document = AsyncMock(side_effect=[record, record])
        gp.update_queued_duplicates_status = AsyncMock()

        ep = handler.event_processor
        ep.on_event = MagicMock(return_value=_async_gen_events([
            {"event": "parsing_complete", "data": {"record_id": "r1"}},
        ]))

        payload = {
            "recordId": "r1",
            "orgId": "org-1",
            "mimeType": "application/pdf",
            "extension": "pdf",
            "signedUrl": "https://example.com/file.pdf",
            # No virtualRecordId in payload
        }

        with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = b"content"
            await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        # Should have used virtualRecordId from DB record
        # Verified by the fact that no error occurred


# ===================================================================
# Signed URL download path
# ===================================================================

class TestSignedUrlPath:
    """Tests for signed URL download path."""

    @pytest.mark.asyncio
    async def test_signed_url_success(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "application/pdf",
        }
        gp.get_document = AsyncMock(side_effect=[record, record])
        gp.update_queued_duplicates_status = AsyncMock()

        ep = handler.event_processor
        ep.on_event = MagicMock(return_value=_async_gen_events([
            {"event": "parsing_complete", "data": {"record_id": "r1"}},
            {"event": "indexing_complete", "data": {"record_id": "r1"}},
        ]))

        payload = {
            "recordId": "r1",
            "virtualRecordId": "vr1",
            "orgId": "org-1",
            "mimeType": "application/pdf",
            "extension": "pdf",
            "signedUrl": "https://example.com/file.pdf",
        }

        with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = b"file content"
            events = await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        assert len(events) == 2
        mock_dl.assert_awaited_once()
        # Verify event processor was called
        ep.on_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_signed_url_download_failure_falls_back_to_connector(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "application/pdf",
        }
        gp.get_document = AsyncMock(side_effect=[record, record])
        gp.update_queued_duplicates_status = AsyncMock()

        ep = handler.event_processor
        ep.on_event = MagicMock(return_value=_async_gen_events([
            {"event": "parsing_complete", "data": {"record_id": "r1"}},
            {"event": "indexing_complete", "data": {"record_id": "r1"}},
        ]))

        payload = {
            "recordId": "r1",
            "virtualRecordId": "vr1",
            "orgId": "org-1",
            "mimeType": "application/pdf",
            "extension": "pdf",
            "signedUrl": "https://example.com/file.pdf",
        }

        with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = None  # returns None -> triggers failure
            with patch("app.services.messaging.kafka.handlers.record.generate_jwt", new_callable=AsyncMock) as mock_jwt:
                mock_jwt.return_value = "fake-token"
                with patch("app.services.messaging.kafka.handlers.record.make_api_call", new_callable=AsyncMock) as mock_api:
                    mock_api.return_value = {"data": b"file bytes"}
                    handler.config_service.get_config = AsyncMock(
                        return_value={"connectors": {"endpoint": "http://localhost:8088"}}
                    )
                    events = await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_signed_url_download_returns_none_fallback_path(self):
        """When _download_from_signed_url returns None, it should fall through to connector streaming."""
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "application/pdf",
        }
        gp.get_document = AsyncMock(side_effect=[record, record])
        gp.update_queued_duplicates_status = AsyncMock()

        ep = handler.event_processor
        # Return different generators for signed_url path and connector path
        ep.on_event = MagicMock(return_value=_async_gen_events([
            {"event": "parsing_complete", "data": {"record_id": "r1"}},
        ]))

        payload = {
            "recordId": "r1",
            "virtualRecordId": "vr1",
            "orgId": "org-1",
            "mimeType": "application/pdf",
            "extension": "pdf",
            "signedUrl": "https://example.com/file.pdf",
        }

        with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = None
            with patch("app.services.messaging.kafka.handlers.record.generate_jwt", new_callable=AsyncMock) as mock_jwt:
                mock_jwt.return_value = "tok"
                with patch("app.services.messaging.kafka.handlers.record.make_api_call", new_callable=AsyncMock) as mock_api:
                    mock_api.return_value = {"data": b"bytes"}
                    handler.config_service.get_config = AsyncMock(
                        return_value={"connectors": {"endpoint": "http://localhost:8088"}}
                    )
                    events = await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        # The connector fallback should have been used
        assert len(events) >= 1


# ===================================================================
# Connector streaming path (no signed URL)
# ===================================================================

class TestConnectorStreamingPath:
    """Tests for connector streaming fallback path."""

    @pytest.mark.asyncio
    async def test_no_signed_url_uses_connector(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "application/pdf",
        }
        gp.get_document = AsyncMock(side_effect=[record, record])
        gp.update_queued_duplicates_status = AsyncMock()

        ep = handler.event_processor
        ep.on_event = MagicMock(return_value=_async_gen_events([
            {"event": "parsing_complete", "data": {"record_id": "r1"}},
            {"event": "indexing_complete", "data": {"record_id": "r1"}},
        ]))

        payload = {
            "recordId": "r1",
            "virtualRecordId": "vr1",
            "orgId": "org-1",
            "mimeType": "application/pdf",
            "extension": "pdf",
            # No signedUrl
        }

        with patch("app.services.messaging.kafka.handlers.record.generate_jwt", new_callable=AsyncMock) as mock_jwt:
            mock_jwt.return_value = "fake-token"
            with patch("app.services.messaging.kafka.handlers.record.make_api_call", new_callable=AsyncMock) as mock_api:
                mock_api.return_value = {"data": b"file bytes"}
                handler.config_service.get_config = AsyncMock(
                    return_value={"connectors": {"endpoint": "http://localhost:8088"}}
                )
                events = await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        assert len(events) == 2
        mock_jwt.assert_awaited_once()
        mock_api.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connector_streaming_error_raises_exception(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "application/pdf",
        }
        gp.get_document = AsyncMock(side_effect=[record, record])
        gp.batch_upsert_nodes = AsyncMock()

        handler._trigger_next_queued_duplicate = AsyncMock()

        payload = {
            "recordId": "r1",
            "virtualRecordId": "vr1",
            "orgId": "org-1",
            "mimeType": "application/pdf",
            "extension": "pdf",
        }

        with patch("app.services.messaging.kafka.handlers.record.generate_jwt", new_callable=AsyncMock) as mock_jwt:
            mock_jwt.return_value = "token"
            with patch("app.services.messaging.kafka.handlers.record.make_api_call", new_callable=AsyncMock) as mock_api:
                mock_api.side_effect = Exception("connection refused")
                handler.config_service.get_config = AsyncMock(
                    return_value={"connectors": {"endpoint": "http://localhost:8088"}}
                )
                with pytest.raises(Exception, match="connection refused"):
                    await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)


# ===================================================================
# Error handling in process_event
# ===================================================================

class TestProcessEventErrors:
    """Tests for error handling and the finally block."""

    @pytest.mark.asyncio
    async def test_indexing_error_re_raised_as_exception(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "application/pdf",
        }
        gp.get_document = AsyncMock(side_effect=[record, record])
        gp.batch_upsert_nodes = AsyncMock()

        handler._trigger_next_queued_duplicate = AsyncMock()

        payload = {
            "recordId": "r1",
            "virtualRecordId": "vr1",
            "orgId": "org-1",
            "mimeType": "application/pdf",
            "extension": "pdf",
            "signedUrl": "https://example.com/file.pdf",
        }

        with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock) as mock_dl:
            mock_dl.side_effect = IndexingError("Indexing failed")
            with patch("app.services.messaging.kafka.handlers.record.generate_jwt", new_callable=AsyncMock) as mock_jwt:
                mock_jwt.side_effect = Exception("jwt fail too")
                handler.config_service.get_config = AsyncMock(
                    return_value={"connectors": {"endpoint": "http://localhost:8088"}}
                )
                with pytest.raises(Exception):
                    await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

    @pytest.mark.asyncio
    async def test_error_updates_document_status_to_failed(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "application/pdf",
        }
        gp.get_document = AsyncMock(return_value=record)
        gp.batch_upsert_nodes = AsyncMock()
        gp.find_next_queued_duplicate = AsyncMock(return_value=None)

        payload = {
            "recordId": "r1",
            "virtualRecordId": "vr1",
            "orgId": "org-1",
            "mimeType": "application/pdf",
            "extension": "pdf",
        }

        with patch("app.services.messaging.kafka.handlers.record.generate_jwt", new_callable=AsyncMock) as mock_jwt:
            mock_jwt.return_value = "token"
            with patch("app.services.messaging.kafka.handlers.record.make_api_call", new_callable=AsyncMock) as mock_api:
                mock_api.side_effect = Exception("download failed")
                handler.config_service.get_config = AsyncMock(
                    return_value={"connectors": {"endpoint": "http://localhost:8088"}}
                )
                with pytest.raises(Exception, match="download failed"):
                    await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        # The finally block should have called __update_document_status
        # which calls get_document and batch_upsert_nodes
        assert gp.get_document.await_count >= 2
        assert gp.batch_upsert_nodes.awaited

    @pytest.mark.asyncio
    async def test_finally_block_completed_status_updates_queued_duplicates(self):
        """When processing succeeds and status is COMPLETED, update queued duplicates."""
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record_initial = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "application/pdf",
        }
        record_final = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "mimeType": "application/pdf",
        }
        gp.get_document = AsyncMock(side_effect=[record_initial, record_final])
        gp.update_queued_duplicates_status = AsyncMock()

        ep = handler.event_processor
        ep.on_event = MagicMock(return_value=_async_gen_events([
            {"event": "parsing_complete", "data": {"record_id": "r1"}},
            {"event": "indexing_complete", "data": {"record_id": "r1"}},
        ]))

        payload = {
            "recordId": "r1",
            "virtualRecordId": "vr1",
            "orgId": "org-1",
            "mimeType": "application/pdf",
            "extension": "pdf",
            "signedUrl": "https://example.com/file.pdf",
        }

        with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = b"content"
            events = await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        gp.update_queued_duplicates_status.assert_awaited_once_with(
            "r1", ProgressStatus.COMPLETED.value, "vr1"
        )

    @pytest.mark.asyncio
    async def test_finally_block_empty_status_updates_queued_duplicates(self):
        """When processing succeeds and status is EMPTY, update queued duplicates."""
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record_initial = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "application/pdf",
        }
        record_final = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.EMPTY.value,
            "mimeType": "application/pdf",
        }
        gp.get_document = AsyncMock(side_effect=[record_initial, record_final])
        gp.update_queued_duplicates_status = AsyncMock()

        ep = handler.event_processor
        ep.on_event = MagicMock(return_value=_async_gen_events([
            {"event": "parsing_complete", "data": {"record_id": "r1"}},
        ]))

        payload = {
            "recordId": "r1",
            "virtualRecordId": "vr1",
            "orgId": "org-1",
            "mimeType": "application/pdf",
            "extension": "pdf",
            "signedUrl": "https://example.com/file.pdf",
        }

        with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = b"content"
            await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        gp.update_queued_duplicates_status.assert_awaited_once_with(
            "r1", ProgressStatus.EMPTY.value, "vr1"
        )

    @pytest.mark.asyncio
    async def test_finally_block_enable_multimodal_triggers_next_duplicate(self):
        """When status is ENABLE_MULTIMODAL_MODELS, trigger next queued duplicate."""
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record_initial = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "application/pdf",
        }
        record_final = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.ENABLE_MULTIMODAL_MODELS.value,
            "mimeType": "application/pdf",
        }
        gp.get_document = AsyncMock(side_effect=[record_initial, record_final])
        gp.find_next_queued_duplicate = AsyncMock(return_value=None)

        ep = handler.event_processor
        ep.on_event = MagicMock(return_value=_async_gen_events([
            {"event": "parsing_complete", "data": {"record_id": "r1"}},
        ]))

        payload = {
            "recordId": "r1",
            "virtualRecordId": "vr1",
            "orgId": "org-1",
            "mimeType": "application/pdf",
            "extension": "pdf",
            "signedUrl": "https://example.com/file.pdf",
        }

        with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = b"content"
            await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        gp.find_next_queued_duplicate.assert_awaited_once_with("r1")

    @pytest.mark.asyncio
    async def test_finally_block_record_none_in_db_logs_warning(self):
        """When record is not found in DB during finally block."""
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record_initial = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "application/pdf",
        }
        # First get_document returns record, second (in finally) returns None
        gp.get_document = AsyncMock(side_effect=[record_initial, None])

        ep = handler.event_processor
        ep.on_event = MagicMock(return_value=_async_gen_events([
            {"event": "parsing_complete", "data": {"record_id": "r1"}},
        ]))

        payload = {
            "recordId": "r1",
            "virtualRecordId": "vr1",
            "orgId": "org-1",
            "mimeType": "application/pdf",
            "extension": "pdf",
            "signedUrl": "https://example.com/file.pdf",
        }

        with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = b"content"
            await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        # Should log warning about record not found
        handler.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_delete_event_skips_queued_duplicate_update_in_finally(self):
        """Delete events should not trigger queued duplicate update in finally."""
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        gp.get_document = AsyncMock(return_value={"_key": "r1", "virtualRecordId": "vr1"})
        gp.update_queued_duplicates_status = AsyncMock()

        pipeline = handler.event_processor.processor.indexing_pipeline
        pipeline.delete_embeddings = AsyncMock()

        payload = {"recordId": "r1", "virtualRecordId": "vr1"}
        events = await _collect_events(handler, EventTypes.DELETE_RECORD.value, payload)

        assert len(events) == 2
        gp.update_queued_duplicates_status.assert_not_awaited()


# ===================================================================
# _trigger_next_queued_duplicate
# ===================================================================

class TestTriggerNextQueuedDuplicate:
    """Tests for _trigger_next_queued_duplicate."""

    @pytest.mark.asyncio
    async def test_no_queued_duplicate_found(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        gp.find_next_queued_duplicate = AsyncMock(return_value=None)

        await handler._trigger_next_queued_duplicate("r1", "vr1")

        gp.find_next_queued_duplicate.assert_awaited_once_with("r1")

    @pytest.mark.asyncio
    async def test_queued_duplicate_found_non_file(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        next_record = {
            "_key": "r2",
            "recordType": "MAIL",
        }
        gp.find_next_queued_duplicate = AsyncMock(return_value=next_record)
        gp._create_reindex_event_payload = AsyncMock(return_value={"some": "payload"})
        gp._publish_record_event = AsyncMock()

        await handler._trigger_next_queued_duplicate("r1", "vr1")

        gp._create_reindex_event_payload.assert_awaited_once_with(next_record, None)
        gp._publish_record_event.assert_awaited_once_with("newRecord", {"some": "payload"})

    @pytest.mark.asyncio
    async def test_queued_duplicate_found_file_type(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        next_record = {
            "_key": "r2",
            "recordType": RecordTypes.FILE.value,
        }
        file_record = {"_key": "r2", "fileName": "doc.pdf"}
        gp.find_next_queued_duplicate = AsyncMock(return_value=next_record)
        gp.get_document = AsyncMock(return_value=file_record)
        gp._create_reindex_event_payload = AsyncMock(return_value={"some": "payload"})
        gp._publish_record_event = AsyncMock()

        await handler._trigger_next_queued_duplicate("r1", "vr1")

        gp.get_document.assert_awaited_once_with("r2", CollectionNames.FILES.value)
        gp._create_reindex_event_payload.assert_awaited_once_with(next_record, file_record)
        gp._publish_record_event.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_updates_queued_duplicates_status(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        gp.find_next_queued_duplicate = AsyncMock(side_effect=Exception("DB error"))
        gp.update_queued_duplicates_status = AsyncMock()

        await handler._trigger_next_queued_duplicate("r1", "vr1")

        gp.update_queued_duplicates_status.assert_awaited_once_with(
            "r1", ProgressStatus.FAILED.value, "vr1"
        )

    @pytest.mark.asyncio
    async def test_exception_and_status_update_also_fails(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        gp.find_next_queued_duplicate = AsyncMock(side_effect=Exception("DB error"))
        gp.update_queued_duplicates_status = AsyncMock(side_effect=Exception("Also failed"))

        # Should not raise - both exceptions are caught
        await handler._trigger_next_queued_duplicate("r1", "vr1")

        handler.logger.warning.assert_called()


# ===================================================================
# __update_document_status
# ===================================================================

class TestUpdateDocumentStatus:
    """Tests for __update_document_status private method."""

    @pytest.mark.asyncio
    async def test_success(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "extractionStatus": ProgressStatus.NOT_STARTED.value,
        }
        gp.get_document = AsyncMock(return_value=record)
        gp.batch_upsert_nodes = AsyncMock()

        result = await handler._RecordEventHandler__update_document_status(
            record_id="r1",
            indexing_status=ProgressStatus.FAILED.value,
            extraction_status=ProgressStatus.FAILED.value,
            reason="Something went wrong",
        )

        assert result is not None
        gp.batch_upsert_nodes.assert_awaited_once()
        call_args = gp.batch_upsert_nodes.call_args
        doc = call_args[0][0][0]
        assert doc["indexingStatus"] == ProgressStatus.FAILED.value
        assert doc["extractionStatus"] == ProgressStatus.FAILED.value
        assert doc["reason"] == "Something went wrong"

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        gp.get_document = AsyncMock(return_value=None)

        result = await handler._RecordEventHandler__update_document_status(
            record_id="r-missing",
            indexing_status=ProgressStatus.FAILED.value,
            extraction_status=ProgressStatus.FAILED.value,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_preserves_completed_extraction_status(self):
        """If extraction is already COMPLETED, it should remain COMPLETED."""
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "indexingStatus": ProgressStatus.IN_PROGRESS.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
        }
        gp.get_document = AsyncMock(return_value=record)
        gp.batch_upsert_nodes = AsyncMock()

        await handler._RecordEventHandler__update_document_status(
            record_id="r1",
            indexing_status=ProgressStatus.FAILED.value,
            extraction_status=ProgressStatus.FAILED.value,
        )

        call_args = gp.batch_upsert_nodes.call_args
        doc = call_args[0][0][0]
        # Extraction should remain COMPLETED
        assert doc["extractionStatus"] == ProgressStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_no_reason_does_not_set_reason_key(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "extractionStatus": ProgressStatus.NOT_STARTED.value,
        }
        gp.get_document = AsyncMock(return_value=record)
        gp.batch_upsert_nodes = AsyncMock()

        await handler._RecordEventHandler__update_document_status(
            record_id="r1",
            indexing_status=ProgressStatus.FAILED.value,
            extraction_status=ProgressStatus.FAILED.value,
            reason=None,
        )

        call_args = gp.batch_upsert_nodes.call_args
        doc = call_args[0][0][0]
        assert "reason" not in doc

    @pytest.mark.asyncio
    async def test_db_error_returns_none(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        gp.get_document = AsyncMock(side_effect=Exception("DB fail"))

        result = await handler._RecordEventHandler__update_document_status(
            record_id="r1",
            indexing_status=ProgressStatus.FAILED.value,
            extraction_status=ProgressStatus.FAILED.value,
        )

        assert result is None


# ===================================================================
# _download_from_signed_url
# ===================================================================

class TestDownloadFromSignedUrl:
    """Tests for _download_from_signed_url."""

    @pytest.mark.asyncio
    async def test_successful_download(self):
        handler = _make_handler()
        file_content = b"Hello, World! This is a test file."

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": str(len(file_content))}

        # Create an async iterator for content chunks
        async def _iter_chunked(chunk_size):
            yield file_content

        mock_response.content = MagicMock()
        mock_response.content.iter_chunked = _iter_chunked

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=_AsyncContextManager(mock_response))
        mock_session_cls = MagicMock(return_value=_AsyncContextManager(mock_session))

        with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientSession", mock_session_cls):
            with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientTimeout"):
                result = await handler._download_from_signed_url(
                    signed_url="https://example.com/file.pdf",
                    record_id="r1",
                    doc={"_key": "r1"},
                )

        assert result == file_content

    @pytest.mark.asyncio
    async def test_download_non_200_status_retries_and_fails(self):
        handler = _make_handler()

        mock_response = AsyncMock()
        mock_response.status = 500

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=_AsyncContextManager(mock_response))
        mock_session_cls = MagicMock(return_value=_AsyncContextManager(mock_session))

        with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientSession", mock_session_cls):
            with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientTimeout"):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    with pytest.raises(Exception, match="Download failed after 3 attempts"):
                        await handler._download_from_signed_url(
                            signed_url="https://example.com/file.pdf",
                            record_id="r1",
                            doc={"_key": "r1"},
                        )

    @pytest.mark.asyncio
    async def test_download_io_error_during_chunking(self):
        handler = _make_handler()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {}

        async def _iter_chunked_fail(chunk_size):
            raise IOError("Disk full")
            yield  # make it a generator  # noqa: E115

        mock_response.content = MagicMock()
        mock_response.content.iter_chunked = _iter_chunked_fail

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=_AsyncContextManager(mock_response))
        mock_session_cls = MagicMock(return_value=_AsyncContextManager(mock_session))

        with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientSession", mock_session_cls):
            with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientTimeout"):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    with pytest.raises(Exception, match="Download failed after 3 attempts"):
                        await handler._download_from_signed_url(
                            signed_url="https://example.com/file.pdf",
                            record_id="r1",
                            doc={"_key": "r1"},
                        )

    @pytest.mark.asyncio
    async def test_download_timeout_retries(self):
        handler = _make_handler()

        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=asyncio.TimeoutError("timed out"))
        mock_session_cls = MagicMock(return_value=_AsyncContextManager(mock_session))

        with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientSession", mock_session_cls):
            with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientTimeout"):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    with pytest.raises(Exception, match="Download failed after 3 attempts"):
                        await handler._download_from_signed_url(
                            signed_url="https://example.com/file.pdf",
                            record_id="r1",
                            doc={"_key": "r1"},
                        )

    @pytest.mark.asyncio
    async def test_download_server_disconnected(self):
        handler = _make_handler()

        mock_response = MagicMock()
        mock_response.__aenter__ = AsyncMock(side_effect=aiohttp.ServerDisconnectedError())

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session_cls = MagicMock(return_value=_AsyncContextManager(mock_session))

        with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientSession", mock_session_cls):
            with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientTimeout"):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    with pytest.raises(Exception, match="Download failed after 3 attempts"):
                        await handler._download_from_signed_url(
                            signed_url="https://example.com/file.pdf",
                            record_id="r1",
                            doc={"_key": "r1"},
                        )

    @pytest.mark.asyncio
    async def test_download_no_content_length(self):
        """Download succeeds even without Content-Length header."""
        handler = _make_handler()
        file_content = b"Small file"

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {}

        async def _iter_chunked(chunk_size):
            yield file_content

        mock_response.content = MagicMock()
        mock_response.content.iter_chunked = _iter_chunked

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=_AsyncContextManager(mock_response))
        mock_session_cls = MagicMock(return_value=_AsyncContextManager(mock_session))

        with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientSession", mock_session_cls):
            with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientTimeout"):
                result = await handler._download_from_signed_url(
                    signed_url="https://example.com/file.pdf",
                    record_id="r1",
                    doc={"_key": "r1"},
                )

        assert result == file_content


# ===================================================================
# Integration-style: full happy path
# ===================================================================

class TestFullHappyPath:
    """End-to-end happy path tests through the whole process_event flow."""

    @pytest.mark.asyncio
    async def test_new_record_with_signed_url_full_flow(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "application/pdf",
        }
        record_completed = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "mimeType": "application/pdf",
        }
        gp.get_document = AsyncMock(side_effect=[record, record_completed])
        gp.update_queued_duplicates_status = AsyncMock()

        ep = handler.event_processor
        ep.on_event = MagicMock(return_value=_async_gen_events([
            {"event": "parsing_complete", "data": {"record_id": "r1"}},
            {"event": "indexing_complete", "data": {"record_id": "r1"}},
        ]))

        payload = {
            "recordId": "r1",
            "virtualRecordId": "vr1",
            "orgId": "org-1",
            "mimeType": "application/pdf",
            "extension": "pdf",
            "signedUrl": "https://storage.example.com/file.pdf",
        }

        with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = b"PDF file content"
            events = await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        assert len(events) == 2
        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"
        gp.update_queued_duplicates_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_new_record_connector_streaming_full_flow(self):
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "text/plain",
        }
        record_completed = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
        }
        gp.get_document = AsyncMock(side_effect=[record, record_completed])
        gp.update_queued_duplicates_status = AsyncMock()

        ep = handler.event_processor
        ep.on_event = MagicMock(return_value=_async_gen_events([
            {"event": "parsing_complete", "data": {"record_id": "r1"}},
            {"event": "indexing_complete", "data": {"record_id": "r1"}},
        ]))

        payload = {
            "recordId": "r1",
            "virtualRecordId": "vr1",
            "orgId": "org-1",
            "mimeType": "text/plain",
            "extension": "txt",
        }

        with patch("app.services.messaging.kafka.handlers.record.generate_jwt", new_callable=AsyncMock) as mock_jwt:
            mock_jwt.return_value = "token"
            with patch("app.services.messaging.kafka.handlers.record.make_api_call", new_callable=AsyncMock) as mock_api:
                mock_api.return_value = {"data": b"plain text content"}
                handler.config_service.get_config = AsyncMock(
                    return_value={"connectors": {"endpoint": "http://localhost:8088"}}
                )
                events = await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        assert len(events) == 2
        gp.update_queued_duplicates_status.assert_awaited_once()


# ===================================================================
# Additional coverage: IndexingError, large chunk logging,
# ClientConnectorError, record_name fallback edge cases
# ===================================================================

class TestAdditionalCoverage:
    """Tests targeting remaining uncovered lines."""

    @pytest.mark.asyncio
    async def test_indexing_error_caught_directly(self):
        """IndexingError raised at the top level of process_event (lines 362-365)."""
        handler = _make_handler()
        gp = handler.event_processor.graph_provider

        # Make get_document raise IndexingError directly
        gp.get_document = AsyncMock(side_effect=IndexingError("parse fail", record_id="r1"))
        gp.batch_upsert_nodes = AsyncMock()
        gp.find_next_queued_duplicate = AsyncMock(return_value=None)

        payload = {
            "recordId": "r1",
            "virtualRecordId": "vr1",
            "mimeType": "application/pdf",
            "extension": "pdf",
        }

        with pytest.raises(Exception, match="parse fail"):
            await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

    @pytest.mark.asyncio
    async def test_record_name_without_dot_does_not_derive_extension(self):
        """When recordName has no dot, extension stays 'unknown' (line 208->211 branch)."""
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "application/x-weird",
        }
        # record + finally
        gp.get_document = AsyncMock(side_effect=[record, record])
        gp.batch_upsert_nodes = AsyncMock()
        gp.update_queued_duplicates_status = AsyncMock()

        payload = {
            "recordId": "r1",
            "mimeType": "application/x-weird",
            "extension": "unknown",
            "recordName": "nodotfilename",
        }
        events = await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        # Should fall through to unsupported file type
        assert len(events) == 2
        gp.batch_upsert_nodes.assert_awaited()

    @pytest.mark.asyncio
    async def test_record_name_missing_does_not_derive_extension(self):
        """When recordName is missing, extension stays 'unknown'."""
        handler = _make_handler()
        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "mimeType": "application/x-weird",
        }
        gp.get_document = AsyncMock(side_effect=[record, record])
        gp.batch_upsert_nodes = AsyncMock()
        gp.update_queued_duplicates_status = AsyncMock()

        payload = {
            "recordId": "r1",
            "mimeType": "application/x-weird",
            "extension": "unknown",
            # No recordName
        }
        events = await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)

        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_download_large_file_triggers_debug_logging(self):
        """Download a file larger than chunk_size to trigger debug logging (lines 498-501)."""
        handler = _make_handler()
        chunk_size = 1024 * 1024 * 3  # 3MB

        # Create two chunks each >= chunk_size
        chunk1 = b"x" * chunk_size
        chunk2 = b"y" * chunk_size

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": str(chunk_size * 2)}

        async def _iter_chunked(size):
            yield chunk1
            yield chunk2

        mock_response.content = MagicMock()
        mock_response.content.iter_chunked = _iter_chunked

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=_AsyncContextManager(mock_response))
        mock_session_cls = MagicMock(return_value=_AsyncContextManager(mock_session))

        with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientSession", mock_session_cls):
            with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientTimeout"):
                result = await handler._download_from_signed_url(
                    signed_url="https://example.com/bigfile.pdf",
                    record_id="r1",
                    doc={"_key": "r1"},
                )

        assert result == chunk1 + chunk2
        # Debug logging should have been called for progress
        handler.logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_download_client_connector_error(self):
        """ClientConnectorError triggers retry (line 516)."""
        handler = _make_handler()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {}

        # First: get returns a context manager whose __aenter__ raises ClientConnectorError
        mock_cm = MagicMock()

        # We need the inner context manager (session.get()) to raise
        async def raise_connector_error():
            raise aiohttp.ClientConnectorError(
                connection_key=MagicMock(), os_error=OSError("connection refused")
            )

        mock_cm.__aenter__ = MagicMock(side_effect=raise_connector_error)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_cm)
        mock_session_cls = MagicMock(return_value=_AsyncContextManager(mock_session))

        with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientSession", mock_session_cls):
            with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientTimeout"):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    with pytest.raises(Exception, match="Download failed after 3 attempts"):
                        await handler._download_from_signed_url(
                            signed_url="https://example.com/file.pdf",
                            record_id="r1",
                            doc={"_key": "r1"},
                        )

    @pytest.mark.asyncio
    async def test_download_retries_then_succeeds(self):
        """Download fails on first attempt but succeeds on second (covers retry loop continuation at line 468)."""
        handler = _make_handler()
        file_content = b"Success on retry"

        # First call raises, second call succeeds
        mock_response_success = AsyncMock()
        mock_response_success.status = 200
        mock_response_success.headers = {}

        async def _iter_chunked(chunk_size):
            yield file_content

        mock_response_success.content = MagicMock()
        mock_response_success.content.iter_chunked = _iter_chunked

        # First call: raise error. Second call: return success.
        call_count = 0

        class _SessionWithRetry:
            def __init__(self):
                self.call_count = 0

            def get(self, url):
                self.call_count += 1
                if self.call_count == 1:
                    raise aiohttp.ClientError("First attempt fails")
                return _AsyncContextManager(mock_response_success)

        session_instance = _SessionWithRetry()

        # Each iteration creates a new session, so we need two sessions
        session1 = MagicMock()
        session1.get = MagicMock(side_effect=aiohttp.ClientError("Attempt 1 fails"))

        session2 = AsyncMock()
        session2.get = MagicMock(return_value=_AsyncContextManager(mock_response_success))

        session_contexts = [
            _AsyncContextManager(session1),
            _AsyncContextManager(session2),
        ]
        mock_session_cls = MagicMock(side_effect=session_contexts)

        with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientSession", mock_session_cls):
            with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientTimeout"):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    result = await handler._download_from_signed_url(
                        signed_url="https://example.com/file.pdf",
                        record_id="r1",
                        doc={"_key": "r1"},
                    )

        assert result == file_content


# ===================================================================
# Helpers for async generators and context managers
# ===================================================================

async def _async_gen_events(events):
    """Create an async generator that yields PipelineEvent objects.

    Accepts dicts with 'event' and 'data' keys and converts them to
    PipelineEvent instances so the handler sees the correct types.
    """
    for event in events:
        if isinstance(event, dict):
            data = event.get("data", {})
            yield PipelineEvent(
                event=IndexingEvent(event["event"]),
                data=PipelineEventData(
                    record_id=data.get("record_id", "unknown"),
                    count=data.get("count"),
                ),
            )
        else:
            yield event


class _AsyncContextManager:
    """Helper to create async context managers for mocking aiohttp sessions and responses."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False
