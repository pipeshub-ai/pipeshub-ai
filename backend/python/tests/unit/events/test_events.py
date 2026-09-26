"""Tests for app.events.events (EventProcessor class).

This module tests the EventProcessor from the events.py module.
Since events.py and processor.py contain the same EventProcessor class,
these tests provide additional coverage for edge cases and boundary conditions.
"""

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import (
    EventTypes,
    ExtensionTypes,
    MimeTypes,
    ProgressStatus,
)
from app.events.events import DedupDecision, EventProcessor
from app.exceptions.indexing_exceptions import ProcessingError
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event_processor():
    """Create an EventProcessor with mocked deps from events module."""
    logger = MagicMock()
    processor = MagicMock()
    # Membership sync is awaited, so the pipeline must be an AsyncMock; a bare
    # MagicMock raises TypeError, which the caller now propagates rather than
    # swallowing.
    processor.indexing_pipeline = AsyncMock()
    graph_provider = AsyncMock()
    graph_provider.update_node = AsyncMock(return_value=True)
    config_service = MagicMock()

    ep = EventProcessor(logger, processor, graph_provider, config_service)
    return ep, logger, processor, graph_provider


def _make_event_payload(
    record_id="rec-1",
    mime_type="unknown",
    extension="unknown",
    event_type=None,
    connector_name="",
    buffer=b"hello",
    virtual_record_id=None,
    org_id="org-1",
    version=1,
    record_name=None,
):
    """Build event_data dict for on_event."""
    payload = {
        "recordId": record_id,
        "orgId": org_id,
        "virtualRecordId": virtual_record_id,
        "version": version,
        "connectorName": connector_name,
        "extension": extension,
        "mimeType": mime_type,
        "recordName": record_name or f"test-{record_id}",
        "buffer": buffer,
    }
    data = {"payload": payload}
    if event_type is not None:
        data["eventType"] = event_type
    return data


async def _drain(async_gen):
    """Collect all items from an async generator."""
    items = []
    async for item in async_gen:
        items.append(item)
    return items


async def _mock_processor_gen(*args, **kwargs):
    yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))
    yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="rec-1"))


# ===========================================================================
# Constructor
# ===========================================================================


class TestEventProcessorInit:
    """Tests for EventProcessor.__init__."""

    def test_initialization_stores_all_deps(self):
        """All dependencies are stored as attributes."""
        ep, logger, processor, graph_provider = _make_event_processor()

        assert ep.logger is logger
        assert ep.processor is processor
        assert ep.graph_provider is graph_provider
        logger.info.assert_called()  # "Initializing EventProcessor"

    def test_config_service_defaults_to_none(self):
        """config_service defaults to None when not provided."""
        logger = MagicMock()
        processor = MagicMock()
        graph_provider = AsyncMock()

        ep = EventProcessor(logger, processor, graph_provider)

        assert ep.config_service is None

    def test_config_service_stored_when_provided(self):
        """config_service is stored when explicitly provided."""
        ep, _, _, _ = _make_event_processor()

        assert ep.config_service is not None


# ===========================================================================
# mark_record_status - Additional Edge Cases
# ===========================================================================


class TestMarkRecordStatusEdgeCases:
    """Additional edge-case tests for mark_record_status."""

    @pytest.mark.asyncio
    async def test_completed_status(self):
        """COMPLETED status is applied correctly."""
        ep, _, _, gp = _make_event_processor()
        doc = {"_key": "k1"}

        await ep.mark_record_status(doc, ProgressStatus.COMPLETED)

        assert doc["indexingStatus"] == ProgressStatus.COMPLETED.value
        assert "extractionStatus" not in doc

    @pytest.mark.asyncio
    async def test_failed_status(self):
        """FAILED status is applied correctly."""
        ep, _, _, gp = _make_event_processor()
        doc = {"_key": "k2"}

        await ep.mark_record_status(doc, ProgressStatus.FAILED)

        assert doc["indexingStatus"] == ProgressStatus.FAILED.value
        assert "extractionStatus" not in doc

    @pytest.mark.asyncio
    async def test_not_started_status(self):
        """NOT_STARTED status is applied correctly."""
        ep, _, _, gp = _make_event_processor()
        doc = {"_key": "k3"}

        await ep.mark_record_status(doc, ProgressStatus.NOT_STARTED)

        assert doc["indexingStatus"] == ProgressStatus.NOT_STARTED.value

    @pytest.mark.asyncio
    async def test_queued_status(self):
        """QUEUED status is applied correctly."""
        ep, _, _, gp = _make_event_processor()
        doc = {"_key": "k4"}

        await ep.mark_record_status(doc, ProgressStatus.QUEUED)

        assert doc["indexingStatus"] == ProgressStatus.QUEUED.value

    @pytest.mark.asyncio
    async def test_doc_modified_in_place(self):
        """The doc dict itself is mutated (not a copy)."""
        ep, _, _, gp = _make_event_processor()
        doc = {"_key": "k5", "other": "data"}

        await ep.mark_record_status(doc, ProgressStatus.IN_PROGRESS)

        # The original dict should be modified
        assert "indexingStatus" in doc
        assert doc["other"] == "data"

    @pytest.mark.asyncio
    async def test_error_with_non_empty_status_raises(self):
        ep, _, _, gp = _make_event_processor()
        gp.update_node.side_effect = Exception("fail")
        doc = {"_key": "k6"}

        with pytest.raises(Exception, match="fail"):
            await ep.mark_record_status(doc, ProgressStatus.FAILED)

    @pytest.mark.asyncio
    async def test_error_with_empty_status_raises(self):
        """Errors with EMPTY status are re-raised."""
        ep, _, _, gp = _make_event_processor()
        gp.update_node.side_effect = Exception("fail")
        doc = {"_key": "k7"}

        with pytest.raises(Exception, match="fail"):
            await ep.mark_record_status(doc, ProgressStatus.EMPTY)

    @pytest.mark.asyncio
    async def test_silent_write_failure_raises_indexing_error(self):
        """A write that fails without raising (update_node returns False,
        e.g. a transient graph DB write failure) must not be swallowed.

        Without this, mark_record_status would report success on a status
        that was never actually persisted — leaving the record stuck at its
        prior status forever, since reconciliation only revisits QUEUED/
        IN_PROGRESS records and the caller would still yield completion
        events as if the write had succeeded.
        """
        from app.exceptions.indexing_exceptions import IndexingError  # noqa: PLC0415

        ep, _, _, gp = _make_event_processor()
        gp.update_node = AsyncMock(return_value=False)
        doc = {"_key": "k8"}

        with pytest.raises(IndexingError):
            await ep.mark_record_status(doc, ProgressStatus.FILE_TYPE_NOT_SUPPORTED)


# ===========================================================================
# _check_duplicate_by_md5 - Additional Edge Cases
# ===========================================================================


class TestCheckDuplicateMd5EdgeCases:
    """Additional tests for _check_duplicate_by_md5."""

    @pytest.mark.asyncio
    async def test_empty_string_content_no_md5(self):
        """Empty string content with no md5Checksum => returns False (no hash calculated)."""
        ep, _, _, gp = _make_event_processor()
        doc = {"_key": "r1", "recordType": "FILE"}

        result = await ep._check_duplicate_by_md5("", doc)

        # Empty string is falsy in `if md5_checksum is None and content:` check
        assert result.skip_indexing is False

    @pytest.mark.asyncio
    async def test_empty_bytes_content_no_md5(self):
        """Empty bytes content with no md5Checksum => returns False."""
        ep, _, _, gp = _make_event_processor()
        doc = {"_key": "r2", "recordType": "FILE"}

        result = await ep._check_duplicate_by_md5(b"", doc)

        assert result.skip_indexing is False

    @pytest.mark.asyncio
    async def test_completed_without_virtual_record_id_not_matched(self):
        """A COMPLETED duplicate without virtualRecordId is NOT treated as processed."""
        ep, _, _, gp = _make_event_processor()
        dup = {
            "_key": "dup-1",
            "virtualRecordId": None,  # No virtualRecordId
            "indexingStatus": ProgressStatus.COMPLETED.value,
        }
        gp.find_duplicate_records.return_value = [dup]
        doc = {"_key": "r3", "md5Checksum": "abc", "recordType": "FILE", "sizeInBytes": 10}

        result = await ep._check_duplicate_by_md5(b"x", doc)

        # COMPLETED without virtualRecordId is NOT matched as processed_duplicate
        # (the condition requires virtualRecordId AND COMPLETED)
        # So it falls through. It's also not IN_PROGRESS, so returns False.
        assert result.skip_indexing is False

    @pytest.mark.asyncio
    async def test_multiple_duplicates_prefers_processed(self):
        """When both processed and in-progress dups exist, processed takes priority."""
        ep, _, _, gp = _make_event_processor()
        processed = {
            "_key": "dup-p",
            "virtualRecordId": "vr-p",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
            "summaryDocumentId": "sum-p",
        }
        in_progress = {
            "_key": "dup-ip",
            "indexingStatus": ProgressStatus.IN_PROGRESS.value,
        }
        gp.find_duplicate_records.return_value = [in_progress, processed]
        doc = {"_key": "r4", "md5Checksum": "abc", "recordType": "FILE", "sizeInBytes": 10}

        with patch("app.events.events.get_epoch_timestamp_in_ms", return_value=100):
            result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        # Should be handled as processed, not queued
        assert doc["virtualRecordId"] == "vr-p"
        assert doc.get("indexingStatus") != ProgressStatus.QUEUED.value

    @pytest.mark.asyncio
    async def test_doc_uses_id_field_as_fallback_for_key(self):
        """copy_document_relationships uses doc['id'] when _key is missing."""
        ep, _, _, gp = _make_event_processor()
        processed = {
            "_key": "dup-src",
            "virtualRecordId": "vr-1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
            "summaryDocumentId": "sum-1",
        }
        gp.find_duplicate_records.return_value = [processed]
        doc = {"id": "r-fallback", "md5Checksum": "abc", "recordType": "FILE", "sizeInBytes": 10}

        with patch("app.events.events.get_epoch_timestamp_in_ms", return_value=200):
            result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        gp.copy_document_relationships.assert_awaited_once_with("dup-src", "r-fallback")


# ===========================================================================
# _check_duplicate_by_md5 - cross-collection dedup matrix
#
# Under a hypothetical per-connector-type strategy (never shipped in OSS,
# but the interface must support it), a duplicate found in a different
# collection than the current record must not be treated the same as one
# in the same collection: see the dedup decision matrix in the
# flexible-collection-strategy plan.
# ===========================================================================


# The real contract double rather than a local stub: dedup compares its answer
# against the write path's, so a stub that skips `required_axes` would let this
# suite pass while production refused the same context.
from app.services.vector_db.strategies.per_connector_type import (  # noqa: E402
    PerConnectorTypeStrategy as _PerConnectorNameStrategy,
)


def _make_multi_collection_event_processor():
    from app.events.events import EventProcessor

    logger = MagicMock()
    processor = MagicMock()
    processor.indexing_pipeline = AsyncMock()
    graph_provider = AsyncMock()
    graph_provider.update_node = AsyncMock(return_value=True)
    config_service = MagicMock()

    ep = EventProcessor(
        logger,
        processor,
        graph_provider,
        config_service,
        collection_strategy=_PerConnectorNameStrategy(),
    )
    return ep, graph_provider


