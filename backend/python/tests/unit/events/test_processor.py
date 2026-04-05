"""Tests for app.events.processor (Processor class and convert_record_dict_to_record).

Note: The EventProcessor class (mark_record_status, _check_duplicate_by_md5, on_event)
is defined in app.events.events and tested in test_events.py.
This file tests the Processor class and helper functions from app.events.processor.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
)
from app.events.processor import Processor, convert_record_dict_to_record
from app.models.entities import RecordType
from app.services.messaging.config import IndexingEvent, PipelineEvent, PipelineEventData
import logging


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_processor():
    """Create a Processor with mocked dependencies."""
    logger = MagicMock()
    config_service = MagicMock()
    indexing_pipeline = MagicMock()
    graph_provider = AsyncMock()
    parsers = {}
    document_extractor = MagicMock()
    sink_orchestrator = MagicMock()

    with patch("app.events.processor.DoclingClient"):
        proc = Processor(
            logger=logger,
            config_service=config_service,
            indexing_pipeline=indexing_pipeline,
            graph_provider=graph_provider,
            parsers=parsers,
            document_extractor=document_extractor,
            sink_orchestrator=sink_orchestrator,
        )
    return proc, logger, graph_provider, config_service


async def _collect(async_gen):
    """Drain an async generator into a list."""
    results = []
    async for item in async_gen:
        results.append(item)
    return results


def _base_record_dict(**overrides):
    """Build a minimal record dict for convert_record_dict_to_record."""
    base = {
        "_key": "rec-1",
        "orgId": "org-1",
        "recordName": "Test Record",
        "recordType": "FILE",
        "indexingStatus": "NOT_STARTED",
        "version": 1,
        "origin": "UPLOAD",
        "connectorName": "KB",
        "connectorId": "conn-1",
        "externalRecordId": "ext-rec-1",
        "mimeType": "application/octet-stream",
        "createdAtTimestamp": 1000,
        "updatedAtTimestamp": 2000,
    }
    base.update(overrides)
    return base


# ===========================================================================
# convert_record_dict_to_record
# ===========================================================================


class TestConvertRecordDictToRecord:
    """Tests for convert_record_dict_to_record helper function."""

    def test_basic_conversion(self):
        """Converts a basic record dict to a Record object."""
        d = _base_record_dict()
        record = convert_record_dict_to_record(d)

        assert record.id == "rec-1"
        assert record.org_id == "org-1"
        assert record.record_name == "Test Record"
        assert record.record_type == RecordType.FILE
        assert record.record_status == ProgressStatus.NOT_STARTED
        assert record.version == 1

    def test_connector_name_maps_to_enum(self):
        """Known connectorName values map to Connectors enum."""
        d = _base_record_dict(connectorName="DRIVE")
        record = convert_record_dict_to_record(d)
        assert record.connector_name == Connectors.GOOGLE_DRIVE

    def test_none_connector_name_defaults_to_kb(self):
        """None connectorName defaults to KNOWLEDGE_BASE."""
        d = _base_record_dict(connectorName=None)
        record = convert_record_dict_to_record(d)
        assert record.connector_name == Connectors.KNOWLEDGE_BASE

    def test_unknown_connector_name_defaults_to_kb(self):
        """Unknown connectorName string defaults to KNOWLEDGE_BASE."""
        d = _base_record_dict(connectorName="NONEXISTENT")
        record = convert_record_dict_to_record(d)
        assert record.connector_name == Connectors.KNOWLEDGE_BASE

    def test_origin_connector(self):
        """CONNECTOR origin maps correctly."""
        d = _base_record_dict(origin="CONNECTOR")
        record = convert_record_dict_to_record(d)
        assert record.origin == OriginTypes.CONNECTOR

    def test_origin_upload(self):
        """UPLOAD origin maps correctly."""
        d = _base_record_dict(origin="UPLOAD")
        record = convert_record_dict_to_record(d)
        assert record.origin == OriginTypes.UPLOAD

    def test_unknown_origin_defaults_to_upload(self):
        """Unknown origin string defaults to UPLOAD."""
        d = _base_record_dict(origin="UNKNOWN_ORIGIN")
        record = convert_record_dict_to_record(d)
        assert record.origin == OriginTypes.UPLOAD

    def test_missing_origin_defaults_to_upload(self):
        """Missing origin key defaults to UPLOAD."""
        d = _base_record_dict()
        del d["origin"]
        record = convert_record_dict_to_record(d)
        assert record.origin == OriginTypes.UPLOAD

    def test_id_falls_back_to_id_field(self):
        """When _key is missing, falls back to 'id' field."""
        d = _base_record_dict()
        del d["_key"]
        d["id"] = "rec-via-id"
        record = convert_record_dict_to_record(d)
        assert record.id == "rec-via-id"

    def test_record_type_file(self):
        d = _base_record_dict(recordType="FILE")
        record = convert_record_dict_to_record(d)
        assert record.record_type == RecordType.FILE

    def test_record_type_mail(self):
        d = _base_record_dict(recordType="MAIL")
        record = convert_record_dict_to_record(d)
        assert record.record_type == RecordType.MAIL

    def test_record_type_webpage(self):
        d = _base_record_dict(recordType="WEBPAGE")
        record = convert_record_dict_to_record(d)
        assert record.record_type == RecordType.WEBPAGE

    def test_default_record_type_is_file(self):
        """Missing recordType defaults to FILE."""
        d = _base_record_dict()
        del d["recordType"]
        record = convert_record_dict_to_record(d)
        assert record.record_type == RecordType.FILE

    def test_default_indexing_status(self):
        """Missing indexingStatus defaults to NOT_STARTED."""
        d = _base_record_dict()
        del d["indexingStatus"]
        record = convert_record_dict_to_record(d)
        assert record.record_status == ProgressStatus.NOT_STARTED

    def test_completed_indexing_status(self):
        d = _base_record_dict(indexingStatus="COMPLETED")
        record = convert_record_dict_to_record(d)
        assert record.record_status == ProgressStatus.COMPLETED

    def test_default_version(self):
        """Missing version defaults to 1."""
        d = _base_record_dict()
        del d["version"]
        record = convert_record_dict_to_record(d)
        assert record.version == 1

    def test_mime_type_preserved(self):
        """mimeType is stored on the record."""
        d = _base_record_dict(mimeType="application/pdf")
        record = convert_record_dict_to_record(d)
        assert record.mime_type == "application/pdf"

    def test_mime_type_default(self):
        """Default mimeType from base dict is application/octet-stream."""
        d = _base_record_dict()
        record = convert_record_dict_to_record(d)
        assert record.mime_type == "application/octet-stream"

    def test_optional_fields_preserved(self):
        """Optional fields like webUrl, summaryDocumentId are mapped."""
        d = _base_record_dict(
            webUrl="https://example.com",
            summaryDocumentId="sum-1",
            externalRecordId="ext-1",
            externalRevisionId="rev-1",
        )
        record = convert_record_dict_to_record(d)
        assert record.weburl == "https://example.com"
        assert record.summary_document_id == "sum-1"
        assert record.external_record_id == "ext-1"
        assert record.external_revision_id == "rev-1"

    def test_timestamps_mapped(self):
        """Timestamps are mapped to record fields."""
        d = _base_record_dict(
            createdAtTimestamp=100,
            updatedAtTimestamp=200,
            sourceCreatedAtTimestamp=300,
            sourceLastModifiedTimestamp=400,
        )
        record = convert_record_dict_to_record(d)
        assert record.created_at == 100
        assert record.updated_at == 200
        assert record.source_created_at == 300
        assert record.source_updated_at == 400

    def test_is_vlm_ocr_processed_default(self):
        """isVLMOcrProcessed defaults to False."""
        d = _base_record_dict()
        record = convert_record_dict_to_record(d)
        assert record.is_vlm_ocr_processed is False

    def test_is_vlm_ocr_processed_true(self):
        d = _base_record_dict(isVLMOcrProcessed=True)
        record = convert_record_dict_to_record(d)
        assert record.is_vlm_ocr_processed is True

    def test_connector_id_mapped(self):
        d = _base_record_dict(connectorId="conn-42")
        record = convert_record_dict_to_record(d)
        assert record.connector_id == "conn-42"


# ===========================================================================
# Processor.__init__
# ===========================================================================


class TestProcessorInit:
    """Tests for Processor constructor."""

    def test_stores_all_dependencies(self):
        """All injected dependencies are stored."""
        proc, logger, gp, config_service = _make_processor()

        assert proc.logger is logger
        assert proc.graph_provider is gp
        assert proc.config_service is config_service
        logger.info.assert_called()  # "Initializing Processor"

    def test_docling_client_initialized(self):
        """DoclingClient is instantiated during init."""
        with patch("app.events.processor.DoclingClient") as mock_docling:
            logger = MagicMock()
            Processor(
                logger=logger,
                config_service=MagicMock(),
                indexing_pipeline=MagicMock(),
                graph_provider=AsyncMock(),
                parsers={},
                document_extractor=MagicMock(),
                sink_orchestrator=MagicMock(),
            )
            mock_docling.assert_called_once()


# ===========================================================================
# Processor.process_image
# ===========================================================================


class TestProcessImage:
    """Tests for Processor.process_image."""

    @pytest.mark.asyncio
    async def test_no_content_raises(self):
        """No content raises an exception."""
        proc, _, _, _ = _make_processor()

        with pytest.raises(Exception, match="No image data"):
            await _collect(proc.process_image("rec-1", None, "vr-1"))

    @pytest.mark.asyncio
    async def test_no_content_empty_bytes_raises(self):
        """Empty bytes raises an exception."""
        proc, _, _, _ = _make_processor()

        with pytest.raises(Exception, match="No image data"):
            await _collect(proc.process_image("rec-1", b"", "vr-1"))

    @pytest.mark.asyncio
    async def test_record_not_found_yields_both_events(self):
        """If record not found in DB, both phase events are still yielded."""
        proc, _, gp, _ = _make_processor()
        gp.get_document.return_value = None

        events = await _collect(proc.process_image("rec-1", b"image-data", "vr-1"))

        assert len(events) == 2
        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_non_multimodal_llm_sets_enable_multimodal_status(self):
        """Non-multimodal LLM/embedding sets ENABLE_MULTIMODAL_MODELS status."""
        proc, _, gp, config_service = _make_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "mimeType": "image/png",
            "recordType": "FILE",
        }

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            with patch("app.events.processor.get_embedding_model_config", new_callable=AsyncMock) as mock_emb:
                mock_emb.return_value = {"isMultimodal": False}

                events = await _collect(proc.process_image("rec-1", b"img", "vr-1"))

        assert len(events) == 2
        # Record should have been updated with ENABLE_MULTIMODAL_MODELS status
        upserted_doc = gp.batch_upsert_nodes.call_args[0][0][0]
        assert upserted_doc["indexingStatus"] == ProgressStatus.ENABLE_MULTIMODAL_MODELS.value


# ===========================================================================
# Processor.process_gmail_message
# ===========================================================================


class TestProcessGmailMessage:
    """Tests for Processor.process_gmail_message."""

    @pytest.mark.asyncio
    async def test_delegates_to_html_processor(self):
        """Gmail processing delegates to process_html_document."""
        proc, _, _, _ = _make_processor()

        async def mock_html(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="rec-1"))

        with patch.object(proc, "process_html_document", side_effect=mock_html):
            events = await _collect(
                proc.process_gmail_message(
                    recordName="email",
                    recordId="rec-1",
                    version=1,
                    source="gmail",
                    orgId="org-1",
                    html_content=b"<p>Hello</p>",
                    virtual_record_id="vr-1",
                )
            )

        assert len(events) == 2
        assert events[0].event == "parsing_complete"


# ===========================================================================
# Processor.process_pdf_with_docling
# ===========================================================================


class TestProcessPdfWithDocling:
    """Tests for Processor.process_pdf_with_docling."""

    @pytest.mark.asyncio
    async def test_docling_failure_yields_docling_failed(self):
        """When Docling returns None, yields docling_failed event."""
        proc, _, gp, _ = _make_processor()
        proc.docling_client = AsyncMock()
        proc.docling_client.parse_pdf.return_value = None

        events = await _collect(
            proc.process_pdf_with_docling(
                recordName="test.pdf",
                recordId="rec-1",
                pdf_binary=b"pdf-content",
                virtual_record_id="vr-1",
            )
        )

        assert len(events) == 1
        assert events[0].event == "docling_failed"

    @pytest.mark.asyncio
    async def test_docling_success_yields_parsing_complete(self):
        """When Docling succeeds, yields parsing_complete first."""
        proc, _, gp, _ = _make_processor()
        proc.docling_client = AsyncMock()
        proc.docling_client.parse_pdf.return_value = {"pages": []}
        gp.get_document.return_value = _base_record_dict(mimeType="application/pdf")

        with patch("app.events.processor.DoclingProcessor") as mock_dp:
            mock_dp_instance = MagicMock()
            mock_dp_instance.process_pdf.return_value = []
            mock_dp.return_value = mock_dp_instance

            with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
                mock_pipeline_instance = AsyncMock()
                mock_pipeline.return_value = mock_pipeline_instance

                events = await _collect(
                    proc.process_pdf_with_docling(
                        recordName="test.pdf",
                        recordId="rec-1",
                        pdf_binary=b"pdf-content",
                        virtual_record_id="vr-1",
                    )
                )

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"


# ===========================================================================
# Processor.process_pdf_with_pymupdf
# ===========================================================================


class TestProcessPdfWithPyMuPDF:
    """Tests for Processor.process_pdf_with_pymupdf."""

    @pytest.mark.asyncio
    async def test_record_not_found_yields_indexing_complete(self):
        """When record not found after parsing, yields indexing_complete."""
        proc, _, gp, _ = _make_processor()
        gp.get_document.return_value = None

        with patch("app.events.processor.PyMuPDFOpenCVProcessor") as mock_pymupdf:
            mock_instance = AsyncMock()
            mock_instance.parse_document.return_value = {"pages": []}
            mock_instance.create_blocks.return_value = []
            mock_pymupdf.return_value = mock_instance

            events = await _collect(
                proc.process_pdf_with_pymupdf(
                    recordName="test.pdf",
                    recordId="rec-1",
                    pdf_binary=b"pdf",
                    virtual_record_id="vr-1",
                )
            )

        # Should have parsing_complete, then indexing_complete
        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_appends_pdf_extension(self):
        """Appends .pdf to record name if not present."""
        proc, _, gp, _ = _make_processor()
        gp.get_document.return_value = _base_record_dict(mimeType="application/pdf")

        with patch("app.events.processor.PyMuPDFOpenCVProcessor") as mock_pymupdf:
            mock_instance = AsyncMock()
            mock_instance.parse_document.return_value = {"pages": []}
            mock_instance.create_blocks.return_value = []
            mock_pymupdf.return_value = mock_instance

            with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
                mock_pipeline.return_value = AsyncMock()

                await _collect(
                    proc.process_pdf_with_pymupdf(
                        recordName="test",  # no .pdf extension
                        recordId="rec-1",
                        pdf_binary=b"pdf",
                        virtual_record_id="vr-1",
                    )
                )

        # parse_document should receive "test.pdf"
        call_args = mock_instance.parse_document.call_args[0]
        assert call_args[0] == "test.pdf"


# ===========================================================================
# Processor.process_docx_document
# ===========================================================================


class TestProcessDocxDocument:
    """Tests for Processor.process_docx_document."""

    @pytest.mark.asyncio
    async def test_record_not_found_yields_indexing_complete(self):
        """When record not found after parsing, yields indexing_complete."""
        proc, _, gp, _ = _make_processor()
        gp.get_document.return_value = None

        with patch("app.events.processor.DoclingProcessor") as mock_dp:
            mock_instance = AsyncMock()
            mock_instance.parse_document.return_value = MagicMock()
            mock_instance.create_blocks.return_value = MagicMock()
            mock_dp.return_value = mock_instance

            events = await _collect(
                proc.process_docx_document(
                    recordName="test.docx",
                    recordId="rec-1",
                    version=1,
                    source="upload",
                    orgId="org-1",
                    docx_binary=b"docx",
                    virtual_record_id="vr-1",
                )
            )

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_success_yields_both_events(self):
        """Successful processing yields parsing_complete then indexing_complete."""
        proc, _, gp, _ = _make_processor()
        gp.get_document.return_value = _base_record_dict(mimeType="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        with patch("app.events.processor.DoclingProcessor") as mock_dp:
            mock_instance = AsyncMock()
            mock_instance.parse_document.return_value = MagicMock()
            mock_instance.create_blocks.return_value = MagicMock()
            mock_dp.return_value = mock_instance

            with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
                mock_pipeline.return_value = AsyncMock()

                events = await _collect(
                    proc.process_docx_document(
                        recordName="test.docx",
                        recordId="rec-1",
                        version=1,
                        source="upload",
                        orgId="org-1",
                        docx_binary=b"docx",
                        virtual_record_id="vr-1",
                    )
                )

        assert len(events) == 2
        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_exception_propagates(self):
        """Exceptions during processing propagate."""
        proc, _, gp, _ = _make_processor()

        with patch("app.events.processor.DoclingProcessor") as mock_dp:
            mock_dp.return_value.parse_document = AsyncMock(
                side_effect=RuntimeError("parse error")
            )

            with pytest.raises(RuntimeError, match="parse error"):
                await _collect(
                    proc.process_docx_document(
                        recordName="test.docx",
                        recordId="rec-1",
                        version=1,
                        source="upload",
                        orgId="org-1",
                        docx_binary=b"docx",
                        virtual_record_id="vr-1",
                    )
                )


# ===========================================================================
# Processor.process_doc_document
# ===========================================================================


class TestProcessDocDocument:
    """Tests for Processor.process_doc_document."""

    @pytest.mark.asyncio
    async def test_delegates_to_docx_processor(self):
        """DOC processing converts to DOCX then delegates."""
        proc, _, gp, _ = _make_processor()

        mock_parser = MagicMock()
        mock_parser.convert_doc_to_docx.return_value = b"docx-content"
        proc.parsers["doc"] = mock_parser

        async def mock_docx(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="rec-1"))

        with patch.object(proc, "process_docx_document", side_effect=mock_docx):
            events = await _collect(
                proc.process_doc_document(
                    recordName="test.doc",
                    recordId="rec-1",
                    version=1,
                    source="upload",
                    orgId="org-1",
                    doc_binary=b"doc",
                    virtual_record_id="vr-1",
                )
            )

        assert len(events) == 2
        mock_parser.convert_doc_to_docx.assert_called_once()


# ===========================================================================
# Processor.process_blocks
# ===========================================================================


class TestProcessBlocks:
    """Tests for Processor.process_blocks."""

    @pytest.mark.asyncio
    async def test_bytes_input_decoded(self):
        """Bytes input is decoded to string then parsed as JSON."""
        proc, _, gp, _ = _make_processor()
        gp.get_document.return_value = _base_record_dict()

        import json
        blocks_data = json.dumps({"blocks": [], "block_groups": []}).encode("utf-8")

        with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
            mock_pipeline.return_value = AsyncMock()
            with patch.object(proc, "_process_blockgroups_through_docling", new_callable=AsyncMock) as mock_bg:
                from app.models.blocks import BlocksContainer
                mock_bg.return_value = BlocksContainer(blocks=[], block_groups=[])
                with patch.object(proc, "_enhance_tables_with_llm", new_callable=AsyncMock):
                    events = await _collect(
                        proc.process_blocks(
                            recordName="test",
                            recordId="rec-1",
                            version=1,
                            source="upload",
                            orgId="org-1",
                            blocks_data=blocks_data,
                            virtual_record_id="vr-1",
                        )
                    )

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_dict_input_handled(self):
        """Dict input is used directly."""
        proc, _, gp, _ = _make_processor()
        gp.get_document.return_value = _base_record_dict()

        blocks_data = {"blocks": [], "block_groups": []}

        with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
            mock_pipeline.return_value = AsyncMock()
            with patch.object(proc, "_process_blockgroups_through_docling", new_callable=AsyncMock) as mock_bg:
                from app.models.blocks import BlocksContainer
                mock_bg.return_value = BlocksContainer(blocks=[], block_groups=[])
                with patch.object(proc, "_enhance_tables_with_llm", new_callable=AsyncMock):
                    events = await _collect(
                        proc.process_blocks(
                            recordName="test",
                            recordId="rec-1",
                            version=1,
                            source="upload",
                            orgId="org-1",
                            blocks_data=blocks_data,
                            virtual_record_id="vr-1",
                        )
                    )

        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_invalid_type_raises(self):
        """Invalid blocks_data type raises ValueError."""
        proc, _, gp, _ = _make_processor()

        with pytest.raises(Exception):
            await _collect(
                proc.process_blocks(
                    recordName="test",
                    recordId="rec-1",
                    version=1,
                    source="upload",
                    orgId="org-1",
                    blocks_data=12345,
                    virtual_record_id="vr-1",
                )
            )

    @pytest.mark.asyncio
    async def test_record_not_found_yields_indexing_complete(self):
        """When record not found, yields indexing_complete."""
        proc, _, gp, _ = _make_processor()
        gp.get_document.return_value = None

        blocks_data = {"blocks": [], "block_groups": []}

        with patch.object(proc, "_process_blockgroups_through_docling", new_callable=AsyncMock) as mock_bg:
            from app.models.blocks import BlocksContainer
            mock_bg.return_value = BlocksContainer(blocks=[], block_groups=[])
            with patch.object(proc, "_enhance_tables_with_llm", new_callable=AsyncMock):
                events = await _collect(
                    proc.process_blocks(
                        recordName="test",
                        recordId="rec-1",
                        version=1,
                        source="upload",
                        orgId="org-1",
                        blocks_data=blocks_data,
                        virtual_record_id="vr-1",
                    )
                )

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"


# ===========================================================================
# Processor._mark_record
# ===========================================================================


class TestMarkRecord:
    """Tests for Processor._mark_record."""

    @pytest.mark.asyncio
    async def test_record_not_found_raises(self):
        """Raises DocumentProcessingError when record not found."""
        from app.exceptions.indexing_exceptions import DocumentProcessingError
        proc, _, gp, _ = _make_processor()
        gp.get_document.return_value = None

        with pytest.raises(DocumentProcessingError, match="Record not found"):
            await proc._mark_record("rec-1", ProgressStatus.EMPTY)

    @pytest.mark.asyncio
    async def test_upsert_failure_raises(self):
        """Raises DocumentProcessingError when upsert fails."""
        from app.exceptions.indexing_exceptions import DocumentProcessingError
        proc, _, gp, _ = _make_processor()
        gp.get_document.return_value = {"_key": "rec-1"}
        gp.batch_upsert_nodes.return_value = False

        with pytest.raises(DocumentProcessingError, match="Failed to update"):
            await proc._mark_record("rec-1", ProgressStatus.EMPTY)

    @pytest.mark.asyncio
    async def test_successful_mark(self):
        """Successfully marks record status."""
        proc, _, gp, _ = _make_processor()
        gp.get_document.return_value = {"_key": "rec-1"}
        gp.batch_upsert_nodes.return_value = True

        await proc._mark_record("rec-1", ProgressStatus.EMPTY)

        gp.batch_upsert_nodes.assert_awaited_once()


# ===========================================================================
# Processor.process_excel_document
# ===========================================================================


class TestProcessExcelDocument:
    """Tests for Processor.process_excel_document."""

    @pytest.mark.asyncio
    async def test_empty_binary_marks_empty(self):
        """Empty binary marks record as EMPTY."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = {"_key": "rec-1"}
        gp.batch_upsert_nodes.return_value = True

        mock_parser = MagicMock()
        proc.parsers["xlsx"] = mock_parser

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {})

            events = await _collect(
                proc.process_excel_document(
                    recordName="test.xlsx",
                    recordId="rec-1",
                    version=1,
                    source="upload",
                    orgId="org-1",
                    excel_binary=b"",
                    virtual_record_id="vr-1",
                )
            )

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_record_not_found_yields_indexing_complete(self):
        """When record not found after parsing, yields indexing_complete."""
        proc, _, gp, _ = _make_processor()
        gp.get_document.return_value = None

        mock_parser = MagicMock()
        mock_parser.load_workbook_from_binary = MagicMock()
        mock_parser.create_blocks = AsyncMock(return_value=MagicMock())
        proc.parsers["xlsx"] = mock_parser

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {})

            events = await _collect(
                proc.process_excel_document(
                    recordName="test.xlsx",
                    recordId="rec-1",
                    version=1,
                    source="upload",
                    orgId="org-1",
                    excel_binary=b"some-data",
                    virtual_record_id="vr-1",
                )
            )

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"


