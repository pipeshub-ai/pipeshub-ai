"""Tests for app.events.events (EventProcessor class).

This module tests the EventProcessor from the events.py module.
Since events.py and processor.py contain the same EventProcessor class,
these tests provide additional coverage for edge cases and boundary conditions.
"""

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    EventTypes,
    ExtensionTypes,
    MimeTypes,
    ProgressStatus,
)
from app.events.events import EventProcessor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event_processor():
    """Create an EventProcessor with mocked deps from events module."""
    logger = MagicMock()
    processor = MagicMock()
    graph_provider = AsyncMock()
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
    yield {"event": "parsing_complete", "data": {}}
    yield {"event": "indexing_complete", "data": {}}


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
        assert doc["extractionStatus"] == ProgressStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_failed_status(self):
        """FAILED status is applied correctly."""
        ep, _, _, gp = _make_event_processor()
        doc = {"_key": "k2"}

        await ep.mark_record_status(doc, ProgressStatus.FAILED)

        assert doc["indexingStatus"] == ProgressStatus.FAILED.value
        assert doc["extractionStatus"] == ProgressStatus.FAILED.value

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
    async def test_error_with_non_empty_status_does_not_raise(self):
        """Errors with non-EMPTY statuses are swallowed."""
        ep, logger, _, gp = _make_event_processor()
        gp.batch_upsert_nodes.side_effect = Exception("fail")
        doc = {"_key": "k6"}

        # FAILED is not EMPTY, so exception should be swallowed
        await ep.mark_record_status(doc, ProgressStatus.FAILED)
        logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_error_with_empty_status_raises(self):
        """Errors with EMPTY status are re-raised."""
        ep, _, _, gp = _make_event_processor()
        gp.batch_upsert_nodes.side_effect = Exception("fail")
        doc = {"_key": "k7"}

        with pytest.raises(Exception, match="Failed to mark record status to EMPTY"):
            await ep.mark_record_status(doc, ProgressStatus.EMPTY)


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
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_bytes_content_no_md5(self):
        """Empty bytes content with no md5Checksum => returns False."""
        ep, _, _, gp = _make_event_processor()
        doc = {"_key": "r2", "recordType": "FILE"}

        result = await ep._check_duplicate_by_md5(b"", doc)

        assert result is False

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
        assert result is False

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

        assert result is True
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

        assert result is True
        gp.copy_document_relationships.assert_awaited_once_with("dup-src", "r-fallback")


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

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            # No eventType key in data
            event_data = _make_event_payload(extension=ExtensionTypes.DOCX.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2

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

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
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

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
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

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
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

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
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

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
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

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(mime_type=MimeTypes.DOCX.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
        processor.process_docx_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_xlsx_mime_type_routes_to_excel_processor(self):
        """XLSX MIME type routes to process_excel_document."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_excel_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(mime_type=MimeTypes.XLSX.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
        processor.process_excel_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_csv_mime_type_routes_to_delimited(self):
        """CSV MIME type routes to process_delimited_document."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_delimited_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(mime_type=MimeTypes.CSV.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
        processor.process_delimited_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_pptx_mime_type_routes_to_pptx(self):
        """PPTX MIME type routes to process_pptx_document."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_pptx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(mime_type=MimeTypes.PPTX.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
        processor.process_pptx_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_markdown_mime_type_routes_to_md(self):
        """Markdown MIME type routes to process_md_document."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_md_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(mime_type=MimeTypes.MARKDOWN.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
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

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(extension=ExtensionTypes.DOCX.value)
            with pytest.raises(ValueError, match="processor broke"):
                await _drain(ep.on_event(event_data))

    @pytest.mark.asyncio
    async def test_pymupdf_env_flag_routes_to_pymupdf(self):
        """ENABLE_PYMUPDF_PROCESSOR=true routes to process_pdf_with_pymupdf."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_pdf_with_pymupdf = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            with patch("app.events.events.fitz") as mock_fitz:
                mock_page = MagicMock()
                mock_doc = MagicMock()
                mock_doc.__enter__ = MagicMock(return_value=mock_doc)
                mock_doc.__exit__ = MagicMock(return_value=False)
                mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
                mock_doc.__len__ = MagicMock(return_value=1)
                mock_fitz.open.return_value = mock_doc
                with patch("app.events.events.OCRStrategy.needs_ocr", return_value=False):
                    with patch.dict("os.environ", {"ENABLE_PYMUPDF_PROCESSOR": "true"}):
                        event_data = _make_event_payload(extension=ExtensionTypes.PDF.value)
                        events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
        processor.process_pdf_with_pymupdf.assert_called_once()

    @pytest.mark.asyncio
    async def test_pymupdf_failure_falls_back_to_ocr(self):
        """When pymupdf raises, falls back to OCR."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}

        async def pymupdf_fails(*args, **kwargs):
            raise RuntimeError("pymupdf error")
            yield  # noqa: unreachable

        processor.process_pdf_with_pymupdf = MagicMock(side_effect=pymupdf_fails)
        processor.process_pdf_document_with_ocr = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            with patch("app.events.events.fitz") as mock_fitz:
                mock_page = MagicMock()
                mock_doc = MagicMock()
                mock_doc.__enter__ = MagicMock(return_value=mock_doc)
                mock_doc.__exit__ = MagicMock(return_value=False)
                mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
                mock_doc.__len__ = MagicMock(return_value=1)
                mock_fitz.open.return_value = mock_doc
                with patch("app.events.events.OCRStrategy.needs_ocr", return_value=False):
                    with patch.dict("os.environ", {"ENABLE_PYMUPDF_PROCESSOR": "true"}):
                        event_data = _make_event_payload(extension=ExtensionTypes.PDF.value)
                        events = await _drain(ep.on_event(event_data))

        processor.process_pdf_document_with_ocr.assert_called_once()

    @pytest.mark.asyncio
    async def test_fitz_open_exception_defaults_to_layout_parser(self):
        """If fitz.open raises, needs_ocr defaults to False (layout parser)."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_pdf_with_docling = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            with patch("app.events.events.fitz") as mock_fitz:
                mock_fitz.open.side_effect = Exception("corrupted pdf")
                event_data = _make_event_payload(extension=ExtensionTypes.PDF.value)
                events = await _drain(ep.on_event(event_data))

        # Should fallback to docling (layout parser)
        processor.process_pdf_with_docling.assert_called_once()


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

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(mime_type=MimeTypes.GOOGLE_SLIDES.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
        processor.process_pptx_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_google_docs_routes_to_docx_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(mime_type=MimeTypes.GOOGLE_DOCS.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
        processor.process_docx_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_google_sheets_routes_to_excel_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_excel_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(mime_type=MimeTypes.GOOGLE_SHEETS.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
        processor.process_excel_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_html_mime_type_routes_to_html_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_html_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(mime_type=MimeTypes.HTML.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
        processor.process_html_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_plain_text_mime_routes_to_txt_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_txt_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(mime_type=MimeTypes.PLAIN_TEXT.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
        processor.process_txt_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_blocks_mime_type_routes_to_blocks_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_blocks = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(mime_type=MimeTypes.BLOCKS.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
        processor.process_blocks.assert_called_once()

    @pytest.mark.asyncio
    async def test_gmail_mime_type_routes_to_gmail_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_gmail_message = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(mime_type=MimeTypes.GMAIL.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
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

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(extension=ExtensionTypes.DOC.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
        processor.process_doc_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_xls_extension_routes_to_xls_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_xls_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(extension=ExtensionTypes.XLS.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
        processor.process_xls_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_tsv_extension_routes_to_delimited_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_delimited_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(extension=ExtensionTypes.TSV.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
        processor.process_delimited_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_ppt_extension_routes_to_ppt_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_ppt_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(extension=ExtensionTypes.PPT.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
        processor.process_ppt_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_md_extension_routes_to_md_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_md_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(extension=ExtensionTypes.MD.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
        processor.process_md_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_mdx_extension_routes_to_mdx_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_mdx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(extension=ExtensionTypes.MDX.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
        processor.process_mdx_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_txt_extension_routes_to_txt_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_txt_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(extension=ExtensionTypes.TXT.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
        processor.process_txt_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_png_extension_routes_to_image_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_image = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(extension=ExtensionTypes.PNG.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
        processor.process_image.assert_called_once()

    @pytest.mark.asyncio
    async def test_jpg_extension_routes_to_image_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_image = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(extension=ExtensionTypes.JPG.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
        processor.process_image.assert_called_once()

    @pytest.mark.asyncio
    async def test_unsupported_extension_raises(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(extension="xyz")
            with pytest.raises(Exception, match="Unsupported file extension"):
                await _drain(ep.on_event(event_data))

    @pytest.mark.asyncio
    async def test_html_extension_routes_to_html_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_html_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(extension=ExtensionTypes.HTML.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2
        processor.process_html_document.assert_called_once()


# ===========================================================================
# _check_duplicate_by_md5 - string content path
# ===========================================================================


class TestCheckDuplicateMd5StringContent:
    """Test md5 calculation with string content (lines 85-90)."""

    @pytest.mark.asyncio
    async def test_string_content_md5_calculated(self):
        """String content is encoded to bytes for MD5 calculation."""
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = []
        doc = {"_key": "r1", "recordType": "FILE"}

        result = await ep._check_duplicate_by_md5("hello world", doc)

        assert result is False
        assert doc.get("md5Checksum") is not None
        # Verify correct MD5 hash
        import hashlib
        expected = hashlib.md5(b"hello world").hexdigest()
        assert doc["md5Checksum"] == expected

    @pytest.mark.asyncio
    async def test_bytes_content_md5_calculated(self):
        """Bytes content has MD5 calculated directly."""
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = []
        doc = {"_key": "r1", "recordType": "FILE"}

        result = await ep._check_duplicate_by_md5(b"hello world", doc)

        assert result is False
        import hashlib
        expected = hashlib.md5(b"hello world").hexdigest()
        assert doc["md5Checksum"] == expected


# ===========================================================================
# on_event - update event creates new virtual_record_id
# ===========================================================================


class TestOnEventUpdateEvent:
    """Test UPDATE_RECORD event creates new virtual_record_id."""

    @pytest.mark.asyncio
    async def test_update_event_creates_new_virtual_record_id(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "FILE",
            "virtualRecordId": "old-vr-id",
        }
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            event_data = _make_event_payload(
                extension=ExtensionTypes.DOCX.value,
                event_type=EventTypes.UPDATE_RECORD.value,
                virtual_record_id="old-vr-id",
            )
            events = await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_docx_document.call_args[1]
        # Should have a new UUID, not the old one
        assert call_kwargs["virtual_record_id"] != "old-vr-id"
        assert len(call_kwargs["virtual_record_id"]) == 36  # UUID format


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
            yield {"event": "docling_failed"}

        processor.process_pdf_with_docling = MagicMock(side_effect=docling_fails)
        processor.process_pdf_document_with_ocr = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=False):
            with patch("app.events.events.fitz") as mock_fitz:
                mock_page = MagicMock()
                mock_doc = MagicMock()
                mock_doc.__enter__ = MagicMock(return_value=mock_doc)
                mock_doc.__exit__ = MagicMock(return_value=False)
                mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
                mock_doc.__len__ = MagicMock(return_value=1)
                mock_fitz.open.return_value = mock_doc
                with patch("app.events.events.OCRStrategy.needs_ocr", return_value=False):
                    with patch.dict("os.environ", {"ENABLE_PYMUPDF_PROCESSOR": "false"}):
                        event_data = _make_event_payload(extension=ExtensionTypes.PDF.value)
                        events = await _drain(ep.on_event(event_data))

        processor.process_pdf_document_with_ocr.assert_called_once()