class TestCheckDuplicateMd5CrossCollectionMatrix:
    @pytest.mark.asyncio
    async def test_same_collection_completed_duplicate_skips_indexing(self):
        """Same connectorName -> same collection -> full reuse, skip indexing."""
        ep, gp = _make_multi_collection_event_processor()
        dup = {
            "_key": "dup-1",
            "connectorName": "GOOGLE_DRIVE",
            "virtualRecordId": "vr-1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
            "summaryDocumentId": "sum-1",
        }
        gp.find_duplicate_records.return_value = [dup]
        doc = {
            "_key": "r1",
            "connectorName": "GOOGLE_DRIVE",
            "md5Checksum": "abc",
            "recordType": "FILE",
            "sizeInBytes": 10,
        }

        with patch("app.events.events.get_epoch_timestamp_in_ms", return_value=100):
            result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        assert result.virtual_record_id == "vr-1"

    @pytest.mark.asyncio
    async def test_different_collection_completed_duplicate_indexes_anyway(self):
        """Different connectorName -> different collection -> VRID reused but
        this record still gets indexed into its own (empty) collection."""
        ep, gp = _make_multi_collection_event_processor()
        dup = {
            "_key": "dup-1",
            "connectorName": "GOOGLE_DRIVE",
            "virtualRecordId": "vr-1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
            "summaryDocumentId": "sum-1",
        }
        gp.find_duplicate_records.return_value = [dup]
        doc = {
            "_key": "r1",
            "connectorName": "SLACK",
            "md5Checksum": "abc",
            "recordType": "FILE",
            "sizeInBytes": 10,
        }

        result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is False
        assert result.virtual_record_id == "vr-1"
        # sync_vector_membership must not run for a collection this record
        # isn't even part of.
        ep.processor.indexing_pipeline.sync_vector_membership.assert_not_called()

    @pytest.mark.asyncio
    async def test_same_collection_in_progress_duplicate_queues(self):
        ep, gp = _make_multi_collection_event_processor()
        in_progress = {
            "_key": "dup-ip",
            "connectorName": "GOOGLE_DRIVE",
            "indexingStatus": ProgressStatus.IN_PROGRESS.value,
        }
        gp.find_duplicate_records.return_value = [in_progress]
        doc = {
            "_key": "r1",
            "connectorName": "GOOGLE_DRIVE",
            "md5Checksum": "abc",
            "recordType": "FILE",
            "sizeInBytes": 10,
        }

        result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        assert result.virtual_record_id is None
        assert doc["indexingStatus"] == ProgressStatus.QUEUED.value

    @pytest.mark.asyncio
    async def test_different_collection_in_progress_duplicate_proceeds(self):
        """Must not queue behind another collection's in-flight work."""
        ep, gp = _make_multi_collection_event_processor()
        in_progress = {
            "_key": "dup-ip",
            "connectorName": "GOOGLE_DRIVE",
            "indexingStatus": ProgressStatus.IN_PROGRESS.value,
        }
        gp.find_duplicate_records.return_value = [in_progress]
        doc = {
            "_key": "r1",
            "connectorName": "SLACK",
            "md5Checksum": "abc",
            "recordType": "FILE",
            "sizeInBytes": 10,
        }

        result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is False
        assert result.virtual_record_id is None
        assert doc.get("indexingStatus") != ProgressStatus.QUEUED.value


# ===========================================================================
# on_event - Additional Edge Cases
# ===========================================================================


class TestOnEventEdgeCases:
    """Additional edge-case tests for on_event."""

    @pytest.mark.asyncio
    async def test_default_event_type_is_new_record(self):
        """When eventType is missing, defaults to NEW_RECORD."""
        ep, logger, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            # No eventType key in data
            event_data = _make_event_payload(extension=ExtensionTypes.DOCX.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3

    @pytest.mark.asyncio
    async def test_virtual_record_id_from_record_when_not_in_payload(self):
        """If virtualRecordId not in payload, it's taken from the DB record."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "FILE",
            "virtualRecordId": "from-db",
        }
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                extension=ExtensionTypes.DOCX.value,
                virtual_record_id=None,
            )
            events = await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_docx_document.call_args[1]
        assert call_kwargs["virtual_record_id"] == "from-db"

    @pytest.mark.asyncio
    async def test_virtual_record_id_generated_when_none_everywhere(self):
        """If virtualRecordId is None in both payload and DB record, a UUID is generated."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "FILE",
            "virtualRecordId": None,
        }
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                extension=ExtensionTypes.DOCX.value,
                virtual_record_id=None,
            )
            events = await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_docx_document.call_args[1]
        # Should be a UUID string (36 chars with hyphens)
        assert len(call_kwargs["virtual_record_id"]) == 36

    @pytest.mark.asyncio
    async def test_origin_defaults_to_connector_when_connector_name_present(self):
        """Origin defaults to CONNECTOR when connectorName is not empty."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_txt_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                mime_type=MimeTypes.PLAIN_TEXT.value,
                connector_name="gmail",
            )
            events = await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_txt_document.call_args[1]
        assert call_kwargs["origin"] == "CONNECTOR"

    @pytest.mark.asyncio
    async def test_origin_defaults_to_upload_when_no_connector(self):
        """Origin defaults to UPLOAD when connectorName is empty."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_txt_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                mime_type=MimeTypes.PLAIN_TEXT.value,
                connector_name="",
            )
            events = await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_txt_document.call_args[1]
        assert call_kwargs["origin"] == "UPLOAD"

    @pytest.mark.asyncio
    async def test_md5_check_exception_is_reraised(self):
        """If _check_duplicate_by_md5 raises, the exception propagates."""
        ep, _, _, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}

        with patch.object(
            ep,
            "_check_duplicate_by_md5",
            new_callable=AsyncMock,
            side_effect=RuntimeError("md5 fail"),
        ):
            event_data = _make_event_payload(extension=ExtensionTypes.DOCX.value)
            with pytest.raises(RuntimeError, match="md5 fail"):
                await _drain(ep.on_event(event_data))

    @pytest.mark.asyncio
    async def test_record_name_defaults_to_untitled(self):
        """When recordName is missing from payload, defaults to Untitled-{recordId}."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            payload = {
                "recordId": "rec-1",
                "orgId": "org-1",
                "virtualRecordId": "vr-1",
                "version": 1,
                "connectorName": "",
                "extension": ExtensionTypes.DOCX.value,
                "mimeType": "unknown",
                "buffer": b"data",
                # No recordName key
            }
            event_data = {"payload": payload}
            events = await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_docx_document.call_args[1]
        assert call_kwargs["recordName"] == "Untitled-rec-1"

    @pytest.mark.asyncio
    async def test_docx_mime_type_routes_to_docx_processor(self):
        """DOCX MIME type routes to process_docx_document via elif branch."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.DOCX.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_docx_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_xlsx_mime_type_routes_to_excel_processor(self):
        """XLSX MIME type routes to process_excel_document."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_excel_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.XLSX.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_excel_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_csv_mime_type_routes_to_delimited(self):
        """CSV MIME type routes to process_delimited_document."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_delimited_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.CSV.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_delimited_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_pptx_mime_type_routes_to_pptx(self):
        """PPTX MIME type routes to process_pptx_document."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_pptx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.PPTX.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_pptx_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_markdown_mime_type_routes_to_md(self):
        """Markdown MIME type routes to process_md_document."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_md_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.MARKDOWN.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_md_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_processor_exception_bubbles_up(self):
        """If the downstream processor raises, on_event re-raises."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}

        async def failing_processor(*args, **kwargs):
            raise ValueError("processor broke")
            yield  # noqa: unreachable - needed to make it an async generator

        processor.process_docx_document = MagicMock(side_effect=failing_processor)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.DOCX.value)
            with pytest.raises(ValueError, match="processor broke"):
                await _drain(ep.on_event(event_data))

    @pytest.mark.asyncio
    async def test_pymupdf_env_flag_routes_to_pymupdf(self):
        """ENABLE_PDFPLUMBER_PROCESSOR=true routes to process_pdf_with_pdf_plumber."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_pdf_with_pdf_plumber = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
             patch.object(ep, "_pdf_needs_ocr", new_callable=AsyncMock, return_value=False), \
             patch.dict("os.environ", {"ENABLE_PDFPLUMBER_PROCESSOR": "true"}):
            event_data = _make_event_payload(extension=ExtensionTypes.PDF.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_pdf_with_pdf_plumber.assert_called_once()

    @pytest.mark.asyncio
    async def test_pymupdf_failure_falls_back_to_ocr(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}

        async def pymupdf_fails(*args, **kwargs):
            raise RuntimeError("pymupdf error")
            yield  # noqa: unreachable

        processor.process_pdf_with_pdf_plumber = MagicMock(side_effect=pymupdf_fails)
        processor.process_pdf_document_with_ocr = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
             patch.object(ep, "_pdf_needs_ocr", new_callable=AsyncMock, return_value=False), \
             patch.dict("os.environ", {"ENABLE_PDFPLUMBER_PROCESSOR": "true"}):
            event_data = _make_event_payload(extension=ExtensionTypes.PDF.value)
            events = await _drain(ep.on_event(event_data))

        processor.process_pdf_document_with_ocr.assert_called_once()

    @pytest.mark.asyncio
    async def test_fitz_open_exception_defaults_to_layout_parser(self):
        """If OCR detection fails (e.g. corrupt PDF), routing defaults to layout parser."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_pdf_with_docling = MagicMock(side_effect=_mock_processor_gen)

        with patch.dict("os.environ", {"ENABLE_PDFPLUMBER_PROCESSOR": "false"}), \
             patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
             patch.object(ep, "_pdf_needs_ocr", new_callable=AsyncMock, side_effect=Exception("corrupted pdf")):
            event_data = _make_event_payload(extension=ExtensionTypes.PDF.value)
            await _drain(ep.on_event(event_data))

        processor.process_pdf_with_docling.assert_called_once()


class TestEpubDispatch:
    """EPUB is read by the processor's EPUB reader, never converted to PDF."""

    @pytest.mark.parametrize(
        ("extension", "mime_type"),
        [(ExtensionTypes.EPUB.value, "unknown"), ("unknown", MimeTypes.EPUB.value)],
    )
    @pytest.mark.asyncio
    async def test_epub_routes_to_the_epub_processor(self, extension: str, mime_type: str) -> None:
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_epub_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
             patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
            event_data = _make_event_payload(
                extension=extension, mime_type=mime_type, record_name="book.epub"
            )
            events = await _drain(ep.on_event(event_data))

        processor.process_epub_document.assert_called_once()
        kwargs = processor.process_epub_document.call_args.kwargs
        assert kwargs["epub_binary"] == b"hello"
        assert kwargs["recordName"] == "book.epub"
        processor.process_pdf_with_docling.assert_not_called()
        processor.process_pdf_document_with_ocr.assert_not_called()
        spawn.assert_not_called()
        assert len(events) == 3

    @pytest.mark.asyncio
    async def test_an_epub_failure_bubbles_up(self) -> None:
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}

        processor.process_epub_document = MagicMock(side_effect=RuntimeError("book could not be read"))

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.EPUB.value)
            with pytest.raises(RuntimeError, match="book could not be read"):
                await _drain(ep.on_event(event_data))

        processor.process_pdf_with_docling.assert_not_called()