# ===========================================================================
# Processor.process_xls_document
# ===========================================================================


class TestProcessXlsDocument:
    """Tests for Processor.process_xls_document."""

    @pytest.mark.asyncio
    async def test_delegates_to_excel_processor(self):
        """XLS converts to XLSX then delegates."""
        proc, _, gp, _ = _make_processor()

        mock_parser = MagicMock()
        mock_parser.convert_xls_to_xlsx.return_value = b"xlsx-content"
        proc.parsers["xls"] = mock_parser

        async def mock_excel(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="rec-1"))

        with patch.object(proc, "process_excel_document", side_effect=mock_excel):
            events = await _collect(
                proc.process_xls_document(
                    recordName="test.xls",
                    recordId="rec-1",
                    version=1,
                    source="upload",
                    orgId="org-1",
                    xls_binary=b"xls",
                    virtual_record_id="vr-1",
                )
            )

        assert len(events) == 2


# ===========================================================================
# Processor.process_html_document
# ===========================================================================


class TestProcessHtmlDocument:
    """Tests for Processor.process_html_document."""

    @pytest.mark.asyncio
    async def test_delegates_to_md_processor(self):
        """HTML is cleaned and converted to markdown, then delegated."""
        proc, _, gp, _ = _make_processor()

        mock_html_parser = MagicMock()
        mock_html_parser.replace_relative_image_urls = MagicMock(return_value="<p>Test</p>")
        proc.parsers["html"] = mock_html_parser

        async def mock_md(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="rec-1"))

        with patch.object(proc, "process_md_document", side_effect=mock_md):
            events = await _collect(
                proc.process_html_document(
                    recordName="test.html",
                    recordId="rec-1",
                    version=1,
                    source="upload",
                    orgId="org-1",
                    html_binary=b"<p>Test</p>",
                    virtual_record_id="vr-1",
                )
            )

        assert len(events) == 2


# ===========================================================================
# Processor.process_md_document
# ===========================================================================


class TestProcessMdDocument:
    """Tests for Processor.process_md_document."""

    @pytest.mark.asyncio
    async def test_empty_markdown_marks_empty(self):
        """Empty markdown content marks record as EMPTY."""
        proc, _, gp, _ = _make_processor()
        gp.get_document.return_value = {"_key": "rec-1"}
        gp.batch_upsert_nodes.return_value = True

        mock_parser = MagicMock()
        proc.parsers["md"] = mock_parser

        events = await _collect(
            proc.process_md_document(
                recordName="test.md",
                recordId="rec-1",
                md_binary=b"   ",
                virtual_record_id="vr-1",
            )
        )

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_bytes_input_decoded(self):
        """Bytes input is decoded to string."""
        proc, _, gp, _ = _make_processor()
        gp.get_document.return_value = _base_record_dict()

        mock_parser = MagicMock()
        mock_parser.extract_and_replace_images.return_value = ("# Hello", [])
        mock_parser.parse_string.return_value = b"# Hello"
        proc.parsers["md"] = mock_parser

        with patch("app.events.processor.DoclingProcessor") as mock_dp:
            mock_instance = AsyncMock()
            mock_instance.parse_document.return_value = MagicMock()
            from app.models.blocks import BlocksContainer
            mock_instance.create_blocks.return_value = BlocksContainer(blocks=[], block_groups=[])
            mock_dp.return_value = mock_instance

            with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
                mock_pipeline.return_value = AsyncMock()

                events = await _collect(
                    proc.process_md_document(
                        recordName="test.md",
                        recordId="rec-1",
                        md_binary=b"# Hello World",
                        virtual_record_id="vr-1",
                    )
                )

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"


# ===========================================================================
# Processor.process_mdx_document
# ===========================================================================


class TestProcessMdxDocument:
    """Tests for Processor.process_mdx_document."""

    @pytest.mark.asyncio
    async def test_converts_mdx_to_md_and_delegates(self):
        """MDX is converted to MD then delegated."""
        proc, _, gp, _ = _make_processor()

        mock_parser = MagicMock()
        mock_parser.convert_mdx_to_md.return_value = "# Converted"
        proc.parsers["mdx"] = mock_parser

        async def mock_md(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="rec-1"))

        with patch.object(proc, "process_md_document", side_effect=mock_md):
            events = await _collect(
                proc.process_mdx_document(
                    recordName="test.mdx",
                    recordId="rec-1",
                    version=1,
                    source="upload",
                    orgId="org-1",
                    mdx_content="# Test MDX",
                    virtual_record_id="vr-1",
                )
            )

        assert len(events) == 2


# ===========================================================================
# Processor._separate_block_groups_by_index
# ===========================================================================


class TestSeparateBlockGroupsByIndex:
    """Tests for Processor._separate_block_groups_by_index."""

    def test_separates_by_index(self):
        proc, _, _, _ = _make_processor()

        bg_with = MagicMock()
        bg_with.index = 0
        bg_without = MagicMock()
        bg_without.index = None

        with_idx, without_idx = proc._separate_block_groups_by_index([bg_with, bg_without])

        assert len(with_idx) == 1
        assert len(without_idx) == 1

    def test_all_with_index(self):
        proc, _, _, _ = _make_processor()

        bg1 = MagicMock()
        bg1.index = 0
        bg2 = MagicMock()
        bg2.index = 1

        with_idx, without_idx = proc._separate_block_groups_by_index([bg1, bg2])

        assert len(with_idx) == 2
        assert len(without_idx) == 0

    def test_empty_list(self):
        proc, _, _, _ = _make_processor()

        with_idx, without_idx = proc._separate_block_groups_by_index([])

        assert with_idx == []
        assert without_idx == []


# ===========================================================================
# Processor.process_txt_document
# ===========================================================================


class TestProcessTxtDocument:
    """Tests for Processor.process_txt_document."""

    @pytest.mark.asyncio
    async def test_delegates_to_md_processor(self):
        """TXT is decoded and delegated to md processor."""
        proc, _, _, _ = _make_processor()

        async def mock_md(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="rec-1"))

        with patch.object(proc, "process_md_document", side_effect=mock_md):
            events = await _collect(
                proc.process_txt_document(
                    recordName="test.txt",
                    recordId="rec-1",
                    version=1,
                    source="upload",
                    orgId="org-1",
                    txt_binary=b"Hello world",
                    virtual_record_id="vr-1",
                    recordType="FILE",
                    connectorName="",
                    origin="UPLOAD",
                )
            )

        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_decode_failure_raises(self):
        """Raises ValueError when all decodings fail."""
        proc, _, _, _ = _make_processor()

        # Create bytes that cannot be decoded by any encoding - use invalid continuation bytes
        bad_bytes = MagicMock()
        bad_bytes.decode = MagicMock(side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "bad"))

        with pytest.raises(Exception):
            await _collect(
                proc.process_txt_document(
                    recordName="test.txt",
                    recordId="rec-1",
                    version=1,
                    source="upload",
                    orgId="org-1",
                    txt_binary=bad_bytes,
                    virtual_record_id="vr-1",
                    recordType="FILE",
                    connectorName="",
                    origin="UPLOAD",
                )
            )


# ===========================================================================
# Processor.process_pptx_document
# ===========================================================================


class TestProcessPptxDocument:
    """Tests for Processor.process_pptx_document."""

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        """When record not found, yields indexing_complete."""
        proc, _, gp, _ = _make_processor()
        gp.get_document.return_value = None

        with patch("app.events.processor.DoclingProcessor") as mock_dp:
            mock_instance = AsyncMock()
            mock_instance.parse_document.return_value = MagicMock()
            mock_instance.create_blocks.return_value = MagicMock()
            mock_dp.return_value = mock_instance

            events = await _collect(
                proc.process_pptx_document(
                    recordName="test.pptx",
                    recordId="rec-1",
                    version=1,
                    source="upload",
                    orgId="org-1",
                    pptx_binary=b"pptx",
                    virtual_record_id="vr-1",
                )
            )

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_success(self):
        """Successful processing yields both events."""
        proc, _, gp, _ = _make_processor()
        gp.get_document.return_value = _base_record_dict()

        with patch("app.events.processor.DoclingProcessor") as mock_dp:
            mock_instance = AsyncMock()
            mock_instance.parse_document.return_value = MagicMock()
            mock_instance.create_blocks.return_value = MagicMock()
            mock_dp.return_value = mock_instance

            with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
                mock_pipeline.return_value = AsyncMock()

                events = await _collect(
                    proc.process_pptx_document(
                        recordName="test.pptx",
                        recordId="rec-1",
                        version=1,
                        source="upload",
                        orgId="org-1",
                        pptx_binary=b"pptx",
                        virtual_record_id="vr-1",
                    )
                )

        assert len(events) == 2


# ===========================================================================
# Processor.process_ppt_document
# ===========================================================================


class TestProcessPptDocument:
    """Tests for Processor.process_ppt_document."""

    @pytest.mark.asyncio
    async def test_converts_ppt_to_pptx_and_delegates(self):
        proc, _, _, _ = _make_processor()

        mock_parser = MagicMock()
        mock_parser.convert_ppt_to_pptx.return_value = b"pptx-content"
        proc.parsers["ppt"] = mock_parser

        async def mock_pptx(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="rec-1"))

        with patch.object(proc, "process_pptx_document", side_effect=mock_pptx):
            events = await _collect(
                proc.process_ppt_document(
                    recordName="test.ppt",
                    recordId="rec-1",
                    version=1,
                    source="upload",
                    orgId="org-1",
                    ppt_binary=b"ppt",
                    virtual_record_id="vr-1",
                )
            )

        assert len(events) == 2
        mock_parser.convert_ppt_to_pptx.assert_called_once()


# ===========================================================================
# Processor.process_delimited_document
# ===========================================================================


class TestProcessDelimitedDocument:
    """Tests for Processor.process_delimited_document."""

    @pytest.mark.asyncio
    async def test_empty_file_marks_empty(self):
        """Empty file yields both events and marks empty."""
        proc, _, gp, _ = _make_processor()
        gp.get_document.return_value = {"_key": "rec-1"}
        gp.batch_upsert_nodes.return_value = True

        mock_parser = MagicMock()
        mock_parser.read_raw_rows.return_value = []
        proc.parsers["csv"] = mock_parser

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {})

            events = await _collect(
                proc.process_delimited_document(
                    recordName="test.csv",
                    recordId="rec-1",
                    file_binary=b"",
                    virtual_record_id="vr-1",
                )
            )

        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        """Record not found yields both events."""
        proc, _, gp, _ = _make_processor()
        gp.get_document.return_value = None

        mock_parser = MagicMock()
        mock_parser.read_raw_rows.return_value = [["a", "b"]]
        proc.parsers["csv"] = mock_parser

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {})

            events = await _collect(
                proc.process_delimited_document(
                    recordName="test.csv",
                    recordId="rec-1",
                    file_binary=b"a,b\n1,2",
                    virtual_record_id="vr-1",
                )
            )

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_success(self):
        """Successful processing yields both events."""
        proc, _, gp, _ = _make_processor()
        gp.get_document.return_value = _base_record_dict()

        mock_parser = MagicMock()
        mock_parser.read_raw_rows.return_value = [["a", "b"], ["1", "2"]]
        mock_parser.find_tables_in_csv.return_value = [[["a", "b"], ["1", "2"]]]
        mock_parser.get_blocks_from_csv_with_multiple_tables = AsyncMock(return_value=MagicMock())
        proc.parsers["csv"] = mock_parser

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {})
            with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
                mock_pipeline.return_value = AsyncMock()

                events = await _collect(
                    proc.process_delimited_document(
                        recordName="test.csv",
                        recordId="rec-1",
                        file_binary=b"a,b\n1,2",
                        virtual_record_id="vr-1",
                    )
                )

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_uses_custom_extension(self):
        """Uses custom extension parser when provided."""
        proc, _, gp, _ = _make_processor()
        gp.get_document.return_value = {"_key": "rec-1"}
        gp.batch_upsert_nodes.return_value = True

        mock_parser = MagicMock()
        mock_parser.read_raw_rows.return_value = []
        proc.parsers["tsv"] = mock_parser

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {})

            events = await _collect(
                proc.process_delimited_document(
                    recordName="test.tsv",
                    recordId="rec-1",
                    file_binary=b"",
                    virtual_record_id="vr-1",
                    extension="tsv",
                )
            )

        assert len(events) == 2


# ===========================================================================
# Processor.process_image - successful processing path
# ===========================================================================