# ===========================================================================
# on_event - MIME type dispatch branches (Google Workspace, HTML, Blocks, etc.)
# ===========================================================================


class TestOnEventMimeTypeDispatch:
    """Tests for MIME type-based routing in on_event."""

    @pytest.mark.asyncio
    async def test_google_slides_routes_to_pptx_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_pptx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.GOOGLE_SLIDES.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_pptx_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_google_docs_routes_to_docx_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.GOOGLE_DOCS.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_docx_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_google_sheets_routes_to_excel_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_excel_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.GOOGLE_SHEETS.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_excel_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_html_mime_type_routes_to_html_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_html_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.HTML.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_html_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_plain_text_mime_routes_to_txt_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_txt_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.PLAIN_TEXT.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_txt_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_blocks_mime_type_routes_to_blocks_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_blocks = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.BLOCKS.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_blocks.assert_called_once()

    @pytest.mark.asyncio
    async def test_gmail_mime_type_routes_to_gmail_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_gmail_message = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.GMAIL.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_gmail_message.assert_called_once()


# ===========================================================================
# on_event - Extension-based dispatch branches
# ===========================================================================


class TestOnEventExtensionDispatch:
    """Tests for extension-based routing in on_event."""

    @pytest.mark.asyncio
    async def test_doc_extension_routes_to_doc_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_doc_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.DOC.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_doc_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_xls_extension_routes_to_xls_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_xls_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.XLS.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_xls_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_tsv_extension_routes_to_delimited_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_delimited_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.TSV.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_delimited_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_ppt_extension_routes_to_ppt_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_ppt_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.PPT.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_ppt_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_md_extension_routes_to_md_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_md_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.MD.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_md_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_mdx_extension_routes_to_mdx_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_mdx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.MDX.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_mdx_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_txt_extension_routes_to_txt_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_txt_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.TXT.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_txt_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_png_extension_routes_to_image_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_image = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.PNG.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_image.assert_called_once()

    @pytest.mark.asyncio
    async def test_jpg_extension_routes_to_image_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_image = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.JPG.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_image.assert_called_once()

    @pytest.mark.asyncio
    async def test_unsupported_extension_raises(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension="xyz")
            with pytest.raises(Exception, match="Unsupported file extension"):
                await _drain(ep.on_event(event_data))

    @pytest.mark.asyncio
    async def test_html_extension_routes_to_html_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_html_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.HTML.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_html_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_code_mime_routes_to_code_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_code_document = MagicMock(side_effect=_mock_processor_gen)
        processor.process_md_document = MagicMock(side_effect=_mock_processor_gen)
        processor.process_txt_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                extension="exe",
                mime_type="text/x-python",
                record_name="main.py",
            )
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_code_document.assert_called_once()
        processor.process_md_document.assert_not_called()
        processor.process_txt_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_code_file_arriving_as_text_plain_still_reaches_code_processor(self):
        """Connectors walking a git tree default an unrecognised blob's mime to
        text/plain. The PLAIN_TEXT branch returns early, so the code dispatch
        has to precede it or every .jsx is silently parsed as prose."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_code_document = MagicMock(side_effect=_mock_processor_gen)
        processor.process_txt_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                extension="",
                mime_type="text/plain",
                record_name="App.jsx",
            )
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_code_document.assert_called_once()
        processor.process_txt_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_plain_text_that_is_not_code_still_uses_txt_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_code_document = MagicMock(side_effect=_mock_processor_gen)
        processor.process_txt_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                extension="",
                mime_type="text/plain",
                record_name="notes.txt",
            )
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_txt_document.assert_called_once()
        processor.process_code_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_code_extension_routes_to_code_processor_when_mime_unknown(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_code_document = MagicMock(side_effect=_mock_processor_gen)
        processor.process_md_document = MagicMock(side_effect=_mock_processor_gen)
        processor.process_txt_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                extension="py",
                mime_type="application/octet-stream",
                record_name="main.py",
            )
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_code_document.assert_called_once()
        processor.process_md_document.assert_not_called()
        processor.process_txt_document.assert_not_called()


# ===========================================================================
# _check_duplicate_by_md5 - string content path
# ===========================================================================


class TestCheckDuplicateMd5StringContent:
    """Test md5 calculation with string content — uses _normalize_content_for_dedup."""

    @pytest.mark.asyncio
    async def test_string_content_md5_calculated(self):
        """String content is encoded to bytes, normalized, then hashed."""
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = []
        doc = {"_key": "r1", "recordType": "FILE"}

        result = await ep._check_duplicate_by_md5("hello world", doc)

        assert result.skip_indexing is False
        assert doc.get("md5Checksum") is not None
        # Content goes through _normalize_content_for_dedup (plain text returns as-is)
        normalized = ep._normalize_content_for_dedup(b"hello world", record_type="FILE")
        expected = hashlib.md5(normalized).hexdigest()
        assert doc["md5Checksum"] == expected

    @pytest.mark.asyncio
    async def test_bytes_content_md5_calculated(self):
        """Bytes content is normalized then hashed."""
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = []
        doc = {"_key": "r1", "recordType": "FILE"}

        result = await ep._check_duplicate_by_md5(b"hello world", doc)

        assert result.skip_indexing is False
        normalized = ep._normalize_content_for_dedup(b"hello world", record_type="FILE")
        expected = hashlib.md5(normalized).hexdigest()
        assert doc["md5Checksum"] == expected


# ===========================================================================
# on_event - update event creates new virtual_record_id
# ===========================================================================


class TestOnEventUpdateEvent:
    """Test UPDATE_RECORD / REINDEX_RECORD reconciliation logic."""

    @pytest.mark.asyncio
    async def test_update_non_reconciliation_type_generates_new_vrid(self):
        """Non-reconciliation types (e.g. XLSX) get a new UUID on update."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "FILE",
            "virtualRecordId": "old-vr-id",
        }
        processor.process_excel_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                extension=ExtensionTypes.XLSX.value,
                event_type=EventTypes.UPDATE_RECORD.value,
                virtual_record_id="old-vr-id",
            )
            events = await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_excel_document.call_args[1]
        assert call_kwargs["virtual_record_id"] != "old-vr-id"
        assert len(call_kwargs["virtual_record_id"]) == 36

    @pytest.mark.asyncio
    async def test_update_reconciliation_type_1to1_keeps_vrid(self):
        """Reconciliation type with 1:1 mapping keeps existing vrid."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "SQL_TABLE",
            "virtualRecordId": "existing-vrid",
        }
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=[{"_key": "rec-1"}])
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                mime_type=MimeTypes.SQL_TABLE.value,
                extension=ExtensionTypes.SQL_TABLE.value,
                event_type=EventTypes.UPDATE_RECORD.value,
                virtual_record_id="existing-vrid",
            )
            events = await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_sql_structured_data.call_args[1]
        assert call_kwargs["virtual_record_id"] == "existing-vrid"
        assert call_kwargs["prev_virtual_record_id"] == "existing-vrid"

    @pytest.mark.asyncio
    async def test_update_reconciliation_type_nto1_isolates_vrid(self):
        """Reconciliation type with N:1 mapping generates new vrid."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "SQL_TABLE",
            "virtualRecordId": "shared-vrid",
        }
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=[
            {"_key": "rec-1"}, {"_key": "rec-2"},
        ])
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                mime_type=MimeTypes.SQL_TABLE.value,
                extension=ExtensionTypes.SQL_TABLE.value,
                event_type=EventTypes.UPDATE_RECORD.value,
                virtual_record_id="shared-vrid",
            )
            events = await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_sql_structured_data.call_args[1]
        assert call_kwargs["virtual_record_id"] != "shared-vrid"
        assert len(call_kwargs["virtual_record_id"]) == 36

    @pytest.mark.asyncio
    async def test_reindex_event_uses_same_reconciliation_logic(self):
        """REINDEX_RECORD follows the same reconciliation path as UPDATE_RECORD."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "SQL_VIEW",
            "virtualRecordId": "reindex-vrid",
        }
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=[{"_key": "rec-1"}])
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                mime_type=MimeTypes.SQL_VIEW.value,
                extension=ExtensionTypes.SQL_VIEW.value,
                event_type=EventTypes.REINDEX_RECORD.value,
                virtual_record_id="reindex-vrid",
            )
            events = await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_sql_structured_data.call_args[1]
        assert call_kwargs["virtual_record_id"] == "reindex-vrid"

    @pytest.mark.asyncio
    async def test_reconciliation_type_no_existing_vrid_treats_as_new(self):
        """Reconciliation type with no existing vrid treats as new."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "SQL_TABLE",
            "virtualRecordId": None,
        }
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                mime_type=MimeTypes.SQL_TABLE.value,
                extension=ExtensionTypes.SQL_TABLE.value,
                event_type=EventTypes.UPDATE_RECORD.value,
                virtual_record_id=None,
            )
            await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_sql_structured_data.call_args[1]
        assert len(call_kwargs["virtual_record_id"]) == 36


# ===========================================================================
# on_event - docling fallback to OCR on docling failure
# ===========================================================================


class TestOnEventDoclingFallback:
    """Test docling failure falls back to OCR handler."""

    @pytest.mark.asyncio
    async def test_docling_failure_falls_back_to_ocr(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}

        async def docling_fails(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.DOCLING_FAILED, data=PipelineEventData(record_id="rec-1"))

        processor.process_pdf_with_docling = MagicMock(side_effect=docling_fails)
        processor.process_pdf_document_with_ocr = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
             patch.object(ep, "_pdf_needs_ocr", new_callable=AsyncMock, return_value=False), \
             patch.dict("os.environ", {"ENABLE_PDFPLUMBER_PROCESSOR": "false"}):
            event_data = _make_event_payload(extension=ExtensionTypes.PDF.value)
            events = await _drain(ep.on_event(event_data))

        processor.process_pdf_document_with_ocr.assert_called_once()


# ===========================================================================
# Coverage: _get_pdf_ocr_detection_worker_count (lines 32-35)
# ===========================================================================


class TestPdfOcrDetectionWorkerCount:
    """Tests for _get_pdf_ocr_detection_worker_count."""

    def test_valid_env_value(self):
        from app.events.events import _get_pdf_ocr_detection_worker_count
        with patch.dict("os.environ", {"PDF_OCR_DETECTION_WORKERS": "4"}):
            result = _get_pdf_ocr_detection_worker_count()
        assert result == 4

    def test_invalid_env_value_returns_1(self):
        """Invalid integer returns 1 (lines 34-35)."""
        from app.events.events import _get_pdf_ocr_detection_worker_count
        with patch.dict("os.environ", {"PDF_OCR_DETECTION_WORKERS": "not-a-number"}):
            result = _get_pdf_ocr_detection_worker_count()
        assert result == 1

    def test_zero_value_returns_1(self):
        """Zero returns 1 (max(1,...))."""
        from app.events.events import _get_pdf_ocr_detection_worker_count
        with patch.dict("os.environ", {"PDF_OCR_DETECTION_WORKERS": "0"}):
            result = _get_pdf_ocr_detection_worker_count()
        assert result == 1

    def test_no_env_uses_cpu_count(self):
        from app.events.events import _get_pdf_ocr_detection_worker_count
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("PDF_OCR_DETECTION_WORKERS", None)
            result = _get_pdf_ocr_detection_worker_count()
        assert result >= 1