class TestProcessImageSuccess:
    """Tests for successful image processing."""

    @pytest.mark.asyncio
    async def test_multimodal_processes_through_pipeline(self):
        """When both LLM and embedding are multimodal, processes image fully."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = _base_record_dict(
            mimeType="image/png",
            recordType="FILE",
        )

        mock_parser = MagicMock()
        mock_parser.parse_image.return_value = MagicMock()
        proc.parsers[".png"] = mock_parser

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": True})
            with patch("app.events.processor.get_embedding_model_config", new_callable=AsyncMock) as mock_emb:
                mock_emb.return_value = {"isMultimodal": True}
                with patch("app.events.processor.get_extension_from_mimetype", return_value=".png"):
                    with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
                        mock_pipeline.return_value = AsyncMock()

                        events = await _collect(proc.process_image("rec-1", b"imgdata", "vr-1"))

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"


# ===========================================================================
# Processor.process_image - additional paths (lines 145, 154-157, 165, 170)
# ===========================================================================


class TestProcessImageAdditional:
    """Additional tests for process_image edge cases."""

    @pytest.mark.asyncio
    async def test_no_content_raises(self):
        """Should raise when content is None."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = _base_record_dict(mimeType="image/png")

        with pytest.raises(Exception, match="No image data"):
            await _collect(proc.process_image("rec-1", None, "vr-1"))

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        """Should yield both events when record is None."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = None

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": True})
            with patch("app.events.processor.get_embedding_model_config", new_callable=AsyncMock) as mock_emb:
                mock_emb.return_value = {"isMultimodal": True}
                events = await _collect(proc.process_image("rec-1", b"imgdata", "vr-1"))

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_no_multimodal_sets_enable_status(self):
        """When neither embedding nor LLM is multimodal, should set ENABLE_MULTIMODAL_MODELS."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = _base_record_dict(mimeType="image/png")
        gp.batch_upsert_nodes.return_value = True

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            with patch("app.events.processor.get_embedding_model_config", new_callable=AsyncMock) as mock_emb:
                mock_emb.return_value = {"isMultimodal": False}
                events = await _collect(proc.process_image("rec-1", b"imgdata", "vr-1"))

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_no_multimodal_batch_upsert_failure(self):
        """When batch_upsert_nodes fails, should raise."""
        from app.exceptions.indexing_exceptions import DocumentProcessingError
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = _base_record_dict(mimeType="image/png")
        gp.batch_upsert_nodes.return_value = False

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            with patch("app.events.processor.get_embedding_model_config", new_callable=AsyncMock) as mock_emb:
                mock_emb.return_value = {"isMultimodal": False}
                with pytest.raises(DocumentProcessingError, match="Failed to update indexing status"):
                    await _collect(proc.process_image("rec-1", b"imgdata", "vr-1"))

    @pytest.mark.asyncio
    async def test_no_mime_type_raises(self):
        """When record has no mimeType, should raise."""
        proc, _, gp, config = _make_processor()
        record = _base_record_dict(mimeType=None)
        gp.get_document.return_value = record

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": True})
            with patch("app.events.processor.get_embedding_model_config", new_callable=AsyncMock) as mock_emb:
                mock_emb.return_value = {"isMultimodal": True}
                with pytest.raises(Exception, match="No mime type"):
                    await _collect(proc.process_image("rec-1", b"imgdata", "vr-1"))

    @pytest.mark.asyncio
    async def test_unsupported_extension_raises(self):
        """When parser for extension is not found, should raise."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = _base_record_dict(mimeType="image/png")
        proc.parsers = {}  # No parsers

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": True})
            with patch("app.events.processor.get_embedding_model_config", new_callable=AsyncMock) as mock_emb:
                mock_emb.return_value = {"isMultimodal": True}
                with patch("app.events.processor.get_extension_from_mimetype", return_value=".png"):
                    with pytest.raises(Exception, match="Unsupported extension"):
                        await _collect(proc.process_image("rec-1", b"imgdata", "vr-1"))


# ===========================================================================
# Processor.process_gmail_message (lines 215-217)
# ===========================================================================


class TestProcessGmailMessage:
    """Tests for process_gmail_message."""

    @pytest.mark.asyncio
    async def test_delegates_to_html_processing(self):
        """Should delegate to process_html_document."""
        proc, _, gp, config = _make_processor()

        async def mock_html(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="rec-1"))

        proc.process_html_document = mock_html
        events = await _collect(proc.process_gmail_message(
            "email.html", "rec-1", 1, "gmail", "org-1", b"<html>hi</html>", "vr-1"
        ))
        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_error_propagates(self):
        """Errors from html processing should propagate."""
        proc, _, gp, config = _make_processor()

        async def failing_html(*args, **kwargs):
            raise Exception("html failed")
            yield  # noqa

        proc.process_html_document = failing_html
        with pytest.raises(Exception, match="html failed"):
            await _collect(proc.process_gmail_message(
                "email.html", "rec-1", 1, "gmail", "org-1", b"<html>hi</html>", "vr-1"
            ))


# ===========================================================================
# Processor.process_pdf_with_pymupdf (lines 263-265)
# ===========================================================================


class TestProcessPdfWithPyMuPDF:
    """Tests for process_pdf_with_pymupdf."""

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        """Should yield indexing_complete when record not found."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = None

        with patch("app.events.processor.PyMuPDFOpenCVProcessor") as mock_proc:
            mock_instance = AsyncMock()
            mock_instance.parse_document = AsyncMock(return_value={})
            mock_instance.create_blocks = AsyncMock(return_value=MagicMock())
            mock_proc.return_value = mock_instance
            events = await _collect(proc.process_pdf_with_pymupdf(
                "test.pdf", "rec-1", b"pdfdata", "vr-1"
            ))

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_success_path(self):
        """Should yield parsing_complete and indexing_complete."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = _base_record_dict()

        with patch("app.events.processor.PyMuPDFOpenCVProcessor") as mock_proc:
            mock_instance = AsyncMock()
            mock_instance.parse_document = AsyncMock(return_value={})
            mock_instance.create_blocks = AsyncMock(return_value=MagicMock())
            mock_proc.return_value = mock_instance
            with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
                mock_pipeline.return_value = AsyncMock()
                events = await _collect(proc.process_pdf_with_pymupdf(
                    "test.pdf", "rec-1", b"pdfdata", "vr-1"
                ))

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"


# ===========================================================================
# Processor.process_pdf_with_docling (lines 290-291, 298-300, 315-317)
# ===========================================================================


class TestProcessPdfWithDocling:
    """Tests for process_pdf_with_docling."""

    @pytest.mark.asyncio
    async def test_docling_parse_fails(self):
        """Should yield docling_failed when parse returns None."""
        proc, _, gp, config = _make_processor()
        proc.docling_client.parse_pdf = AsyncMock(return_value=None)

        events = await _collect(proc.process_pdf_with_docling(
            "test.pdf", "rec-1", b"pdfdata", "vr-1"
        ))
        assert events[0].event == "docling_failed"

    @pytest.mark.asyncio
    async def test_block_creation_fails(self):
        """Should raise when create_blocks returns None."""
        proc, _, gp, config = _make_processor()
        proc.docling_client.parse_pdf = AsyncMock(return_value={"parsed": True})
        proc.docling_client.create_blocks = AsyncMock(return_value=None)

        with pytest.raises(Exception, match="failed to create blocks"):
            await _collect(proc.process_pdf_with_docling(
                "test.pdf", "rec-1", b"pdfdata", "vr-1"
            ))

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        """Should yield indexing_complete when record not found."""
        proc, _, gp, config = _make_processor()
        proc.docling_client.parse_pdf = AsyncMock(return_value={"parsed": True})
        proc.docling_client.create_blocks = AsyncMock(return_value=MagicMock())
        gp.get_document.return_value = None

        events = await _collect(proc.process_pdf_with_docling(
            "test.pdf", "rec-1", b"pdfdata", "vr-1"
        ))
        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_success_path(self):
        """Should yield both events on success."""
        proc, _, gp, config = _make_processor()
        proc.docling_client.parse_pdf = AsyncMock(return_value={"parsed": True})
        proc.docling_client.create_blocks = AsyncMock(return_value=MagicMock())
        gp.get_document.return_value = _base_record_dict()

        with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
            mock_pipeline.return_value = AsyncMock()
            events = await _collect(proc.process_pdf_with_docling(
                "test.pdf", "rec-1", b"pdfdata", "vr-1"
            ))

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"


# ===========================================================================
# Processor.process_doc_document (lines 587-600)
# ===========================================================================


class TestProcessDocDocument:
    """Tests for process_doc_document."""

    @pytest.mark.asyncio
    async def test_delegates_to_docx_processing(self):
        """Should convert doc to docx and process."""
        proc, _, gp, config = _make_processor()
        from app.config.constants.arangodb import ExtensionTypes

        mock_parser = MagicMock()
        mock_parser.convert_doc_to_docx.return_value = b"docx_binary"
        proc.parsers[ExtensionTypes.DOC.value] = mock_parser

        async def mock_docx(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="rec-1"))

        proc.process_docx_document = mock_docx
        events = await _collect(proc.process_doc_document(
            "test.doc", "rec-1", 1, "upload", "org-1", b"doc_binary", "vr-1"
        ))
        assert events[0].event == "parsing_complete"
        mock_parser.convert_doc_to_docx.assert_called_once()


# ===========================================================================
# Processor.process_docx_document (lines 674-805)
# ===========================================================================


class TestProcessDocxDocument:
    """Tests for process_docx_document."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Successful DOCX processing yields both events."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = _base_record_dict()

        with patch("app.events.processor.DoclingProcessor") as mock_proc:
            mock_instance = AsyncMock()
            mock_instance.parse_document = AsyncMock(return_value={})
            mock_instance.create_blocks = AsyncMock(return_value=MagicMock())
            mock_proc.return_value = mock_instance
            with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
                mock_pipeline.return_value = AsyncMock()
                events = await _collect(proc.process_docx_document(
                    "test.docx", "rec-1", 1, "upload", "org-1", b"docx_binary", "vr-1"
                ))

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        """Should yield indexing_complete when record not found."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = None

        with patch("app.events.processor.DoclingProcessor") as mock_proc:
            mock_instance = AsyncMock()
            mock_instance.parse_document = AsyncMock(return_value={})
            mock_instance.create_blocks = AsyncMock(return_value=MagicMock())
            mock_proc.return_value = mock_instance
            events = await _collect(proc.process_docx_document(
                "test.docx", "rec-1", 1, "upload", "org-1", b"docx_binary", "vr-1"
            ))

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"


# ===========================================================================
# Processor._enhance_tables_with_llm (lines 674-805)
# ===========================================================================


class TestEnhanceTablesWithLlm:
    """Tests for _enhance_tables_with_llm."""

    @pytest.mark.asyncio
    async def test_no_table_groups(self):
        """Should return early when no TABLE block groups."""
        from app.models.blocks import BlocksContainer
        proc, _, gp, config = _make_processor()
        container = BlocksContainer(blocks=[], block_groups=[])
        await proc._enhance_tables_with_llm(container)
        # No error, just returns

    @pytest.mark.asyncio
    async def test_table_group_no_markdown(self):
        """Should skip table groups without table_markdown."""
        from app.models.blocks import BlockGroup, BlocksContainer, GroupType
        proc, _, gp, config = _make_processor()
        bg = BlockGroup(index=0, type=GroupType.TABLE, data=None)
        container = BlocksContainer(blocks=[], block_groups=[bg])

        with patch("app.utils.indexing_helpers.get_table_summary_n_headers", new_callable=AsyncMock) as mock_ts:
            await proc._enhance_tables_with_llm(container)
        mock_ts.assert_not_awaited()


# ===========================================================================
# Processor._mark_record (lines 1555-1584)
# ===========================================================================


class TestMarkRecord:
    """Tests for _mark_record."""

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        """Should raise DocumentProcessingError when record not found."""
        from app.exceptions.indexing_exceptions import DocumentProcessingError
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = None

        with pytest.raises(DocumentProcessingError, match="Record not found"):
            await proc._mark_record("rec-1", ProgressStatus.EMPTY)

    @pytest.mark.asyncio
    async def test_batch_upsert_failure(self):
        """Should raise when batch_upsert_nodes returns False."""
        from app.exceptions.indexing_exceptions import DocumentProcessingError
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = _base_record_dict()
        gp.batch_upsert_nodes.return_value = False

        with patch("app.events.processor.get_epoch_timestamp_in_ms", return_value=12345):
            with pytest.raises(DocumentProcessingError, match="Failed to update indexing status"):
                await proc._mark_record("rec-1", ProgressStatus.EMPTY)

    @pytest.mark.asyncio
    async def test_success(self):
        """Should update record status successfully."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = _base_record_dict()
        gp.batch_upsert_nodes.return_value = True

        with patch("app.events.processor.get_epoch_timestamp_in_ms", return_value=12345):
            await proc._mark_record("rec-1", ProgressStatus.EMPTY)

        gp.batch_upsert_nodes.assert_awaited_once()


# ===========================================================================
# Processor.process_html_document (lines 1601, 1605-1606, 1609-1612, 1629-1631)
# ===========================================================================


class TestProcessHtmlDocument:
    """Tests for process_html_document."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Should delegate to process_md_document after conversion."""
        from app.config.constants.arangodb import ExtensionTypes
        proc, _, gp, config = _make_processor()

        mock_html_parser = MagicMock()
        mock_html_parser.replace_relative_image_urls.return_value = "<html>clean</html>"
        proc.parsers[ExtensionTypes.HTML.value] = mock_html_parser

        async def mock_md(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="rec-1"))

        proc.process_md_document = mock_md

        with patch("app.events.processor.convert", return_value="# Markdown"):
            events = await _collect(proc.process_html_document(
                "test.html", "rec-1", 1, "upload", "org-1", b"<html>test</html>", "vr-1"
            ))

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_html_cleaning_failure_fallback(self):
        """When BeautifulSoup fails, should fall back to raw content."""
        from app.config.constants.arangodb import ExtensionTypes
        proc, _, gp, config = _make_processor()

        mock_html_parser = MagicMock()
        mock_html_parser.replace_relative_image_urls.return_value = "<html>raw</html>"
        proc.parsers[ExtensionTypes.HTML.value] = mock_html_parser

        async def mock_md(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="rec-1"))

        proc.process_md_document = mock_md

        with patch("app.events.processor.BeautifulSoup", side_effect=Exception("soup failed")):
            with patch("app.events.processor.convert", return_value="# Markdown"):
                events = await _collect(proc.process_html_document(
                    "test.html", "rec-1", 1, "upload", "org-1", b"<html>test</html>", "vr-1"
                ))

        assert len(events) == 2


# ===========================================================================
# Processor.process_mdx_document (lines 1673, 1684-1687)
# ===========================================================================


class TestProcessMdxDocument:
    """Tests for process_mdx_document."""

    @pytest.mark.asyncio
    async def test_delegates_to_md_processing(self):
        """Should convert MDX to MD and delegate."""
        from app.config.constants.arangodb import ExtensionTypes
        proc, _, gp, config = _make_processor()

        mock_parser = MagicMock()
        mock_parser.convert_mdx_to_md.return_value = "# Converted markdown"
        proc.parsers[ExtensionTypes.MDX.value] = mock_parser

        async def mock_md(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="rec-1"))

        proc.process_md_document = mock_md
        events = await _collect(proc.process_mdx_document(
            "test.mdx", "rec-1", 1, "upload", "org-1", "mdx content", "vr-1"
        ))
        assert events[0].event == "parsing_complete"
        mock_parser.convert_mdx_to_md.assert_called_once()


# ===========================================================================
# Processor.process_md_document (lines 1705-1711, 1731-1734, 1739-1752)
# ===========================================================================


class TestProcessMdDocument:
    """Tests for process_md_document."""

    @pytest.mark.asyncio
    async def test_empty_markdown_marks_empty(self):
        """Empty markdown should mark record as EMPTY."""
        proc, _, gp, config = _make_processor()
        proc._mark_record = AsyncMock()

        # Use empty markdown
        events = await _collect(proc.process_md_document(
            "test.md", "rec-1", b"   ", "vr-1"
        ))

        proc._mark_record.assert_awaited_once()
        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        """Should yield indexing_complete when record not found."""
        from app.config.constants.arangodb import ExtensionTypes
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = None

        mock_parser = MagicMock()
        mock_parser.extract_and_replace_images.return_value = ("# Content", [])
        mock_parser.parse_string.return_value = b"bytes"
        proc.parsers[ExtensionTypes.MD.value] = mock_parser

        with patch("app.events.processor.DoclingProcessor") as mock_proc:
            mock_instance = AsyncMock()
            mock_instance.parse_document = AsyncMock(return_value={})
            mock_instance.create_blocks = AsyncMock(return_value=MagicMock(blocks=[], block_groups=[]))
            mock_proc.return_value = mock_instance
            events = await _collect(proc.process_md_document(
                "test.md", "rec-1", b"# Hello world", "vr-1"
            ))

        assert events[-1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_string_input(self):
        """Should handle string input (not bytes)."""
        from app.config.constants.arangodb import ExtensionTypes
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = _base_record_dict()

        mock_parser = MagicMock()
        mock_parser.extract_and_replace_images.return_value = ("# Content", [])
        mock_parser.parse_string.return_value = b"bytes"
        proc.parsers[ExtensionTypes.MD.value] = mock_parser

        with patch("app.events.processor.DoclingProcessor") as mock_proc:
            mock_instance = AsyncMock()
            mock_instance.parse_document = AsyncMock(return_value={})
            mock_instance.create_blocks = AsyncMock(return_value=MagicMock(blocks=[], block_groups=[]))
            mock_proc.return_value = mock_instance
            with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
                mock_pipeline.return_value = AsyncMock()
                events = await _collect(proc.process_md_document(
                    "test.md", "rec-1", "# Hello world", "vr-1"
                ))

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"


# ===========================================================================
# Processor.process_txt_document (lines 1768-1770)
# ===========================================================================


class TestProcessTxtDocument:
    """Tests for process_txt_document."""

    @pytest.mark.asyncio
    async def test_decode_failure(self):
        """Should raise when all encodings fail."""
        proc, _, gp, config = _make_processor()

        # Create binary that fails all decodings
        bad_binary = MagicMock()
        bad_binary.decode = MagicMock(side_effect=UnicodeDecodeError('utf-8', b'', 0, 1, 'bad'))

        with pytest.raises(Exception):
            await _collect(proc.process_txt_document(
                "test.txt", "rec-1", 1, "upload", "org-1",
                bad_binary, "vr-1", "FILE", "KB", "UPLOAD"
            ))

    @pytest.mark.asyncio
    async def test_success_delegates_to_md(self):
        """Should decode and delegate to process_md_document."""
        proc, _, gp, config = _make_processor()

        async def mock_md(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="rec-1"))

        proc.process_md_document = mock_md
        events = await _collect(proc.process_txt_document(
            "test.txt", "rec-1", 1, "upload", "org-1",
            b"Hello text", "vr-1", "FILE", "KB", "UPLOAD"
        ))
        assert events[0].event == "parsing_complete"


# ===========================================================================
# Processor.process_pptx_document (lines 1865-1867)
# ===========================================================================


class TestProcessPptxDocument:
    """Tests for process_pptx_document."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Successful PPTX processing yields both events."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = _base_record_dict()

        with patch("app.events.processor.DoclingProcessor") as mock_proc:
            mock_instance = AsyncMock()
            mock_instance.parse_document = AsyncMock(return_value={})
            mock_instance.create_blocks = AsyncMock(return_value=MagicMock())
            mock_proc.return_value = mock_instance
            with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
                mock_pipeline.return_value = AsyncMock()
                events = await _collect(proc.process_pptx_document(
                    "test.pptx", "rec-1", 1, "upload", "org-1", b"pptx_binary", "vr-1"
                ))

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        """Should yield indexing_complete when record not found."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = None

        with patch("app.events.processor.DoclingProcessor") as mock_proc:
            mock_instance = AsyncMock()
            mock_instance.parse_document = AsyncMock(return_value={})
            mock_instance.create_blocks = AsyncMock(return_value=MagicMock())
            mock_proc.return_value = mock_instance
            events = await _collect(proc.process_pptx_document(
                "test.pptx", "rec-1", 1, "upload", "org-1", b"pptx_binary", "vr-1"
            ))

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"


# ===========================================================================
# Processor.process_excel_document (lines 1409-1423)
# ===========================================================================


class TestProcessExcelDocument:
    """Tests for process_excel_document."""

    @pytest.mark.asyncio
    async def test_empty_binary(self):
        """Empty binary should mark record as EMPTY."""
        from app.config.constants.arangodb import ExtensionTypes
        proc, _, gp, config = _make_processor()
        proc._mark_record = AsyncMock()

        mock_parser = MagicMock()
        proc.parsers[ExtensionTypes.XLSX.value] = mock_parser

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {})
            events = await _collect(proc.process_excel_document(
                "test.xlsx", "rec-1", 1, "upload", "org-1", b"", "vr-1"
            ))

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_success(self):
        """Successful Excel processing."""
        from app.config.constants.arangodb import ExtensionTypes
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = _base_record_dict()

        mock_parser = MagicMock()
        mock_parser.load_workbook_from_binary = MagicMock()
        mock_parser.create_blocks = AsyncMock(return_value=MagicMock())
        proc.parsers[ExtensionTypes.XLSX.value] = mock_parser

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {})
            with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
                mock_pipeline.return_value = AsyncMock()
                events = await _collect(proc.process_excel_document(
                    "test.xlsx", "rec-1", 1, "upload", "org-1", b"excel_data", "vr-1"
                ))

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        """Should yield indexing_complete when record not found."""
        from app.config.constants.arangodb import ExtensionTypes
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = None

        mock_parser = MagicMock()
        mock_parser.load_workbook_from_binary = MagicMock()
        mock_parser.create_blocks = AsyncMock(return_value=MagicMock())
        proc.parsers[ExtensionTypes.XLSX.value] = mock_parser

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {})
            events = await _collect(proc.process_excel_document(
                "test.xlsx", "rec-1", 1, "upload", "org-1", b"excel_data", "vr-1"
            ))

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"


# ===========================================================================
# Processor.process_xls_document (lines 1445-1447)
# ===========================================================================


class TestProcessXlsDocument:
    """Tests for process_xls_document."""

    @pytest.mark.asyncio
    async def test_delegates_to_excel(self):
        """Should convert XLS to XLSX and delegate."""
        from app.config.constants.arangodb import ExtensionTypes
        proc, _, gp, config = _make_processor()

        mock_xls_parser = MagicMock()
        mock_xls_parser.convert_xls_to_xlsx.return_value = b"xlsx_binary"
        proc.parsers[ExtensionTypes.XLS.value] = mock_xls_parser

        async def mock_excel(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="rec-1"))

        proc.process_excel_document = mock_excel

        events = await _collect(proc.process_xls_document(
            "test.xls", "rec-1", 1, "upload", "org-1", b"xls_binary", "vr-1"
        ))
        assert events[0].event == "parsing_complete"
        mock_xls_parser.convert_xls_to_xlsx.assert_called_once()


# ===========================================================================
# Processor.process_blocks (lines 924-950)
# ===========================================================================


class TestProcessBlocks:
    """Tests for process_blocks."""

    @pytest.mark.asyncio
    async def test_bytes_input(self):
        """Should handle bytes input."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = _base_record_dict()
        proc._process_blockgroups_through_docling = AsyncMock(return_value=MagicMock(block_groups=[], blocks=[]))
        proc._enhance_tables_with_llm = AsyncMock()

        blocks_dict = '{"blocks": [], "block_groups": []}'

        with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
            mock_pipeline.return_value = AsyncMock()
            events = await _collect(proc.process_blocks(
                "test", "rec-1", 1, "upload", "org-1",
                blocks_dict.encode('utf-8'), "vr-1"
            ))

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_dict_input(self):
        """Should handle dict input."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = _base_record_dict()
        proc._process_blockgroups_through_docling = AsyncMock(return_value=MagicMock(block_groups=[], blocks=[]))
        proc._enhance_tables_with_llm = AsyncMock()

        with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
            mock_pipeline.return_value = AsyncMock()
            events = await _collect(proc.process_blocks(
                "test", "rec-1", 1, "upload", "org-1",
                {"blocks": [], "block_groups": []}, "vr-1"
            ))

        assert events[0].event == "parsing_complete"
        assert events[1].event == "indexing_complete"

    @pytest.mark.asyncio
    async def test_invalid_type_raises(self):
        """Should raise for invalid blocks_data type."""
        proc, _, gp, config = _make_processor()

        with pytest.raises(ValueError, match="Invalid blocks_data type"):
            await _collect(proc.process_blocks(
                "test", "rec-1", 1, "upload", "org-1",
                12345, "vr-1"
            ))

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        """Should yield indexing_complete when record not found."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = None
        proc._process_blockgroups_through_docling = AsyncMock(return_value=MagicMock(block_groups=[], blocks=[]))
        proc._enhance_tables_with_llm = AsyncMock()

        events = await _collect(proc.process_blocks(
            "test", "rec-1", 1, "upload", "org-1",
            '{"blocks": [], "block_groups": []}', "vr-1"
        ))

        assert events[-1].event == "indexing_complete"


# ===========================================================================
# Processor._separate_block_groups_by_index (lines 888-909)
# ===========================================================================


class TestSeparateBlockGroupsByIndex:
    """Tests for _separate_block_groups_by_index."""

    def test_mixed_indices(self):
        """Should separate block groups by index presence."""
        proc, _, gp, config = _make_processor()

        bg_with = MagicMock()
        bg_with.index = 0
        bg_without = MagicMock()
        bg_without.index = None

        with_idx, without_idx = proc._separate_block_groups_by_index([bg_with, bg_without])
        assert len(with_idx) == 1
        assert len(without_idx) == 1

    def test_all_with_index(self):
        """All block groups have index."""
        proc, _, gp, config = _make_processor()

        bgs = []
        for i in range(3):
            bg = MagicMock()
            bg.index = i
            bgs.append(bg)
        with_idx, without_idx = proc._separate_block_groups_by_index(bgs)
        assert len(with_idx) == 3
        assert len(without_idx) == 0

    def test_all_without_index(self):
        """All block groups have None index."""
        proc, _, gp, config = _make_processor()

        bgs = [MagicMock(index=None), MagicMock(index=None)]
        with_idx, without_idx = proc._separate_block_groups_by_index(bgs)
        assert len(with_idx) == 0
        assert len(without_idx) == 2

    def test_empty_list(self):
        """Empty list returns two empty lists."""
        proc, _, gp, config = _make_processor()

        with_idx, without_idx = proc._separate_block_groups_by_index([])
        assert with_idx == []
        assert without_idx == []


# ===========================================================================
# Processor._enhance_tables_with_llm (lines 662-805)
# ===========================================================================


class TestEnhanceTablesWithLlm:
    """Tests for Processor._enhance_tables_with_llm."""

    @pytest.mark.asyncio
    async def test_no_table_groups_skips(self):
        """No TABLE block groups => returns immediately."""
        from app.models.blocks import BlockGroup, BlocksContainer, GroupType
        proc, _, _, _ = _make_processor()

        # Only non-TABLE groups
        bg = BlockGroup(index=0, type=GroupType.SHEET)
        container = BlocksContainer(blocks=[], block_groups=[bg])

        await proc._enhance_tables_with_llm(container)
        # Should not error

    @pytest.mark.asyncio
    async def test_table_group_no_markdown_skips(self):
        """TABLE group with no table_markdown is skipped."""
        from app.models.blocks import BlockGroup, BlocksContainer, GroupType
        proc, _, _, _ = _make_processor()

        bg = BlockGroup(index=0, type=GroupType.TABLE, data={"no_markdown": True})
        container = BlocksContainer(blocks=[], block_groups=[bg])

        await proc._enhance_tables_with_llm(container)
        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_table_group_llm_returns_none(self):
        """When LLM returns None for table summary."""
        from app.models.blocks import BlockGroup, BlocksContainer, GroupType
        proc, _, _, config = _make_processor()

        bg = BlockGroup(index=0, type=GroupType.TABLE, data={"table_markdown": "| A | B |"})
        container = BlocksContainer(blocks=[], block_groups=[bg])

        with patch("app.utils.indexing_helpers.get_table_summary_n_headers", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = None
            await proc._enhance_tables_with_llm(container)

        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_table_group_enhancement_success(self):
        """Successful table enhancement with summary and headers."""
        from app.models.blocks import (
            Block, BlockGroup, BlockGroupChildren, BlocksContainer,
            BlockType, GroupType, DataFormat,
        )
        proc, _, _, config = _make_processor()

        # Create a table row block
        row_block = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data={"cells": ["val1", "val2"]},
            parent_index=0,
        )

        # Create table group with children
        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            data={"table_markdown": "| A | B |\n| val1 | val2 |"},
        )
        bg.children = BlockGroupChildren.from_indices(block_indices=[0])

        container = BlocksContainer(blocks=[row_block], block_groups=[bg])

        mock_response = MagicMock()
        mock_response.summary = "This is a test table"
        mock_response.headers = ["Col_A", "Col_B"]

        with patch("app.utils.indexing_helpers.get_table_summary_n_headers", new_callable=AsyncMock) as mock_summary:
            mock_summary.return_value = mock_response
            with patch("app.utils.indexing_helpers.get_rows_text", new_callable=AsyncMock) as mock_rows:
                mock_rows.return_value = (["Row 1: val1 is in Col_A, val2 is in Col_B"], [])
                await proc._enhance_tables_with_llm(container)

        assert bg.description == "This is a test table"
        assert bg.data["table_summary"] == "This is a test table"

    @pytest.mark.asyncio
    async def test_table_group_exception_continues(self):
        """Exception in one table group doesn't stop others."""
        from app.models.blocks import BlockGroup, BlocksContainer, GroupType
        proc, _, _, config = _make_processor()

        bg1 = BlockGroup(index=0, type=GroupType.TABLE, data={"table_markdown": "| A |"})
        bg2 = BlockGroup(index=1, type=GroupType.TABLE, data={"table_markdown": "| B |"})
        container = BlocksContainer(blocks=[], block_groups=[bg1, bg2])

        with patch("app.utils.indexing_helpers.get_table_summary_n_headers", new_callable=AsyncMock) as mock_llm:
            # First call raises, second succeeds
            mock_response = MagicMock()
            mock_response.summary = "Summary"
            mock_response.headers = ["B"]
            mock_llm.side_effect = [RuntimeError("fail"), mock_response]

            await proc._enhance_tables_with_llm(container)

        proc.logger.error.assert_called()