# ===========================================================================
# Coverage: _detect_pdf_needs_ocr (lines 52-72)
# ===========================================================================


class TestDetectPdfNeedsOcr:
    """Tests for _detect_pdf_needs_ocr."""

    def test_empty_pdf_returns_false(self):
        """PDF with 0 pages returns False (line 57)."""
        from app.events.events import _detect_pdf_needs_ocr

        mock_pdf = MagicMock()
        mock_pdf.pages = []
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_pdf
        mock_cm.__exit__.return_value = None
        with patch("app.events.events.pdfplumber.open", return_value=mock_cm):
            assert _detect_pdf_needs_ocr(b"fake pdf") is False

    def test_all_pages_need_ocr(self):
        """All pages need OCR -> True (lines 62-66)."""
        from app.events.events import _detect_pdf_needs_ocr

        pages = [MagicMock() for _ in range(4)]
        mock_pdf = MagicMock()
        mock_pdf.pages = pages
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_pdf
        mock_cm.__exit__.return_value = None
        with patch("app.events.events.pdfplumber.open", return_value=mock_cm), patch(
            "app.events.events.OCRStrategy.needs_ocr", return_value=True
        ):
            assert _detect_pdf_needs_ocr(b"fake pdf") is True

    def test_no_pages_need_ocr_early_exit(self):
        """Early exit when remaining pages can't reach threshold (lines 68-70)."""
        from app.events.events import _detect_pdf_needs_ocr

        pages = [MagicMock() for _ in range(4)]
        mock_pdf = MagicMock()
        mock_pdf.pages = pages
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_pdf
        mock_cm.__exit__.return_value = None
        with patch("app.events.events.pdfplumber.open", return_value=mock_cm), patch(
            "app.events.events.OCRStrategy.needs_ocr", return_value=False
        ):
            assert _detect_pdf_needs_ocr(b"fake pdf") is False


# ===========================================================================
# Coverage: on_event early return paths (lines 244-245, 253-254, 261-262, 280-281)
# ===========================================================================


class TestOnEventEarlyReturns:
    """Test on_event early return edge cases."""

    @pytest.mark.asyncio
    async def test_no_payload_raises_terminal_error(self):
        """A malformed envelope must fail loudly and once.

        Returning bare here yielded neither completion event, which the
        consumers read as an unexplained failure and retried to the dead-letter
        ceiling — three deliveries and no diagnosis for something no retry can
        fix.
        """
        ep, _logger, _, _ = _make_event_processor()
        event_data = {"eventType": "NEW_RECORD"}  # no payload key

        with pytest.raises(ProcessingError):
            await _drain(ep.on_event(event_data))

    @pytest.mark.asyncio
    async def test_no_record_id_raises_terminal_error(self):
        """A payload with no recordId can never come good on a retry."""
        ep, _logger, _, _ = _make_event_processor()
        event_data = {"payload": {"orgId": "org-1"}}  # no recordId

        with pytest.raises(ProcessingError):
            await _drain(ep.on_event(event_data))

    @pytest.mark.asyncio
    async def test_record_not_found_drains_the_message(self):
        """A deleted record is drained, not retried.

        Unlike the two malformed cases above this is legitimately reachable —
        a record can be deleted between an event being published and consumed —
        so it completes the pipeline rather than raising.
        """
        ep, _logger, _, gp = _make_event_processor()
        gp.get_document.return_value = None
        event_data = _make_event_payload()

        events = await _drain(ep.on_event(event_data))

        assert [e.event for e in events] == [
            IndexingEvent.PARSING_COMPLETE,
            IndexingEvent.INDEXING_COMPLETE,
        ]

    @pytest.mark.asyncio
    async def test_a_failed_lookup_is_not_drained_as_a_deletion(self):
        """The graph being unreachable must not read as "this record is gone".

        Draining is permanent: the message is acknowledged and the record sits
        at QUEUED until the stranded sweep republishes it an hour later. During
        a graph restart every record in flight took that path.
        """
        ep, _logger, _, gp = _make_event_processor()

        async def unreachable_graph(*_args, raise_on_error: bool = False, **_kwargs):
            # What both providers do: swallow and answer None unless asked not to.
            # A double that raised either way would pass without the fix.
            if raise_on_error:
                raise RuntimeError("graph is restarting")
            return None

        gp.get_document.side_effect = unreachable_graph

        with pytest.raises(RuntimeError):
            await _drain(ep.on_event(_make_event_payload()))

    @pytest.mark.asyncio
    async def test_the_record_lookup_asks_for_failures_to_be_raised(self):
        """`raise_on_error` is what makes the None above mean "deleted"."""
        ep, _logger, _, gp = _make_event_processor()
        gp.get_document.return_value = None

        await _drain(ep.on_event(_make_event_payload()))

        assert gp.get_document.await_args.kwargs.get("raise_on_error") is True

    @pytest.mark.asyncio
    async def test_no_buffer_proceeds_with_none_content(self):
        """None buffer proceeds (no early return), duplicate check runs with None content."""
        ep, logger, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(buffer=None, extension=ExtensionTypes.DOCX.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2


# ===========================================================================
# Coverage: duplicate record handling (lines 208-214, 290-293)
# ===========================================================================


class TestOnEventDuplicate:
    """Test duplicate detection in on_event."""

    @pytest.mark.asyncio
    async def test_duplicate_detected_yields_events(self):
        """Duplicate detected yields parsing_complete + indexing_complete (lines 290-293)."""
        ep, _, _, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}

        with patch.object(
            ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
            return_value=DedupDecision(virtual_record_id=None, skip_indexing=True),
        ):
            event_data = _make_event_payload()
            events = await _drain(ep.on_event(event_data))

        assert any(e.event == "parsing_complete" for e in events)
        assert any(e.event == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_duplicate_skip_invalidates_accessible_records_cache(self):
        """When dedup skips indexing, the accessible-records cache must still
        be invalidated so the newly attached record is searchable immediately.

        Without this, a KB file re-uploaded with identical content (matched by
        MD5 to an existing record) would be marked COMPLETED but invisible to
        search until the cache TTL expires.
        """
        ep, _, _, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "FILE",
            "connectorName": "KB",
            "connectorId": "hidden-kb-1",
            "orgId": "org-1",
        }

        with patch.object(
            ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
            return_value=DedupDecision(virtual_record_id=None, skip_indexing=True),
        ), patch(
            "app.events.events.notify_record_indexed", new_callable=AsyncMock,
        ) as mock_notify:
            event_data = _make_event_payload(
                connector_name="KB",
            )
            events = await _drain(ep.on_event(event_data))

        mock_notify.assert_awaited_once()
        call_kwargs = mock_notify.call_args[1]
        assert call_kwargs["connector_name"] == "KB"
        assert call_kwargs["connector_id"] == "hidden-kb-1"
        assert call_kwargs["org_id"] == "org-1"

    @pytest.mark.asyncio
    async def test_check_duplicate_in_progress_handling(self):
        """Duplicate record in IN_PROGRESS status gets QUEUED (lines 208-214)."""
        ep, _, _, gp = _make_event_processor()

        # find_duplicate_records returns an in-progress duplicate (no processed one)
        gp.find_duplicate_records = AsyncMock(return_value=[
            {"_key": "dup-1", "indexingStatus": ProgressStatus.IN_PROGRESS.value}
        ])
        gp.batch_update_nodes = AsyncMock()

        doc = {"_key": "rec-1", "md5Checksum": "abc123", "recordType": "FILE", "sizeInBytes": 100}
        result = await ep._check_duplicate_by_md5(b"hello world", doc)
        assert result.skip_indexing is True
        assert doc["indexingStatus"] == ProgressStatus.QUEUED.value


# ===========================================================================
# Coverage: OCR exception and OCR path (lines 410, 417-427)
# ===========================================================================


class TestOnEventOcrPath:
    """Test PDF OCR processing path."""

    @pytest.mark.asyncio
    async def test_ocr_check_exception_defaults_to_false(self):
        """OCR check failure defaults to no OCR (line 410)."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_pdf_with_docling = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
             patch.object(ep, "_pdf_needs_ocr", new_callable=AsyncMock, side_effect=RuntimeError("OCR check failed")), \
             patch.dict("os.environ", {"ENABLE_PDFPLUMBER_PROCESSOR": "false"}):
            event_data = _make_event_payload(extension="pdf")
            events = await _drain(ep.on_event(event_data))

        # Should fall through to docling since OCR check failed (needs_ocr=False)
        processor.process_pdf_with_docling.assert_called_once()

    @pytest.mark.asyncio
    async def test_pdf_needs_ocr_uses_ocr_handler(self):
        """PDF that needs OCR goes through OCR handler (lines 417-427)."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_pdf_document_with_ocr = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
             patch.object(ep, "_pdf_needs_ocr", new_callable=AsyncMock, return_value=True):
            event_data = _make_event_payload(extension="pdf")
            events = await _drain(ep.on_event(event_data))

        processor.process_pdf_document_with_ocr.assert_called_once()


# ===========================================================================
# _normalize_content_for_dedup
# ===========================================================================


class TestNormalizeContentForDedup:
    """Tests for _normalize_content_for_dedup."""

    def test_plain_bytes_returned_as_is(self):
        """Non-JSON, non-HTML bytes are returned unchanged."""
        ep, _, _, _ = _make_event_processor()
        content = b"just plain text"
        result = ep._normalize_content_for_dedup(content)
        assert result == content

    def test_html_mime_type_strips_scripts_and_extracts_text(self):
        """HTML content strips script/style tags and extracts text."""
        ep, _, _, _ = _make_event_processor()
        html = b"<html><script>var x=1;</script><body><p>Hello</p></body></html>"
        result = ep._normalize_content_for_dedup(html, mime_type="text/html")
        assert b"var x=1" not in result
        assert b"Hello" in result

    def test_html_strips_local_id_and_emoji_attrs(self):
        """HTML normalization removes local-id, id, data-emoji-* attributes."""
        ep, _, _, _ = _make_event_processor()
        html = b'<div local-id="abc" id="x" data-emoji-id="e1" data-emoji-fallback="f">Text</div>'
        result = ep._normalize_content_for_dedup(html, mime_type="text/html")
        assert b"Text" in result
        assert b"abc" not in result

    def test_confluence_page_record_type_triggers_html_normalization(self):
        """CONFLUENCE_PAGE record type triggers HTML normalization even without HTML mime."""
        ep, _, _, _ = _make_event_processor()
        html = b"<p>Confluence content</p><style>.x{}</style>"
        result = ep._normalize_content_for_dedup(html, record_type="CONFLUENCE_PAGE")
        assert b".x{}" not in result
        assert b"Confluence content" in result

    def test_confluence_blogpost_triggers_html_normalization(self):
        ep, _, _, _ = _make_event_processor()
        html = b"<p>Blog</p>"
        result = ep._normalize_content_for_dedup(html, record_type="CONFLUENCE_BLOGPOST")
        assert b"Blog" in result

    def test_comment_record_type_triggers_html_normalization(self):
        ep, _, _, _ = _make_event_processor()
        html = b"<p>A comment</p>"
        result = ep._normalize_content_for_dedup(html, record_type="COMMENT")
        assert b"A comment" in result

    def test_inline_comment_record_type_triggers_html_normalization(self):
        ep, _, _, _ = _make_event_processor()
        html = b"<p>Inline</p>"
        result = ep._normalize_content_for_dedup(html, record_type="INLINE_COMMENT")
        assert b"Inline" in result

    def test_html_with_empty_text_returns_original(self):
        """HTML that produces empty text returns original content."""
        ep, _, _, _ = _make_event_processor()
        html = b"<script>only scripts</script>"
        result = ep._normalize_content_for_dedup(html, mime_type="text/html")
        assert result == html

    def test_json_with_block_groups_extracts_data(self):
        """JSON with block_groups extracts data fields."""
        ep, _, _, _ = _make_event_processor()
        payload = {
            "block_groups": [
                {"data": {"text": "hello"}},
                {"data": {"text": "world"}},
            ]
        }
        content = json.dumps(payload).encode("utf-8")
        result = ep._normalize_content_for_dedup(content)
        assert b"hello" in result
        assert b"world" in result

    def test_json_with_blocks_extracts_data(self):
        """JSON with blocks key extracts data fields."""
        ep, _, _, _ = _make_event_processor()
        payload = {"blocks": [{"data": {"val": 42}}]}
        content = json.dumps(payload).encode("utf-8")
        result = ep._normalize_content_for_dedup(content)
        assert b"42" in result

    def test_json_block_groups_with_null_data_skipped(self):
        """Block groups with None data are skipped."""
        ep, _, _, _ = _make_event_processor()
        payload = {"block_groups": [{"data": None}, {"data": {"x": 1}}]}
        content = json.dumps(payload).encode("utf-8")
        result = ep._normalize_content_for_dedup(content)
        assert b'"x": 1' in result

    def test_json_block_groups_all_null_data_returns_original(self):
        """Block groups where all data is None returns original content."""
        ep, _, _, _ = _make_event_processor()
        payload = {"block_groups": [{"data": None}]}
        content = json.dumps(payload).encode("utf-8")
        result = ep._normalize_content_for_dedup(content)
        assert result == content

    def test_plain_json_dict_sorted(self):
        """Plain JSON dict is re-serialized with sorted keys."""
        ep, _, _, _ = _make_event_processor()
        payload = {"z": 1, "a": 2}
        content = json.dumps(payload).encode("utf-8")
        result = ep._normalize_content_for_dedup(content)
        assert result == json.dumps({"a": 2, "z": 1}, sort_keys=True).encode("utf-8")

    def test_json_list_returns_original(self):
        """JSON that parses to a list (not dict) returns original content."""
        ep, _, _, _ = _make_event_processor()
        content = b"[1, 2, 3]"
        result = ep._normalize_content_for_dedup(content)
        assert result == content

    def test_invalid_json_returns_original(self):
        """Invalid JSON returns original content."""
        ep, _, _, _ = _make_event_processor()
        content = b"not json at all"
        result = ep._normalize_content_for_dedup(content)
        assert result == content


# ===========================================================================
# _check_duplicate_by_md5 - source key fallback to "id"
# ===========================================================================


class TestCheckDuplicateSourceKeyFallback:
    """Test processed_duplicate source key falls back to 'id' field."""

    @pytest.mark.asyncio
    async def test_processed_duplicate_uses_id_when_key_missing(self):
        """copy_document_relationships uses processed_duplicate['id'] when _key is missing."""
        ep, _, _, gp = _make_event_processor()
        processed = {
            "id": "dup-src-id",
            "virtualRecordId": "vr-1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
            "summaryDocumentId": "sum-1",
        }
        gp.find_duplicate_records.return_value = [processed]
        doc = {"_key": "target", "md5Checksum": "abc", "recordType": "FILE", "sizeInBytes": 10}

        with patch("app.events.events.get_epoch_timestamp_in_ms", return_value=200):
            result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        gp.copy_document_relationships.assert_awaited_once_with("dup-src-id", "target")


# ===========================================================================
# on_event - SQL_TABLE / SQL_VIEW routing
# ===========================================================================


class TestOnEventSqlRouting:
    """Tests for SQL_TABLE and SQL_VIEW dispatch."""

    @pytest.mark.asyncio
    async def test_sql_table_mime_routes_to_sql_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "SQL_TABLE"}
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.SQL_TABLE.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        call_kwargs = processor.process_sql_structured_data.call_args[1]
        assert call_kwargs["record_type"] == "SQL_TABLE"

    @pytest.mark.asyncio
    async def test_sql_table_extension_routes_to_sql_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "SQL_TABLE"}
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.SQL_TABLE.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_sql_structured_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_sql_view_mime_routes_to_sql_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "SQL_VIEW"}
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.SQL_VIEW.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        call_kwargs = processor.process_sql_structured_data.call_args[1]
        assert call_kwargs["record_type"] == "SQL_VIEW"

    @pytest.mark.asyncio
    async def test_sql_view_extension_routes_to_sql_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "SQL_VIEW"}
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.SQL_VIEW.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_sql_structured_data.assert_called_once()


# ===========================================================================
# on_event - event_type passed to processors
# ===========================================================================


class TestOnEventPassesEventType:
    """Verify event_type is forwarded to processor methods."""

    @pytest.mark.asyncio
    async def test_event_type_passed_to_docx_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                extension=ExtensionTypes.DOCX.value,
                event_type=EventTypes.NEW_RECORD.value,
            )
            await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_docx_document.call_args[1]
        assert call_kwargs["event_type"] == EventTypes.NEW_RECORD.value

    @pytest.mark.asyncio
    async def test_event_type_passed_to_image_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_image = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                extension=ExtensionTypes.PNG.value,
                event_type=EventTypes.NEW_RECORD.value,
            )
            await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_image.call_args[1]
        assert call_kwargs["event_type"] == EventTypes.NEW_RECORD.value

    @pytest.mark.asyncio
    async def test_event_type_passed_to_google_slides(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_pptx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                mime_type=MimeTypes.GOOGLE_SLIDES.value,
                event_type=EventTypes.UPDATE_RECORD.value,
            )
            await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_pptx_document.call_args[1]
        assert call_kwargs["event_type"] == EventTypes.UPDATE_RECORD.value


# ===========================================================================
# on_event - prev_virtual_record_id passed to processor
# ===========================================================================


class TestOnEventPrevVirtualRecordId:
    """Verify prev_virtual_record_id is forwarded to processor calls."""

    @pytest.mark.asyncio
    async def test_new_record_passes_prev_vrid_as_none(self):
        """NEW_RECORD event passes prev_virtual_record_id=None."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                extension=ExtensionTypes.DOCX.value,
                event_type=EventTypes.NEW_RECORD.value,
            )
            await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_docx_document.call_args[1]
        assert call_kwargs["prev_virtual_record_id"] is None

    @pytest.mark.asyncio
    async def test_update_reconciliation_type_passes_prev_vrid(self):
        """UPDATE on reconciliation type forwards prev_virtual_record_id."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "SQL_TABLE",
            "virtualRecordId": "prev-vrid",
        }
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=[{"_key": "rec-1"}])
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                mime_type=MimeTypes.SQL_TABLE.value,
                extension=ExtensionTypes.SQL_TABLE.value,
                event_type=EventTypes.UPDATE_RECORD.value,
                virtual_record_id="prev-vrid",
            )
            await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_sql_structured_data.call_args[1]
        assert call_kwargs["prev_virtual_record_id"] == "prev-vrid"


class TestVectorMembershipHooks:
    @pytest.mark.asyncio
    async def test_duplicate_attach_syncs_membership(self):
        ep, _, _, gp = _make_event_processor()
        processed = {
            "_key": "dup-p",
            "virtualRecordId": "vr-shared",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
            "summaryDocumentId": "sum-p",
        }
        gp.find_duplicate_records.return_value = [processed]
        doc = {"_key": "r-new", "md5Checksum": "abc", "recordType": "FILE", "sizeInBytes": 10}

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock) as sync:
            with patch("app.events.events.get_epoch_timestamp_in_ms", return_value=100):
                result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        sync.assert_awaited_once_with("vr-shared")

    @pytest.mark.asyncio
    async def test_n1_vrid_split_rewrites_old_vrid(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "SQL_TABLE",
            "virtualRecordId": "shared-vrid",
        }
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1", "rec-2"])
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            with patch.object(ep, "_rewrite_or_delete_vrid_vectors", new_callable=AsyncMock) as rewrite:
                event_data = _make_event_payload(
                    mime_type=MimeTypes.SQL_TABLE.value,
                    extension=ExtensionTypes.SQL_TABLE.value,
                    event_type=EventTypes.UPDATE_RECORD.value,
                    virtual_record_id="shared-vrid",
                )
                await _drain(ep.on_event(event_data))

        rewrite.assert_awaited_once_with("shared-vrid")
        call_kwargs = processor.process_sql_structured_data.call_args[1]
        assert call_kwargs["virtual_record_id"] != "shared-vrid"
        assert call_kwargs["prev_virtual_record_id"] == "shared-vrid"

    @pytest.mark.asyncio
    async def test_duplicate_attach_skips_sync_when_vrid_missing(self):
        ep, _, _, gp = _make_event_processor()
        processed = {
            "_key": "dup-empty",
            "indexingStatus": ProgressStatus.EMPTY.value,
        }
        gp.find_duplicate_records.return_value = [processed]
        doc = {"_key": "r-new", "md5Checksum": "abc", "recordType": "FILE", "sizeInBytes": 10}

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock) as sync:
            with patch("app.events.events.get_epoch_timestamp_in_ms", return_value=100):
                result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        sync.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_one_to_one_reconciliation_does_not_rewrite(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "SQL_TABLE",
            "virtualRecordId": "solo-vrid",
        }
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            with patch.object(ep, "_rewrite_or_delete_vrid_vectors", new_callable=AsyncMock) as rewrite:
                event_data = _make_event_payload(
                    mime_type=MimeTypes.SQL_TABLE.value,
                    extension=ExtensionTypes.SQL_TABLE.value,
                    event_type=EventTypes.UPDATE_RECORD.value,
                    virtual_record_id="solo-vrid",
                )
                await _drain(ep.on_event(event_data))

        rewrite.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_delegates_to_indexing_pipeline(self):
        ep, _, processor, _ = _make_event_processor()
        processor.indexing_pipeline.sync_vector_membership = AsyncMock()

        await ep.sync_vector_membership("vr-1")

        processor.indexing_pipeline.sync_vector_membership.assert_awaited_once_with("vr-1")

    @pytest.mark.asyncio
    async def test_sync_without_pipeline_fails_the_event(self):
        """Must not acknowledge work that never happened.

        The handler yields INDEXING_COMPLETE straight after this call, so
        returning quietly would ack a syncVectorMembership event whose update was
        never applied, with nothing to revisit it. IndexingError is transient, so
        an event arriving before the pipeline is wired succeeds on retry and a
        real misconfiguration dead-letters visibly.
        """
        from app.exceptions.indexing_exceptions import IndexingError
        from app.services.messaging.error_classifier import (
            MessageErrorClassifier,
            MessageErrorType,
        )

        ep, _, processor, _ = _make_event_processor()
        processor.indexing_pipeline = None

        with pytest.raises(IndexingError) as exc:
            await ep.sync_vector_membership("vr-1")

        assert (
            MessageErrorClassifier.classify_by_exception(exc.value)
            == MessageErrorType.TRANSIENT
        )

    @pytest.mark.asyncio
    async def test_sync_ignores_blank_vrid(self):
        ep, _, processor, _ = _make_event_processor()
        processor.indexing_pipeline.sync_vector_membership = AsyncMock()

        await ep.sync_vector_membership("")

        processor.indexing_pipeline.sync_vector_membership.assert_not_awaited()