# ===========================================================================
# Processor._process_blockgroup_images (lines 924-950)
# ===========================================================================


class TestProcessBlockgroupImages:
    """Tests for Processor._process_blockgroup_images."""

    @pytest.mark.asyncio
    async def test_no_parsers_returns_unmodified(self):
        """When md/image parsers not available, returns original markdown."""
        proc, _, _, _ = _make_processor()
        proc.parsers = {}  # no parsers

        md, caption_map = await proc._process_blockgroup_images("# Hello", 0)
        assert md == "# Hello"
        assert caption_map == {}

    @pytest.mark.asyncio
    async def test_with_images_extracted(self):
        """Images are extracted and converted to base64."""
        from app.config.constants.arangodb import ExtensionTypes
        proc, _, _, _ = _make_processor()

        mock_md_parser = MagicMock()
        mock_md_parser.extract_and_replace_images.return_value = (
            "# Hello ![img](replaced)",
            [{"url": "http://example.com/img.png", "new_alt_text": "img"}],
        )

        mock_image_parser = MagicMock()
        mock_image_parser.urls_to_base64 = AsyncMock(return_value=["base64data"])

        proc.parsers = {
            ExtensionTypes.MD.value: mock_md_parser,
            ExtensionTypes.PNG.value: mock_image_parser,
        }

        md, caption_map = await proc._process_blockgroup_images("# Hello ![img](http://example.com/img.png)", 0)
        assert "img" in caption_map
        assert caption_map["img"] == "base64data"

    @pytest.mark.asyncio
    async def test_no_images_in_markdown(self):
        """Markdown with no images returns empty caption map."""
        from app.config.constants.arangodb import ExtensionTypes
        proc, _, _, _ = _make_processor()

        mock_md_parser = MagicMock()
        mock_md_parser.extract_and_replace_images.return_value = ("# Hello", [])
        mock_image_parser = MagicMock()

        proc.parsers = {
            ExtensionTypes.MD.value: mock_md_parser,
            ExtensionTypes.PNG.value: mock_image_parser,
        }

        md, caption_map = await proc._process_blockgroup_images("# Hello", 0)
        assert caption_map == {}


# ===========================================================================
# Processor._map_base64_images_to_blocks (lines 952-982)
# ===========================================================================


class TestMapBase64ImagesToBlocks:
    """Tests for Processor._map_base64_images_to_blocks."""

    def test_empty_caption_map_noop(self):
        """Empty caption map does nothing."""
        proc, _, _, _ = _make_processor()
        blocks = [MagicMock()]
        proc._map_base64_images_to_blocks(blocks, {}, 0)
        # No error should occur

    def test_image_block_gets_uri(self):
        """Image block with matching caption gets base64 uri."""
        from app.models.blocks import BlockType
        proc, _, _, _ = _make_processor()

        block = MagicMock()
        block.type = BlockType.IMAGE.value
        block.image_metadata = MagicMock()
        block.image_metadata.captions = "my_image"
        block.data = {}

        proc._map_base64_images_to_blocks([block], {"my_image": "base64uri"}, 0)
        assert block.data["uri"] == "base64uri"

    def test_image_block_caption_list(self):
        """Image block with list of captions uses first."""
        from app.models.blocks import BlockType
        proc, _, _, _ = _make_processor()

        block = MagicMock()
        block.type = BlockType.IMAGE.value
        block.image_metadata = MagicMock()
        block.image_metadata.captions = ["img1", "img2"]
        block.data = None

        proc._map_base64_images_to_blocks([block], {"img1": "data1"}, 0)
        assert block.data == {"uri": "data1"}

    def test_image_block_no_matching_caption_warns(self):
        """Image block with non-matching caption logs warning."""
        from app.models.blocks import BlockType
        proc, _, _, _ = _make_processor()

        block = MagicMock()
        block.type = BlockType.IMAGE.value
        block.image_metadata = MagicMock()
        block.image_metadata.captions = "unknown_img"
        block.data = {}

        proc._map_base64_images_to_blocks([block], {"other_img": "data"}, 0)
        proc.logger.warning.assert_called()

    def test_image_block_data_not_dict_replaced(self):
        """Image block with non-dict data gets replaced."""
        from app.models.blocks import BlockType
        proc, _, _, _ = _make_processor()

        block = MagicMock()
        block.type = BlockType.IMAGE.value
        block.image_metadata = MagicMock()
        block.image_metadata.captions = "img"
        block.data = "some_string"

        proc._map_base64_images_to_blocks([block], {"img": "base64"}, 0)
        assert block.data == {"uri": "base64"}


# ===========================================================================
# Processor._process_single_blockgroup_through_docling (lines 1007-1051)
# ===========================================================================


class TestProcessSingleBlockgroupThroughDocling:
    """Tests for Processor._process_single_blockgroup_through_docling."""

    @pytest.mark.asyncio
    async def test_no_markdown_data_raises(self):
        """Block group with no data raises ValueError."""
        proc, _, _, _ = _make_processor()

        bg = MagicMock()
        bg.data = None
        bg.index = 0

        with pytest.raises(ValueError, match="no valid markdown data"):
            await proc._process_single_blockgroup_through_docling(
                bg, "test.md", MagicMock(), None
            )

    @pytest.mark.asyncio
    async def test_non_string_data_raises(self):
        """Block group with non-string data raises ValueError."""
        proc, _, _, _ = _make_processor()

        bg = MagicMock()
        bg.data = {"not": "a string"}
        bg.index = 0

        with pytest.raises(ValueError, match="no valid markdown data"):
            await proc._process_single_blockgroup_through_docling(
                bg, "test.md", MagicMock(), None
            )

    @pytest.mark.asyncio
    async def test_docling_returns_none_raises(self):
        """Empty result from docling raises ValueError."""
        proc, _, _, _ = _make_processor()
        proc._process_blockgroup_images = AsyncMock(return_value=("# Hello", {}))

        bg = MagicMock()
        bg.data = "# Hello World"
        bg.index = 0
        bg.name = "test_bg"

        mock_processor = AsyncMock()
        mock_processor.load_document = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="Docling returned empty result"):
            await proc._process_single_blockgroup_through_docling(
                bg, "test.md", mock_processor, None
            )

    @pytest.mark.asyncio
    async def test_success_with_md_parser(self):
        """Successful processing with markdown parser."""
        from app.models.blocks import Block, BlockGroup, BlocksContainer
        proc, _, _, _ = _make_processor()
        proc._process_blockgroup_images = AsyncMock(return_value=("# Hello", {}))
        proc._map_base64_images_to_blocks = MagicMock()

        bg = MagicMock()
        bg.data = "# Hello World"
        bg.index = 0
        bg.name = "test.md"

        result_container = MagicMock()
        result_container.blocks = [MagicMock()]
        result_container.block_groups = [MagicMock()]

        mock_processor = AsyncMock()
        mock_processor.load_document = AsyncMock(return_value=result_container)

        mock_md_parser = MagicMock()
        mock_md_parser.parse_string.return_value = b"# Hello"

        new_bgs, new_blocks = await proc._process_single_blockgroup_through_docling(
            bg, "test.md", mock_processor, mock_md_parser
        )

        assert len(new_blocks) == 1
        assert len(new_bgs) == 1

    @pytest.mark.asyncio
    async def test_success_without_md_parser(self):
        """Successful processing without markdown parser (encodes directly)."""
        proc, _, _, _ = _make_processor()
        proc._process_blockgroup_images = AsyncMock(return_value=("# Hello", {}))
        proc._map_base64_images_to_blocks = MagicMock()

        bg = MagicMock()
        bg.data = "# Hello World"
        bg.index = 0
        bg.name = None  # No name, should generate default

        result_container = MagicMock()
        result_container.blocks = []
        result_container.block_groups = []

        mock_processor = AsyncMock()
        mock_processor.load_document = AsyncMock(return_value=result_container)

        new_bgs, new_blocks = await proc._process_single_blockgroup_through_docling(
            bg, "test_record", mock_processor, None
        )

        assert new_blocks == []
        assert new_bgs == []


# ===========================================================================
# Processor._calculate_index_shift_map (lines 1053-1083)
# ===========================================================================


class TestCalculateIndexShiftMap:
    """Tests for Processor._calculate_index_shift_map."""

    def test_no_processing_results(self):
        """No processing results means zero shift for all."""
        proc, _, _, _ = _make_processor()

        bg0 = MagicMock(index=0)
        bg1 = MagicMock(index=1)

        result = proc._calculate_index_shift_map([bg0, bg1], {})
        assert result == {0: 0, 1: 0}

    def test_with_processing_results(self):
        """Processing results add cumulative shifts."""
        proc, _, _, _ = _make_processor()

        bg0 = MagicMock(index=0)
        bg1 = MagicMock(index=1)
        bg2 = MagicMock(index=2)

        # bg0 produced 2 new block groups, bg1 produced 1
        processing_results = {
            0: ([MagicMock(), MagicMock()], []),
            1: ([MagicMock()], []),
        }

        result = proc._calculate_index_shift_map([bg0, bg1, bg2], processing_results)
        assert result[0] == 0  # no shift for first
        assert result[1] == 2  # shifted by 2 from bg0
        assert result[2] == 3  # shifted by 2+1 from bg0+bg1


# ===========================================================================
# Processor._build_updated_blocks_container (lines 1114-1275)
# ===========================================================================


class TestBuildUpdatedBlocksContainer:
    """Tests for Processor._build_updated_blocks_container."""

    def test_empty_inputs(self):
        """Empty inputs produce empty container."""
        from app.models.blocks import BlocksContainer
        proc, _, _, _ = _make_processor()

        container = BlocksContainer(blocks=[], block_groups=[])
        result = proc._build_updated_blocks_container(
            container, [], [], {}, {}, 0
        )
        assert len(result.blocks) == 0
        assert len(result.block_groups) == 0

    def test_unprocessed_blockgroups_with_existing_blocks(self):
        """Unprocessed block groups reassign existing block indices."""
        from app.models.blocks import Block, BlockGroup, BlocksContainer, GroupType, DataFormat
        proc, _, _, _ = _make_processor()

        # Block with parent_index=0
        block = Block(index=0, type="text", format=DataFormat.TXT, data="hello", parent_index=0)
        bg = BlockGroup(index=0, type=GroupType.TABLE, requires_processing=False)
        bg.children = None

        container = BlocksContainer(blocks=[block], block_groups=[bg])

        index_shift_map = {0: 0}

        result = proc._build_updated_blocks_container(
            container, [bg], [], {}, index_shift_map, 1
        )

        assert len(result.blocks) == 1
        assert len(result.block_groups) == 1
        assert result.blocks[0].parent_index == 0

    def test_processed_blockgroups_get_new_blocks(self):
        """Processed block groups get new blocks from docling."""
        from app.models.blocks import Block, BlockGroup, BlocksContainer, GroupType, DataFormat
        proc, _, _, _ = _make_processor()

        bg = BlockGroup(index=0, type=GroupType.TABLE, requires_processing=True)
        bg.children = None

        # Processing result: 1 new block group, 1 new block
        new_bg = BlockGroup(index=0, type=GroupType.TABLE)
        new_bg.parent_index = None
        new_bg.children = None
        new_block = Block(index=0, type="text", format=DataFormat.TXT, data="processed", parent_index=None)

        container = BlocksContainer(blocks=[], block_groups=[bg])
        processing_results = {0: ([new_bg], [new_block])}
        index_shift_map = {0: 0}

        result = proc._build_updated_blocks_container(
            container, [bg], [], processing_results, index_shift_map, 0
        )

        assert len(result.blocks) == 1
        assert len(result.block_groups) == 2  # original + new

    def test_without_index_appended(self):
        """Block groups without index are appended at end."""
        from app.models.blocks import BlockGroup, BlocksContainer, GroupType
        proc, _, _, _ = _make_processor()

        # BlockGroup with index=None (pydantic allows None for Optional[int])
        bg_no_idx = BlockGroup(type=GroupType.TABLE)
        bg_no_idx.index = None
        bg_no_idx.children = None

        container = BlocksContainer(blocks=[], block_groups=[])

        result = proc._build_updated_blocks_container(
            container, [], [bg_no_idx], {}, {}, 0
        )

        assert len(result.block_groups) == 1
        assert result.block_groups[0].index is None


# ===========================================================================
# Processor._process_blockgroups_through_docling (lines 1302-1371)
# ===========================================================================