from app.exceptions.indexing_exceptions import IndexingError  # noqa: E402


def _fail_every_write_except_md5():
    """`_check_duplicate_by_md5` persists md5Checksum before it reaches any of
    the writes under test, so a blanket failure would trip that one instead."""
    async def _update(record_id, collection, fields):
        return "md5Checksum" in fields

    return AsyncMock(side_effect=_update)



class TestFailedGraphWritesAreNotReportedAsSuccess:
    """A failed write must not be turned into "skip indexing".

    `on_event` answers `skip_indexing=True` by emitting PARSING_COMPLETE and
    INDEXING_COMPLETE and consuming the message, and the reconciliation sweep in
    `indexing_main` only revisits QUEUED/IN_PROGRESS records. So a write that
    quietly failed here strands a record that is neither indexed nor ever looked
    at again. `IndexingError` classifies as transient: the consumer redelivers,
    and a persistent failure dead-letters visibly.
    """

    @pytest.mark.asyncio
    async def test_failed_md5_write_raises(self):
        ep, _, _, gp = _make_event_processor()
        gp.update_node = AsyncMock(return_value=False)
        doc = {"_key": "r1", "recordType": "FILE", "sizeInBytes": 10}

        with pytest.raises(IndexingError, match="md5Checksum"):
            await ep._check_duplicate_by_md5(b"payload", doc)

    @pytest.mark.asyncio
    async def test_failed_duplicate_field_write_raises(self):
        """Without this the record is marked complete with no virtualRecordId,
        so it has neither vectors of its own nor a share of the duplicate's."""
        ep, gp = _make_multi_collection_event_processor()
        gp.find_duplicate_records.return_value = [{
            "_key": "dup-1",
            "connectorName": "GOOGLE_DRIVE",
            "virtualRecordId": "vr-1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
        }]
        gp.update_node = _fail_every_write_except_md5()
        doc = {
            "_key": "r1", "md5Checksum": "abc", "connectorName": "GOOGLE_DRIVE",
            "recordType": "FILE", "sizeInBytes": 10,
        }

        with pytest.raises(IndexingError, match="duplicate record fields"):
            await ep._check_duplicate_by_md5(b"payload", doc)

    @pytest.mark.asyncio
    async def test_failed_relationship_copy_raises(self):
        """The record would otherwise be marked COMPLETED while missing the
        departments/categories/topics edges it inherited the content of."""
        ep, gp = _make_multi_collection_event_processor()
        gp.find_duplicate_records.return_value = [{
            "_key": "dup-1",
            "connectorName": "GOOGLE_DRIVE",
            "virtualRecordId": "vr-1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
        }]
        gp.copy_document_relationships = AsyncMock(return_value=False)
        doc = {
            "_key": "r1", "md5Checksum": "abc", "connectorName": "GOOGLE_DRIVE",
            "recordType": "FILE", "sizeInBytes": 10,
        }

        with pytest.raises(IndexingError, match="relationships"):
            await ep._check_duplicate_by_md5(b"payload", doc)

    @pytest.mark.asyncio
    async def test_failed_queued_write_raises(self):
        """The in-flight branch: QUEUED is the only handle the sweeper has, so
        losing that write is what strands the record for good."""
        ep, gp = _make_multi_collection_event_processor()
        gp.find_duplicate_records.return_value = [{
            "_key": "dup-1",
            "connectorName": "GOOGLE_DRIVE",
            "indexingStatus": ProgressStatus.IN_PROGRESS.value,
        }]
        gp.update_node = _fail_every_write_except_md5()
        doc = {
            "_key": "r1", "md5Checksum": "abc", "connectorName": "GOOGLE_DRIVE",
            "recordType": "FILE", "sizeInBytes": 10,
        }

        with pytest.raises(IndexingError, match="QUEUED"):
            await ep._check_duplicate_by_md5(b"payload", doc)

    @pytest.mark.asyncio
    async def test_a_successful_in_flight_duplicate_still_skips(self):
        """The happy path is unchanged: raising is reserved for real failures."""
        ep, gp = _make_multi_collection_event_processor()
        gp.find_duplicate_records.return_value = [{
            "_key": "dup-1",
            "connectorName": "GOOGLE_DRIVE",
            "indexingStatus": ProgressStatus.IN_PROGRESS.value,
        }]
        doc = {
            "_key": "r1", "md5Checksum": "abc", "connectorName": "GOOGLE_DRIVE",
            "recordType": "FILE", "sizeInBytes": 10,
        }

        result = await ep._check_duplicate_by_md5(b"payload", doc)

        assert result.skip_indexing is True
        assert doc["indexingStatus"] == ProgressStatus.QUEUED.value


# ===========================================================================
# Duplicate attach: ordering, the parked-duplicate recheck, and releasing the
# records parked behind a twin that just finished
# ===========================================================================

from app.events.dedup import ReleaseAction  # noqa: E402

_TWIN = {
    "_key": "twin",
    "orgId": "org-1",
    "md5Checksum": "abc",
    "recordType": "FILE",
    "sizeInBytes": 10,
    "virtualRecordId": "vr-1",
    "summaryDocumentId": "sum-1",
    "indexingStatus": ProgressStatus.COMPLETED.value,
    "extractionStatus": ProgressStatus.COMPLETED.value,
}


def _twin(**overrides):
    return {**_TWIN, **overrides}


def _parked(key, **overrides):
    return {
        "_key": key,
        "orgId": "org-1",
        "md5Checksum": "abc",
        "recordType": "FILE",
        "sizeInBytes": 10,
        "connectorId": f"kb-{key}",
        "indexingStatus": ProgressStatus.QUEUED.value,
        "virtualRecordId": None,
        "summaryDocumentId": None,
        **overrides,
    }


def _kind(fields, kwargs):
    if "indexingStatus" in fields:
        return "status"
    if kwargs.get("match_virtual_record_id"):
        return "rollback"
    return "identity"


def _conditional_writes(gp, kind):
    """``update_record_if`` calls of one kind: identity, status or rollback."""
    return [
        (c.args[0], c.args[1], c.kwargs)
        for c in gp.update_record_if.await_args_list
        if _kind(c.args[1], c.kwargs) == kind
    ]


def _update_record_if(status=True, identity=True):
    """A conditional write whose answer depends on what is being written."""
    async def _impl(record_id, fields, **kwargs):
        answer = {"status": status, "identity": identity, "rollback": True}[
            _kind(fields, kwargs)
        ]
        if isinstance(answer, Exception):
            raise answer
        return answer
    return AsyncMock(side_effect=_impl)


def _recording_graph(gp, order):
    """Record the attach steps, in the order they happen, into ``order``."""
    async def _copy(src, dst):
        order.append(("edges", dst))
        return True

    async def _update_node(record_id, collection, fields):
        if "virtualRecordId" in fields:
            order.append(("identity", record_id))
        return True

    async def _update_if(record_id, fields, **kwargs):
        order.append((_kind(fields, kwargs), record_id))
        return True

    gp.copy_document_relationships = AsyncMock(side_effect=_copy)
    gp.update_node = AsyncMock(side_effect=_update_node)
    gp.update_record_if = AsyncMock(side_effect=_update_if)


class TestProcessedTwinAttachOrdering:
    """The same-collection attach writes the record's status last.

    The record handler drops a redelivered newRecord for a COMPLETED record.
    Writing COMPLETED first turned any failure in the edge copy or the
    membership sync into a permanent one: the record claimed to be indexed
    while its KB was missing from the VRID's vector membership.
    """

    @pytest.mark.asyncio
    async def test_status_is_written_last_after_edges_identity_and_sync(self):
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = [_twin()]
        order = []
        _recording_graph(gp, order)
        doc = _parked("r1", indexingStatus=ProgressStatus.NOT_STARTED.value)

        async def _sync(vrid):
            order.append(("sync", vrid))

        with patch.object(ep, "sync_vector_membership", side_effect=_sync):
            result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        assert order == [("edges", "r1"), ("identity", "r1"), ("sync", "vr-1"), ("status", "r1")]

    @pytest.mark.asyncio
    async def test_status_write_is_conditional_on_the_attached_vrid(self):
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = [_twin()]
        gp.update_record_if = _update_record_if()
        doc = _parked("r1")

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock):
            await ep._check_duplicate_by_md5(b"x", doc)

        [(key, fields, kwargs)] = _conditional_writes(gp, "status")
        assert key == "r1"
        assert fields["indexingStatus"] == ProgressStatus.COMPLETED.value
        assert kwargs == {"match_virtual_record_id": True, "expected_virtual_record_id": "vr-1"}
        assert doc["indexingStatus"] == ProgressStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_sync_failure_leaves_status_unwritten_and_restores_identity(self):
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = [_twin()]
        gp.update_record_if = _update_record_if()
        doc = _parked("r1", virtualRecordId="vr-own", summaryDocumentId="sum-own")

        with patch.object(
            ep, "sync_vector_membership", new_callable=AsyncMock,
            side_effect=IndexingError("qdrant down"),
        ):
            with pytest.raises(IndexingError, match="qdrant down"):
                await ep._check_duplicate_by_md5(b"x", doc)

        assert _conditional_writes(gp, "status") == []
        [(key, fields, kwargs)] = _conditional_writes(gp, "rollback")
        assert key == "r1"
        assert fields == {"virtualRecordId": "vr-own", "summaryDocumentId": "sum-own"}
        assert kwargs["expected_statuses"] == [ProgressStatus.QUEUED.value]
        assert kwargs["expected_virtual_record_id"] == "vr-1"

    @pytest.mark.asyncio
    async def test_redelivery_after_a_failed_sync_attaches(self):
        """The record is left QUEUED, so its redelivered event gets past the
        COMPLETED guard and the attach runs again."""
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = [_twin()]
        gp.update_record_if = _update_record_if()
        sync = AsyncMock(side_effect=[IndexingError("qdrant down"), None])

        with patch.object(ep, "sync_vector_membership", sync):
            with pytest.raises(IndexingError):
                await ep._check_duplicate_by_md5(b"x", _parked("r1"))
            result = await ep._check_duplicate_by_md5(b"x", _parked("r1"))

        assert result.skip_indexing is True
        assert sync.await_count == 2
        assert len(_conditional_writes(gp, "status")) == 1

    @pytest.mark.asyncio
    async def test_a_missed_status_write_raises_and_restores_identity(self):
        """Another writer took the record off the twin's VRID mid-attach."""
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = [_twin()]
        gp.update_record_if = _update_record_if(status=False)

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock):
            with pytest.raises(IndexingError, match="changed while it was being attached"):
                await ep._check_duplicate_by_md5(b"x", _parked("r1"))

        assert len(_conditional_writes(gp, "rollback")) == 1

    @pytest.mark.asyncio
    async def test_cancellation_mid_attach_restores_identity(self):
        """Left holding the twin's VRID in its old status, the record would be
        skipped by the stranded sweep as parked and still count in the VRID's
        membership."""
        import asyncio

        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = [_twin()]
        gp.update_record_if = _update_record_if()

        with patch.object(
            ep, "sync_vector_membership", new_callable=AsyncMock,
            side_effect=asyncio.CancelledError(),
        ):
            with pytest.raises(asyncio.CancelledError):
                await ep._check_duplicate_by_md5(b"x", _parked("r1"))

        assert _conditional_writes(gp, "status") == []
        assert len(_conditional_writes(gp, "rollback")) == 1

    @pytest.mark.asyncio
    async def test_a_missed_status_write_after_the_sync_recomputes_membership(self):
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = [_twin()]
        gp.update_record_if = _update_record_if(status=False)

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock) as sync:
            with pytest.raises(IndexingError):
                await ep._check_duplicate_by_md5(b"x", _parked("r1"))

        assert sync.await_count == 2

    @pytest.mark.asyncio
    async def test_a_failed_rollback_does_not_replace_the_original_error(self):
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = [_twin()]
        gp.update_record_if = AsyncMock(side_effect=RuntimeError("graph down"))

        with patch.object(
            ep, "sync_vector_membership", new_callable=AsyncMock,
            side_effect=IndexingError("qdrant down"),
        ):
            with pytest.raises(IndexingError, match="qdrant down"):
                await ep._check_duplicate_by_md5(b"x", _parked("r1"))

    @pytest.mark.asyncio
    async def test_copies_the_twins_extraction_status(self):
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = [
            _twin(extractionStatus=ProgressStatus.FAILED.value)
        ]
        gp.update_record_if = _update_record_if()

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock):
            await ep._check_duplicate_by_md5(b"x", _parked("r1"))

        [(_, fields, _)] = _conditional_writes(gp, "status")
        assert fields["extractionStatus"] == ProgressStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_empty_twin_without_vrid_is_attached_without_a_sync(self):
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = [
            _twin(virtualRecordId=None, indexingStatus=ProgressStatus.EMPTY.value,
                  extractionStatus=None)
        ]
        gp.update_record_if = _update_record_if()

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock) as sync:
            result = await ep._check_duplicate_by_md5(b"x", _parked("r1"))

        assert result.skip_indexing is True
        sync.assert_not_awaited()
        [(_, fields, kwargs)] = _conditional_writes(gp, "status")
        assert fields["indexingStatus"] == ProgressStatus.EMPTY.value
        assert fields["extractionStatus"] == ProgressStatus.NOT_STARTED.value
        assert kwargs["expected_virtual_record_id"] is None