class TestProcessBlockgroupsThroughDocling:
    """Tests for Processor._process_blockgroups_through_docling."""

    @pytest.mark.asyncio
    async def test_empty_block_groups_returns_original(self):
        """No block groups => returns original container."""
        from app.models.blocks import BlocksContainer
        proc, _, _, _ = _make_processor()

        container = BlocksContainer(blocks=[], block_groups=[])
        result = await proc._process_blockgroups_through_docling(container, "test")
        assert result is container

    @pytest.mark.asyncio
    async def test_no_processing_needed_returns_original(self):
        """Block groups exist but none need processing."""
        from app.models.blocks import BlockGroup, BlocksContainer, GroupType
        proc, _, _, _ = _make_processor()

        bg = BlockGroup(index=0, type=GroupType.TABLE, requires_processing=False)
        container = BlocksContainer(blocks=[], block_groups=[bg])

        result = await proc._process_blockgroups_through_docling(container, "test")
        assert result is container

    @pytest.mark.asyncio
    async def test_processing_error_raises(self):
        """Error in blockgroup processing propagates."""
        from app.models.blocks import BlockGroup, BlocksContainer, GroupType
        proc, _, _, _ = _make_processor()

        bg = BlockGroup(index=0, type=GroupType.TABLE, requires_processing=True, data="# markdown")
        container = BlocksContainer(blocks=[], block_groups=[bg])

        proc._process_single_blockgroup_through_docling = AsyncMock(
            side_effect=RuntimeError("docling error")
        )

        with patch("app.events.processor.DoclingProcessor"):
            with pytest.raises(RuntimeError, match="docling error"):
                await proc._process_blockgroups_through_docling(container, "test")

    @pytest.mark.asyncio
    async def test_successful_processing(self):
        """Successful block group processing returns new container."""
        from app.models.blocks import Block, BlockGroup, BlocksContainer, GroupType, DataFormat
        proc, _, _, _ = _make_processor()
        from app.config.constants.arangodb import ExtensionTypes

        bg = BlockGroup(index=0, type=GroupType.TABLE, requires_processing=True, data="# markdown")
        container = BlocksContainer(blocks=[], block_groups=[bg])

        new_block = Block(index=0, type="text", format=DataFormat.TXT, data="result", parent_index=None)
        new_bg = BlockGroup(index=0, type=GroupType.TABLE)
        new_bg.parent_index = None
        new_bg.children = None

        proc._process_single_blockgroup_through_docling = AsyncMock(
            return_value=([new_bg], [new_block])
        )

        with patch("app.events.processor.DoclingProcessor"):
            result = await proc._process_blockgroups_through_docling(container, "test")

        assert len(result.blocks) >= 1
        assert len(result.block_groups) >= 1


# ===========================================================================
# Processor.process_pdf_document_with_ocr (lines 323-585)
# ===========================================================================