class TestParkedDuplicateRecheck:
    """A record parked behind an in-flight twin reads again after parking.

    The twin releases what it finds QUEUED only after writing COMPLETED. If it
    finished between this record's read and its QUEUED write, it found nothing,
    and a KB upload parked that way was never released or swept.
    """

    _IN_FLIGHT = {"_key": "twin", "indexingStatus": ProgressStatus.IN_PROGRESS.value}

    @pytest.mark.asyncio
    async def test_a_twin_that_finished_meanwhile_is_attached_inline(self):
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records = AsyncMock(side_effect=[[self._IN_FLIGHT], [_twin()]])
        gp.update_record_if = _update_record_if()
        doc = _parked("r1", indexingStatus=ProgressStatus.NOT_STARTED.value)

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock) as sync:
            result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        assert result.virtual_record_id == "vr-1"
        sync.assert_awaited_once_with("vr-1")
        assert doc["indexingStatus"] == ProgressStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_a_twin_still_in_flight_leaves_it_parked_after_one_recheck(self):
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records = AsyncMock(return_value=[self._IN_FLIGHT])
        doc = _parked("r1", indexingStatus=ProgressStatus.NOT_STARTED.value)

        result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        assert doc["indexingStatus"] == ProgressStatus.QUEUED.value
        assert gp.find_duplicate_records.await_count == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize("after", [
        [],
        [{"_key": "twin", "indexingStatus": ProgressStatus.FAILED.value}],
    ])
    async def test_a_twin_gone_or_failed_meanwhile_unparks_it(self, after):
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records = AsyncMock(side_effect=[[self._IN_FLIGHT], after])

        result = await ep._check_duplicate_by_md5(b"x", _parked("r1"))

        assert result.skip_indexing is False
        assert result.virtual_record_id is None

    @pytest.mark.asyncio
    async def test_no_recheck_when_the_queued_write_fails(self):
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records = AsyncMock(return_value=[self._IN_FLIGHT])
        gp.update_node = _fail_every_write_except_md5()

        with pytest.raises(IndexingError, match="QUEUED"):
            await ep._check_duplicate_by_md5(b"x", _parked("r1"))

        assert gp.find_duplicate_records.await_count == 1


class TestReleaseQueuedDuplicates:
    """Records parked behind a finished twin end up as if they had attached
    themselves: its VRID, its edges, its status, and their connectorId in the
    VRID's vector membership.

    The status-only promotion this replaces did none of the last three, so a
    copy parked in a second KB was missing from that VRID's connectorIds.
    Container-scoped search could not find it there, and deleting the first KB
    purged vectors the second one still needed.
    """

    def _ep(self, waiting, **graph):
        ep, _, _, gp = _make_event_processor()
        gp.find_queued_duplicates = AsyncMock(return_value=waiting)
        gp.copy_document_relationships = AsyncMock(return_value=True)
        gp.update_record_if = graph.pop("update_record_if", _update_record_if())
        gp.get_document = AsyncMock(return_value=_twin())
        for name, value in graph.items():
            setattr(gp, name, value)
        return ep, gp

    @staticmethod
    def _actions(outcomes):
        return {o.record["_key"]: o.action for o in outcomes}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("missing", ["md5Checksum", "orgId"])
    async def test_needs_the_primarys_md5_and_org(self, missing):
        ep, gp = self._ep([_parked("d1")])

        outcomes = await ep.release_queued_duplicates(_twin(**{missing: None}))

        assert outcomes == []
        gp.find_queued_duplicates.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_looks_up_the_duplicates_by_the_primarys_identity(self):
        ep, gp = self._ep([])

        await ep.release_queued_duplicates(_twin())

        gp.find_queued_duplicates.assert_awaited_once_with(
            record_key="twin", md5_checksum="abc", org_id="org-1",
            record_type="FILE", size_in_bytes=10, raise_on_error=True,
        )

    @pytest.mark.asyncio
    async def test_attaches_every_duplicate_with_one_membership_sync(self):
        ep, gp = self._ep([_parked("d1"), _parked("d2")])

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock) as sync:
            outcomes = await ep.release_queued_duplicates(_twin())

        assert self._actions(outcomes) == {"d1": ReleaseAction.ATTACHED, "d2": ReleaseAction.ATTACHED}
        sync.assert_awaited_once_with("vr-1")
        assert [c.args for c in gp.copy_document_relationships.await_args_list] == [
            ("twin", "d1"), ("twin", "d2"),
        ]

    @pytest.mark.asyncio
    async def test_claims_then_copies_edges_before_the_sync_and_status_after(self):
        """Identity first, conditional on QUEUED: a copy that moved on meanwhile
        must not be handed this twin's classification edges."""
        ep, gp = self._ep([_parked("d1"), _parked("d2")])
        order = []
        _recording_graph(gp, order)
        gp.get_document = AsyncMock(return_value=_twin())

        async def _sync(vrid):
            order.append(("sync", vrid))

        with patch.object(ep, "sync_vector_membership", side_effect=_sync):
            await ep.release_queued_duplicates(_twin())

        assert order == [
            ("identity", "d1"), ("edges", "d1"),
            ("identity", "d2"), ("edges", "d2"),
            ("sync", "vr-1"),
            ("status", "d1"), ("status", "d2"),
        ]

    @pytest.mark.asyncio
    async def test_both_writes_only_apply_while_the_duplicate_is_still_queued(self):
        ep, gp = self._ep([_parked("d1")])

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock):
            await ep.release_queued_duplicates(_twin())

        [(_, identity, identity_kw)] = _conditional_writes(gp, "identity")
        assert identity == {"virtualRecordId": "vr-1", "summaryDocumentId": "sum-1"}
        assert identity_kw == {"expected_statuses": [ProgressStatus.QUEUED.value]}
        [(_, status, status_kw)] = _conditional_writes(gp, "status")
        assert status["indexingStatus"] == ProgressStatus.COMPLETED.value
        assert status_kw == {
            "expected_statuses": [ProgressStatus.QUEUED.value],
            "match_virtual_record_id": True,
            "expected_virtual_record_id": "vr-1",
        }

    @pytest.mark.asyncio
    async def test_copies_the_primarys_extraction_status(self):
        ep, gp = self._ep([_parked("d1")])

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock):
            await ep.release_queued_duplicates(
                _twin(extractionStatus=ProgressStatus.FAILED.value)
            )

        [(_, status, _)] = _conditional_writes(gp, "status")
        assert status["extractionStatus"] == ProgressStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_a_duplicate_in_another_collection_is_woken_untouched(self):
        ep, gp = _make_multi_collection_event_processor()
        gp.find_queued_duplicates = AsyncMock(
            return_value=[_parked("d1", connectorName="SLACK")]
        )
        gp.update_record_if = _update_record_if()

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock) as sync:
            outcomes = await ep.release_queued_duplicates(_twin(connectorName="GOOGLE_DRIVE"))

        assert self._actions(outcomes) == {"d1": ReleaseAction.WAKE}
        gp.copy_document_relationships.assert_not_awaited()
        gp.update_record_if.assert_not_awaited()
        sync.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_completed_primary_without_a_vrid_wakes_them(self):
        ep, gp = self._ep([_parked("d1")])

        outcomes = await ep.release_queued_duplicates(_twin(virtualRecordId=None))

        assert self._actions(outcomes) == {"d1": ReleaseAction.WAKE}
        gp.update_record_if.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_empty_primary_without_a_vrid_attaches_them_without_a_sync(self):
        empty = _twin(virtualRecordId=None, indexingStatus=ProgressStatus.EMPTY.value,
                      extractionStatus=None)
        ep, gp = self._ep([_parked("d1")], get_document=AsyncMock(return_value=empty))

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock) as sync:
            outcomes = await ep.release_queued_duplicates(empty)

        assert self._actions(outcomes) == {"d1": ReleaseAction.ATTACHED}
        sync.assert_not_awaited()
        [(_, status, kwargs)] = _conditional_writes(gp, "status")
        assert status["indexingStatus"] == ProgressStatus.EMPTY.value
        assert kwargs["expected_virtual_record_id"] is None

    @pytest.mark.asyncio
    async def test_a_failed_edge_copy_gives_back_the_claim_and_wakes_it(self):
        ep, gp = self._ep(
            [_parked("d1"), _parked("d2")],
            copy_document_relationships=AsyncMock(side_effect=[False, True]),
        )

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock):
            outcomes = await ep.release_queued_duplicates(_twin())

        assert self._actions(outcomes) == {"d1": ReleaseAction.WAKE, "d2": ReleaseAction.ATTACHED}
        assert [w[0] for w in _conditional_writes(gp, "rollback")] == ["d1"]
        assert [w[0] for w in _conditional_writes(gp, "status")] == ["d2"]

    @pytest.mark.asyncio
    async def test_a_copy_that_left_queued_gets_no_edges(self):
        ep, gp = self._ep(
            [_parked("d1")], update_record_if=_update_record_if(identity=False)
        )

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock):
            await ep.release_queued_duplicates(_twin())

        gp.copy_document_relationships.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("current", [
        None,
        _twin(virtualRecordId="vr-reindexed"),
        _twin(indexingStatus=ProgressStatus.IN_PROGRESS.value),
    ], ids=["deleted", "new-vrid", "reindexing"])
    async def test_nothing_is_completed_onto_a_primary_that_moved_on(self, current):
        """Its VRID may have lost its points: completed onto it, the copies
        would claim to be indexed with nothing to search."""
        ep, gp = self._ep([_parked("d1"), _parked("d2")], get_document=AsyncMock(return_value=current))

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock) as sync:
            outcomes = await ep.release_queued_duplicates(_twin())

        assert self._actions(outcomes) == {"d1": ReleaseAction.WAKE, "d2": ReleaseAction.WAKE}
        sync.assert_not_awaited()
        assert _conditional_writes(gp, "status") == []
        assert len(_conditional_writes(gp, "rollback")) == 2

    @pytest.mark.asyncio
    async def test_a_rollback_after_the_sync_recomputes_membership(self):
        """The rolled-back copy's connectorId is already on the points."""
        ep, gp = self._ep(
            [_parked("d1")],
            update_record_if=_update_record_if(status=RuntimeError("graph down")),
        )

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock) as sync:
            await ep.release_queued_duplicates(_twin())

        assert sync.await_count == 2

    @pytest.mark.asyncio
    async def test_a_duplicate_that_left_queued_is_skipped(self):
        ep, gp = self._ep(
            [_parked("d1")], update_record_if=_update_record_if(identity=False)
        )

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock) as sync:
            outcomes = await ep.release_queued_duplicates(_twin())

        assert self._actions(outcomes) == {"d1": ReleaseAction.SKIPPED}
        sync.assert_not_awaited()
        assert _conditional_writes(gp, "status") == []

    @pytest.mark.asyncio
    async def test_a_failed_sync_restores_every_identity_and_wakes_them(self):
        """Left holding the VRID, a woken duplicate would re-embed over it."""
        ep, gp = self._ep([
            _parked("d1", virtualRecordId="vr-d1"), _parked("d2"),
        ])

        with patch.object(
            ep, "sync_vector_membership", new_callable=AsyncMock,
            side_effect=IndexingError("qdrant down"),
        ):
            outcomes = await ep.release_queued_duplicates(_twin())

        assert self._actions(outcomes) == {"d1": ReleaseAction.WAKE, "d2": ReleaseAction.WAKE}
        assert _conditional_writes(gp, "status") == []
        rollbacks = {key: (fields, kw) for key, fields, kw in _conditional_writes(gp, "rollback")}
        assert rollbacks["d1"][0] == {"virtualRecordId": "vr-d1", "summaryDocumentId": None}
        assert rollbacks["d2"][1] == {
            "expected_statuses": [ProgressStatus.QUEUED.value],
            "match_virtual_record_id": True,
            "expected_virtual_record_id": "vr-1",
        }

    @pytest.mark.asyncio
    async def test_a_failed_status_write_restores_identity_and_wakes_it(self):
        ep, gp = self._ep(
            [_parked("d1")],
            update_record_if=_update_record_if(status=RuntimeError("graph down")),
        )

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock):
            outcomes = await ep.release_queued_duplicates(_twin())

        assert self._actions(outcomes) == {"d1": ReleaseAction.WAKE}
        assert len(_conditional_writes(gp, "rollback")) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("current, action, rolled_back", [
        ({"indexingStatus": ProgressStatus.COMPLETED.value, "virtualRecordId": "vr-1"},
         ReleaseAction.ATTACHED, False),
        ({"indexingStatus": ProgressStatus.QUEUED.value, "virtualRecordId": "vr-other"},
         ReleaseAction.WAKE, True),
        ({"indexingStatus": ProgressStatus.IN_PROGRESS.value, "virtualRecordId": "vr-other"},
         ReleaseAction.SKIPPED, False),
    ])
    async def test_a_missed_status_write_is_judged_by_where_the_record_went(
        self, current, action, rolled_back
    ):
        async def _get_document(key, *_args, **_kwargs):
            return _twin() if key == "twin" else {"_key": "d1", **current}

        ep, gp = self._ep(
            [_parked("d1")],
            update_record_if=_update_record_if(status=False),
            get_document=AsyncMock(side_effect=_get_document),
        )

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock):
            outcomes = await ep.release_queued_duplicates(_twin())

        assert self._actions(outcomes) == {"d1": action}
        assert bool(_conditional_writes(gp, "rollback")) is rolled_back

    @pytest.mark.asyncio
    async def test_an_unreadable_copy_after_a_missed_status_write_is_woken(self):
        """Not knowing is not "moved on": it may still be QUEUED on this VRID,
        where the stranded sweep would skip it for good."""
        async def _get_document(key, *_args, **_kwargs):
            if key == "twin":
                return _twin()
            raise RuntimeError("graph down")

        ep, gp = self._ep(
            [_parked("d1")],
            update_record_if=_update_record_if(status=False),
            get_document=AsyncMock(side_effect=_get_document),
        )

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock):
            outcomes = await ep.release_queued_duplicates(_twin())

        assert self._actions(outcomes) == {"d1": ReleaseAction.WAKE}
        assert len(_conditional_writes(gp, "rollback")) == 1

    @pytest.mark.asyncio
    async def test_a_failed_lookup_propagates(self):
        """A lookup that failed must not read as "nothing waiting"."""
        ep, gp = self._ep([])
        gp.find_queued_duplicates = AsyncMock(side_effect=RuntimeError("graph down"))

        with pytest.raises(RuntimeError):
            await ep.release_queued_duplicates(_twin())

    @pytest.mark.asyncio
    async def test_many_duplicates_are_all_attached_inline_with_one_sync(self):
        """Waking them instead would re-download the content once per copy
        from its source, which for a connector is a rate-limited API call."""
        waiting = [_parked(f"d{i}") for i in range(150)]
        ep, gp = self._ep(waiting)

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock) as sync:
            outcomes = await ep.release_queued_duplicates(_twin())

        assert set(self._actions(outcomes).values()) == {ReleaseAction.ATTACHED}
        assert len(outcomes) == 150
        sync.assert_awaited_once_with("vr-1")


class TestBackfillEarlyAttachedCopies:
    """A copy that attached while its twin was still extracting copied no edges
    and a not-yet-final extractionStatus. Reproduced on a dev stack: the second
    upload landed 6s after the first read COMPLETED and 3s before its edges
    existed, and kept no edges for good."""

    _EARLY = {
        "_key": "early", "orgId": "org-1", "virtualRecordId": "vr-1",
        "indexingStatus": ProgressStatus.COMPLETED.value,
        "extractionStatus": ProgressStatus.NOT_STARTED.value,
    }

    def _ep(self, copies, keys=None):
        ep, _, _, gp = _make_event_processor()
        docs = {c["_key"]: c for c in copies}
        gp.get_records_by_virtual_record_id = AsyncMock(
            return_value=keys if keys is not None else ["twin", *docs]
        )
        gp.get_document = AsyncMock(side_effect=lambda key, *a, **k: docs.get(key))
        gp.copy_document_relationships = AsyncMock(return_value=True)
        gp.update_record_if = AsyncMock(return_value=True)
        return ep, gp

    @pytest.mark.asyncio
    async def test_an_early_copy_gets_the_edges_and_the_final_extraction_status(self):
        ep, gp = self._ep([dict(self._EARLY)])

        with patch("app.events.events.get_epoch_timestamp_in_ms", return_value=500):
            assert await ep.backfill_early_attached_copies(_twin()) == 1

        gp.copy_document_relationships.assert_awaited_once_with("twin", "early")
        gp.update_record_if.assert_awaited_once_with(
            "early",
            {"extractionStatus": ProgressStatus.COMPLETED.value, "lastExtractionTimestamp": 500},
            expected_statuses=[ProgressStatus.COMPLETED.value],
            match_virtual_record_id=True,
            expected_virtual_record_id="vr-1",
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("copy", [
        {"extractionStatus": ProgressStatus.COMPLETED.value},
        {"extractionStatus": ProgressStatus.FAILED.value},
        {"indexingStatus": ProgressStatus.QUEUED.value},
        {"indexingStatus": ProgressStatus.IN_PROGRESS.value},
    ], ids=["own-extraction", "own-failed-extraction", "still-parked", "mid-attach"])
    async def test_copies_that_are_not_early_attached_are_left_alone(self, copy):
        """Its own extraction is its own; a copy still being attached gets the
        edges from that attach."""
        ep, gp = self._ep([{**self._EARLY, **copy}])

        assert await ep.backfill_early_attached_copies(_twin()) == 0
        gp.copy_document_relationships.assert_not_awaited()
        gp.update_record_if.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("primary", [
        _twin(extractionStatus=ProgressStatus.NOT_STARTED.value),
        _twin(extractionStatus=ProgressStatus.IN_PROGRESS.value),
        _twin(indexingStatus=ProgressStatus.EMPTY.value, virtualRecordId=None),
        _twin(virtualRecordId=None),
    ], ids=["deferred-extraction", "still-extracting", "empty", "no-vrid"])
    async def test_nothing_happens_until_the_primary_has_finished_extracting(self, primary):
        ep, gp = self._ep([dict(self._EARLY)])

        assert await ep.backfill_early_attached_copies(primary) == 0
        gp.get_records_by_virtual_record_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_copy_in_another_collection_is_left_alone(self):
        ep, gp = _make_multi_collection_event_processor()
        early = {**self._EARLY, "connectorName": "SLACK"}
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=["twin", "early"])
        gp.get_document = AsyncMock(return_value=early)
        gp.update_record_if = AsyncMock(return_value=True)

        assert await ep.backfill_early_attached_copies(_twin(connectorName="GOOGLE_DRIVE")) == 0
        gp.update_record_if.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_one_failing_copy_does_not_stop_the_others(self):
        ep, gp = self._ep([dict(self._EARLY, _key="e1"), dict(self._EARLY, _key="e2")])
        gp.copy_document_relationships = AsyncMock(side_effect=[False, True])

        assert await ep.backfill_early_attached_copies(_twin()) == 1
        assert gp.update_record_if.await_args.args[0] == "e2"

    @pytest.mark.asyncio
    async def test_a_copy_that_moved_on_is_not_counted(self):
        ep, gp = self._ep([dict(self._EARLY)])
        gp.update_record_if = AsyncMock(return_value=False)

        assert await ep.backfill_early_attached_copies(_twin()) == 0