class TestProcessPdfDocumentWithOcr:
    """Tests for Processor.process_pdf_document_with_ocr."""

    @pytest.mark.asyncio
    async def test_vlm_ocr_provider(self):
        """VLM OCR provider processes pages correctly."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = _base_record_dict(mimeType="application/pdf")

        config.get_config = AsyncMock(return_value={
            "ocr": [{"provider": "VLM_OCR", "configuration": {}}],
            "llm": [],
        })

        with patch("app.events.processor.OCRHandler") as mock_handler_cls:
            mock_handler = AsyncMock()
            mock_handler.process_document.return_value = {
                "pages": [
                    {"page_number": 1, "markdown": "# Page 1 content"},
                ],
                "blocks": [],
                "tables": [],
            }
            mock_handler_cls.return_value = mock_handler

            with patch("app.events.processor.DoclingProcessor") as mock_docling:
                mock_dp = AsyncMock()
                mock_dp.parse_document.return_value = MagicMock()
                mock_dp.create_blocks.return_value = MagicMock(
                    blocks=[], block_groups=[]
                )
                mock_docling.return_value = mock_dp

                with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
                    mock_pipeline.return_value = AsyncMock()

                    events = await _collect(
                        proc.process_pdf_document_with_ocr(
                            recordName="test.pdf",
                            recordId="rec-1",
                            version=1,
                            source="upload",
                            orgId="org-1",
                            pdf_binary=b"pdf",
                            virtual_record_id="vr-1",
                        )
                    )

        assert any(e.event == "parsing_complete" for e in events)
        assert any(e.event == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_azure_di_provider(self):
        """Azure DI provider processes OCR correctly."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = _base_record_dict(mimeType="application/pdf")

        config.get_config = AsyncMock(return_value={
            "ocr": [{"provider": "AZURE_DI", "configuration": {"endpoint": "https://test.azure.com", "apiKey": "key123"}}],
            "llm": [],
        })

        with patch("app.events.processor.OCRHandler") as mock_handler_cls:
            mock_handler = AsyncMock()
            mock_handler.process_document.return_value = {
                "blocks": [],
                "tables": [],
            }
            mock_handler_cls.return_value = mock_handler

            with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
                mock_pipeline.return_value = AsyncMock()

                events = await _collect(
                    proc.process_pdf_document_with_ocr(
                        recordName="test.pdf",
                        recordId="rec-1",
                        version=1,
                        source="upload",
                        orgId="org-1",
                        pdf_binary=b"pdf",
                        virtual_record_id="vr-1",
                    )
                )

        assert any(e.event == "parsing_complete" for e in events)
        assert any(e.event == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_ocrmypdf_provider(self):
        """OCRmyPDF provider processes correctly."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = _base_record_dict(mimeType="application/pdf")

        config.get_config = AsyncMock(return_value={
            "ocr": [{"provider": "OCRMYPDF", "configuration": {}}],
            "llm": [],
        })

        with patch("app.events.processor.OCRHandler") as mock_handler_cls:
            mock_handler = AsyncMock()
            mock_handler.process_document.return_value = {
                "blocks": [],
                "tables": [],
            }
            mock_handler_cls.return_value = mock_handler

            with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
                mock_pipeline.return_value = AsyncMock()

                events = await _collect(
                    proc.process_pdf_document_with_ocr(
                        recordName="test.pdf",
                        recordId="rec-1",
                        version=1,
                        source="upload",
                        orgId="org-1",
                        pdf_binary=b"pdf",
                        virtual_record_id="vr-1",
                    )
                )

        assert any(e.event == "parsing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_no_handler_fallback_to_multimodal(self):
        """When no OCR handler configured, checks multimodal LLM fallback."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = _base_record_dict(mimeType="application/pdf")

        config.get_config = AsyncMock(return_value={
            "ocr": [],
            "llm": [{"provider": "openai", "isMultimodal": True, "configuration": {"model": "gpt-4o"}}],
        })

        with patch("app.events.processor.is_multimodal_llm", return_value=True):
            with patch("app.events.processor.OCRHandler") as mock_handler_cls:
                mock_handler = AsyncMock()
                mock_handler.process_document.return_value = {
                    "pages": [{"page_number": 1, "markdown": "# Content"}],
                    "blocks": [],
                    "tables": [],
                }
                mock_handler_cls.return_value = mock_handler

                with patch("app.events.processor.DoclingProcessor") as mock_docling:
                    mock_dp = AsyncMock()
                    mock_dp.parse_document.return_value = MagicMock()
                    mock_dp.create_blocks.return_value = MagicMock(blocks=[], block_groups=[])
                    mock_docling.return_value = mock_dp

                    with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
                        mock_pipeline.return_value = AsyncMock()

                        events = await _collect(
                            proc.process_pdf_document_with_ocr(
                                recordName="test.pdf",
                                recordId="rec-1",
                                version=1,
                                source="upload",
                                orgId="org-1",
                                pdf_binary=b"pdf",
                                virtual_record_id="vr-1",
                            )
                        )

        assert any(e.event == "parsing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_no_handler_fallback_to_ocrmypdf(self):
        """When no OCR config and no multimodal LLM, falls back to OCRmyPDF."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = _base_record_dict(mimeType="application/pdf")

        config.get_config = AsyncMock(return_value={
            "ocr": [],
            "llm": [],
        })

        with patch("app.events.processor.is_multimodal_llm", return_value=False):
            with patch("app.events.processor.OCRHandler") as mock_handler_cls:
                mock_handler = AsyncMock()
                mock_handler.process_document = AsyncMock(return_value={
                    "blocks": [],
                    "tables": [],
                })
                mock_handler_cls.return_value = mock_handler

                with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
                    mock_pipeline.return_value = AsyncMock()

                    events = await _collect(
                        proc.process_pdf_document_with_ocr(
                            recordName="test.pdf",
                            recordId="rec-1",
                            version=1,
                            source="upload",
                            orgId="org-1",
                            pdf_binary=b"pdf",
                            virtual_record_id="vr-1",
                        )
                    )

        assert any(e.event == "parsing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_non_vlm_with_empty_blocks(self):
        """Non-VLM OCR with empty blocks succeeds."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = _base_record_dict(mimeType="application/pdf")

        config.get_config = AsyncMock(return_value={
            "ocr": [{"provider": "OCRMYPDF", "configuration": {}}],
            "llm": [],
        })

        with patch("app.events.processor.OCRHandler") as mock_handler_cls:
            mock_handler = AsyncMock()
            mock_handler.process_document = AsyncMock(return_value={
                "blocks": [],
                "tables": [],
            })
            mock_handler_cls.return_value = mock_handler

            with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
                mock_pipeline.return_value = AsyncMock()

                events = await _collect(
                    proc.process_pdf_document_with_ocr(
                        recordName="test.pdf",
                        recordId="rec-1",
                        version=1,
                        source="upload",
                        orgId="org-1",
                        pdf_binary=b"pdf",
                        virtual_record_id="vr-1",
                    )
                )

        assert any(e.event == "parsing_complete" for e in events)
        assert any(e.event == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_vlm_ocr_with_pages_processes(self):
        """VLM OCR processes pages and yields correct events."""
        from app.models.blocks import BlocksContainer
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = _base_record_dict(mimeType="application/pdf")

        config.get_config = AsyncMock(return_value={
            "ocr": [{"provider": "VLM_OCR", "configuration": {}}],
            "llm": [],
        })

        with patch("app.events.processor.OCRHandler") as mock_handler_cls:
            mock_handler = AsyncMock()
            mock_handler.process_document = AsyncMock(return_value={
                "pages": [
                    {"page_number": 1, "markdown": "# Page 1 content"},
                ],
                "blocks": [],
                "tables": [],
            })
            mock_handler_cls.return_value = mock_handler

            with patch("app.events.processor.DoclingProcessor") as mock_docling:
                mock_dp = AsyncMock()
                mock_dp.parse_document = AsyncMock(return_value=MagicMock())
                mock_dp.create_blocks = AsyncMock(return_value=BlocksContainer(
                    blocks=[], block_groups=[]
                ))
                mock_docling.return_value = mock_dp

                with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
                    mock_pipeline.return_value = AsyncMock()

                    events = await _collect(
                        proc.process_pdf_document_with_ocr(
                            recordName="test.pdf",
                            recordId="rec-1",
                            version=1,
                            source="upload",
                            orgId="org-1",
                            pdf_binary=b"pdf",
                            virtual_record_id="vr-1",
                        )
                    )

        assert any(e.event == "parsing_complete" for e in events)
        assert any(e.event == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_record_not_found_yields_indexing_complete(self):
        """When record not found after OCR, yields indexing_complete."""
        proc, _, gp, config = _make_processor()
        gp.get_document.return_value = None

        config.get_config = AsyncMock(return_value={
            "ocr": [{"provider": "OCRMYPDF", "configuration": {}}],
            "llm": [],
        })

        with patch("app.events.processor.OCRHandler") as mock_handler_cls:
            mock_handler = AsyncMock()
            mock_handler.process_document.return_value = {
                "blocks": [],
                "tables": [],
            }
            mock_handler_cls.return_value = mock_handler

            events = await _collect(
                proc.process_pdf_document_with_ocr(
                    recordName="test.pdf",
                    recordId="rec-1",
                    version=1,
                    source="upload",
                    orgId="org-1",
                    pdf_binary=b"pdf",
                    virtual_record_id="vr-1",
                )
            )

        assert events[-1].event == "indexing_complete"

# =============================================================================
# Merged from test_processor_coverage.py
# =============================================================================

log = logging.getLogger("test")
log.setLevel(logging.CRITICAL)


def _make_processor_cov(**overrides):
    from app.events.processor import Processor
    kwargs = {
        "logger": log,
        "config_service": AsyncMock(),
        "indexing_pipeline": MagicMock(),
        "graph_provider": AsyncMock(),
        "parsers": {},
        "document_extractor": MagicMock(),
        "sink_orchestrator": MagicMock(),
    }
    kwargs.update(overrides)
    with patch("app.events.processor.DoclingClient"):
        proc = Processor(**kwargs)
    return proc


def _mock_record_dict(**overrides):
    base = {
        "_key": "r1",
        "orgId": "o1",
        "recordName": "test.md",
        "recordType": "FILE",
        "indexingStatus": "NOT_STARTED",
        "externalRecordId": "ext1",
        "connectorId": "c1",
        "mimeType": "text/plain",
        "createdAtTimestamp": 1000,
        "updatedAtTimestamp": 2000,
        "version": 1,
    }
    base.update(overrides)
    return base


async def _collect_events(async_gen):
    events = []
    async for ev in async_gen:
        events.append(ev)
    return events


# ===================================================================
# convert_record_dict_to_record
# ===================================================================


class TestConvertRecordDictToRecordCoverage:
    def test_basic_conversion(self):
        from app.events.processor import convert_record_dict_to_record
        record = convert_record_dict_to_record(_mock_record_dict())
        assert record.id == "r1"
        assert record.org_id == "o1"

    def test_unknown_connector_name(self):
        from app.events.processor import convert_record_dict_to_record
        record = convert_record_dict_to_record(
            _mock_record_dict(connectorName="unknown_connector_xyz")
        )
        # Should default to KNOWLEDGE_BASE
        assert record.connector_name is not None

    def test_no_connector_name(self):
        from app.events.processor import convert_record_dict_to_record
        d = _mock_record_dict()
        d.pop("connectorName", None)
        record = convert_record_dict_to_record(d)
        assert record.connector_name is not None

    def test_unknown_origin(self):
        from app.events.processor import convert_record_dict_to_record
        record = convert_record_dict_to_record(
            _mock_record_dict(origin="unknown_origin_xyz")
        )
        from app.config.constants.arangodb import OriginTypes
        assert record.origin == OriginTypes.UPLOAD

    def test_id_from_id_field(self):
        from app.events.processor import convert_record_dict_to_record
        d = _mock_record_dict()
        d.pop("_key")
        d["id"] = "id-from-id-field"
        record = convert_record_dict_to_record(d)
        assert record.id == "id-from-id-field"


# ===================================================================
# process_image
# ===================================================================


class TestProcessImageCoverage:
    @pytest.mark.asyncio
    async def test_no_content_raises(self):
        proc = _make_processor_cov()
        with pytest.raises(Exception, match="No image data"):
            async for _ in proc.process_image("r1", None, "vr1"):
                pass

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        proc = _make_processor_cov()
        proc.graph_provider.get_document = AsyncMock(return_value=None)

        events = await _collect_events(
            proc.process_image("r1", b"image_data", "vr1")
        )
        assert any(e.event == "parsing_complete" for e in events)
        assert any(e.event == "indexing_complete" for e in events)


# ===================================================================
# process_pdf_with_pymupdf
# ===================================================================


class TestProcessPdfWithPyMuPDFCoverage:
    @pytest.mark.asyncio
    async def test_record_not_found(self):
        proc = _make_processor_cov()

        with patch("app.events.processor.PyMuPDFOpenCVProcessor") as mock_proc:
            mock_instance = AsyncMock()
            mock_instance.parse_document = AsyncMock(return_value=MagicMock())
            mock_instance.create_blocks = AsyncMock(return_value=MagicMock())
            mock_proc.return_value = mock_instance

            proc.graph_provider.get_document = AsyncMock(return_value=None)

            events = await _collect_events(
                proc.process_pdf_with_pymupdf("test.pdf", "r1", b"pdf_data", "vr1")
            )
            assert any(e.event == "parsing_complete" for e in events)
            assert any(e.event == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_success(self):
        proc = _make_processor_cov()

        with patch("app.events.processor.PyMuPDFOpenCVProcessor") as mock_proc:
            mock_instance = AsyncMock()
            mock_instance.parse_document = AsyncMock(return_value=MagicMock())
            mock_instance.create_blocks = AsyncMock(return_value=MagicMock())
            mock_proc.return_value = mock_instance

            proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict())

            with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
                mock_pipeline.return_value.apply = AsyncMock()
                events = await _collect_events(
                    proc.process_pdf_with_pymupdf("test.pdf", "r1", b"pdf_data", "vr1")
                )

            assert any(e.event == "parsing_complete" for e in events)
            assert any(e.event == "indexing_complete" for e in events)


# ===================================================================
# process_pdf_with_docling
# ===================================================================


class TestProcessPdfWithDoclingCoverage:
    @pytest.mark.asyncio
    async def test_parse_failure(self):
        proc = _make_processor_cov()
        proc.docling_client = AsyncMock()
        proc.docling_client.parse_pdf = AsyncMock(return_value=None)

        events = await _collect_events(
            proc.process_pdf_with_docling("test.pdf", "r1", b"pdf", "vr1")
        )
        assert any(e.event == "docling_failed" for e in events)

    @pytest.mark.asyncio
    async def test_blocks_failure(self):
        proc = _make_processor_cov()
        proc.docling_client = AsyncMock()
        proc.docling_client.parse_pdf = AsyncMock(return_value=MagicMock())
        proc.docling_client.create_blocks = AsyncMock(return_value=None)

        with pytest.raises(Exception, match="failed to create blocks"):
            async for _ in proc.process_pdf_with_docling("test.pdf", "r1", b"pdf", "vr1"):
                pass

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        proc = _make_processor_cov()
        proc.docling_client = AsyncMock()
        proc.docling_client.parse_pdf = AsyncMock(return_value=MagicMock())
        proc.docling_client.create_blocks = AsyncMock(return_value=MagicMock())
        proc.graph_provider.get_document = AsyncMock(return_value=None)

        events = await _collect_events(
            proc.process_pdf_with_docling("test.pdf", "r1", b"pdf", "vr1")
        )
        assert any(e.event == "parsing_complete" for e in events)
        assert any(e.event == "indexing_complete" for e in events)


# ===================================================================
# process_docx_document
# ===================================================================


class TestProcessDocxDocumentCoverage:
    @pytest.mark.asyncio
    async def test_record_not_found(self):
        proc = _make_processor_cov()

        with patch("app.events.processor.DoclingProcessor") as mock_dp:
            mock_instance = AsyncMock()
            mock_instance.parse_document = AsyncMock(return_value=MagicMock())
            mock_instance.create_blocks = AsyncMock(return_value=MagicMock())
            mock_dp.return_value = mock_instance

            proc.graph_provider.get_document = AsyncMock(return_value=None)

            events = await _collect_events(
                proc.process_docx_document("test.docx", "r1", 1, "upload", "o1", b"docx", "vr1")
            )
            assert any(e.event == "parsing_complete" for e in events)
            assert any(e.event == "indexing_complete" for e in events)


# ===================================================================
# _enhance_tables_with_llm
# ===================================================================


class TestEnhanceTablesWithLLM:
    @pytest.mark.asyncio
    async def test_no_table_groups(self):
        """No TABLE block groups => returns early."""
        proc = _make_processor_cov()
        from app.models.blocks import BlocksContainer
        bc = BlocksContainer(blocks=[], block_groups=[])
        # Should not raise
        await proc._enhance_tables_with_llm(bc)

    @pytest.mark.asyncio
    async def test_table_group_no_markdown(self):
        """Table group without table_markdown in data => skipped."""
        proc = _make_processor_cov()
        from app.models.blocks import BlockGroup, BlocksContainer, GroupType
        bg = BlockGroup(index=0, type=GroupType.TABLE, data={})
        bc = BlocksContainer(blocks=[], block_groups=[bg])
        await proc._enhance_tables_with_llm(bc)

    @pytest.mark.asyncio
    async def test_table_group_no_llm_response(self):
        """get_table_summary_n_headers returns None."""
        proc = _make_processor_cov()
        from app.models.blocks import BlockGroup, BlocksContainer, GroupType
        bg = BlockGroup(index=0, type=GroupType.TABLE, data={"table_markdown": "| a | b |"})
        bc = BlocksContainer(blocks=[], block_groups=[bg])

        with patch("app.utils.indexing_helpers.get_table_summary_n_headers", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value = None
            await proc._enhance_tables_with_llm(bc)


# ===================================================================
# _separate_block_groups_by_index
# ===================================================================


class TestSeparateBlockGroupsByIndexCoverage:
    def test_separates_correctly(self):
        proc = _make_processor_cov()
        # Use MagicMock since BlockGroup.index doesn't allow None in pydantic
        bg1 = MagicMock(index=0)
        bg2 = MagicMock(index=None)
        bg3 = MagicMock(index=2)

        with_idx, without_idx = proc._separate_block_groups_by_index([bg1, bg2, bg3])
        assert len(with_idx) == 2
        assert len(without_idx) == 1


# ===================================================================
# _map_base64_images_to_blocks
# ===================================================================


class TestMapBase64ImagesToBlocksCoverage:
    def test_empty_caption_map(self):
        proc = _make_processor_cov()
        blocks = [MagicMock()]
        proc._map_base64_images_to_blocks(blocks, {}, 0)
        # Should be a no-op

    def test_image_block_with_matching_caption(self):
        from app.models.blocks import BlockType
        proc = _make_processor_cov()

        block = MagicMock()
        block.type = BlockType.IMAGE.value
        block.image_metadata = MagicMock()
        block.image_metadata.captions = "test_caption"
        block.data = {}

        caption_map = {"test_caption": "data:image/png;base64,abc123"}
        proc._map_base64_images_to_blocks([block], caption_map, 0)
        assert block.data["uri"] == "data:image/png;base64,abc123"

    def test_image_block_with_list_captions(self):
        from app.models.blocks import BlockType
        proc = _make_processor_cov()

        block = MagicMock()
        block.type = BlockType.IMAGE.value
        block.image_metadata = MagicMock()
        block.image_metadata.captions = ["test_caption"]
        block.data = None

        caption_map = {"test_caption": "data:image/png;base64,xyz"}
        proc._map_base64_images_to_blocks([block], caption_map, 0)
        assert block.data["uri"] == "data:image/png;base64,xyz"

    def test_image_block_caption_not_in_map(self):
        from app.models.blocks import BlockType
        proc = _make_processor_cov()

        block = MagicMock()
        block.type = BlockType.IMAGE.value
        block.image_metadata = MagicMock()
        block.image_metadata.captions = "missing_caption"
        block.data = {}

        caption_map = {"other_caption": "data:image/png;base64,abc"}
        proc._map_base64_images_to_blocks([block], caption_map, 0)
        assert "uri" not in block.data

    def test_image_block_data_is_non_dict_string(self):
        """When block.data is not a dict, creates new dict."""
        from app.models.blocks import BlockType
        proc = _make_processor_cov()

        block = MagicMock()
        block.type = BlockType.IMAGE.value
        block.image_metadata = MagicMock()
        block.image_metadata.captions = "cap"
        block.data = "some string"  # Not a dict

        caption_map = {"cap": "data:image/png;base64,xyz"}
        proc._map_base64_images_to_blocks([block], caption_map, 0)
        assert block.data == {"uri": "data:image/png;base64,xyz"}


# ===================================================================
# process_blocks
# ===================================================================


class TestProcessBlocksCoverage:
    @pytest.mark.asyncio
    async def test_bytes_input(self):
        proc = _make_processor_cov()
        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict())

        blocks_dict = {"blocks": [], "block_groups": []}
        blocks_bytes = bytes(str(blocks_dict).replace("'", '"'), "utf-8")

        with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
            mock_pipeline.return_value.apply = AsyncMock()
            with patch.object(proc, "_process_blockgroups_through_docling", new_callable=AsyncMock) as mock_pd:
                from app.models.blocks import BlocksContainer
                mock_pd.return_value = BlocksContainer(blocks=[], block_groups=[])
                with patch.object(proc, "_enhance_tables_with_llm", new_callable=AsyncMock):
                    events = await _collect_events(
                        proc.process_blocks("test", "r1", 1, "upload", "o1", blocks_bytes, "vr1")
                    )

        assert any(e.event == "parsing_complete" for e in events)
        assert any(e.event == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_dict_input(self):
        proc = _make_processor_cov()
        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict())

        blocks_dict = {"blocks": [], "block_groups": []}

        with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
            mock_pipeline.return_value.apply = AsyncMock()
            with patch.object(proc, "_process_blockgroups_through_docling", new_callable=AsyncMock) as mock_pd:
                from app.models.blocks import BlocksContainer
                mock_pd.return_value = BlocksContainer(blocks=[], block_groups=[])
                with patch.object(proc, "_enhance_tables_with_llm", new_callable=AsyncMock):
                    events = await _collect_events(
                        proc.process_blocks("test", "r1", 1, "upload", "o1", blocks_dict, "vr1")
                    )

        assert any(e.event == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_invalid_type_raises(self):
        proc = _make_processor_cov()
        with pytest.raises(ValueError, match="Invalid blocks_data type"):
            async for _ in proc.process_blocks("test", "r1", 1, "upload", "o1", 12345, "vr1"):
                pass

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        proc = _make_processor_cov()
        proc.graph_provider.get_document = AsyncMock(return_value=None)

        blocks_dict = {"blocks": [], "block_groups": []}

        with patch.object(proc, "_process_blockgroups_through_docling", new_callable=AsyncMock) as mock_pd:
            from app.models.blocks import BlocksContainer
            mock_pd.return_value = BlocksContainer(blocks=[], block_groups=[])
            with patch.object(proc, "_enhance_tables_with_llm", new_callable=AsyncMock):
                events = await _collect_events(
                    proc.process_blocks("test", "r1", 1, "upload", "o1", blocks_dict, "vr1")
                )

        assert any(e.event == "indexing_complete" for e in events)


# ===================================================================
# process_gmail_message
# ===================================================================


class TestProcessGmailMessageCoverage:
    @pytest.mark.asyncio
    async def test_delegates_to_html(self):
        proc = _make_processor_cov()

        async def _fake_html(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        proc.process_html_document = _fake_html

        events = await _collect_events(
            proc.process_gmail_message("msg", "r1", 1, "gmail", "o1", b"<p>Hello</p>", "vr1")
        )
        assert any(e.event == "parsing_complete" for e in events)
