"""Unit tests for app.events.processor — Processor document-processing methods.

Covers: process_html_document, process_md_document, process_txt_document,
process_excel_document, process_pptx_document, process_docx_document,
process_image, process_delimited_document.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

log = logging.getLogger("test")
log.setLevel(logging.CRITICAL)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_processor(**overrides):
    """Build a Processor with all dependencies mocked."""
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
    """Return a valid record dict that passes pydantic validation in convert_record_dict_to_record."""
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
    """Drain an async generator and return events as a list."""
    events = []
    async for ev in async_gen:
        events.append(ev)
    return events


# ============================================================================
# process_html_document
# ============================================================================

class TestProcessHtmlDocument:
    @pytest.mark.asyncio
    async def test_success(self):
        """HTML is cleaned, converted to markdown, and delegated to process_md_document."""
        proc = _make_processor()

        # Stub process_md_document to yield expected events
        async def _fake_md(*args, **kwargs):
            yield {"event": "parsing_complete", "data": {"record_id": "r1"}}
            yield {"event": "indexing_complete", "data": {"record_id": "r1"}}

        proc.process_md_document = _fake_md

        html_parser = MagicMock()
        html_parser.replace_relative_image_urls = MagicMock(side_effect=lambda x: x)
        proc.parsers = {"html": html_parser}

        html = b"<html><body><p>Hello</p></body></html>"
        events = await _collect_events(
            proc.process_html_document("test.html", "r1", "1", "web", "org1", html, "vr1")
        )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_bs4_parse_failure_falls_back(self):
        """If BeautifulSoup cleanup fails, raw HTML is still processed."""
        proc = _make_processor()

        async def _fake_md(*args, **kwargs):
            yield {"event": "parsing_complete", "data": {"record_id": "r1"}}
            yield {"event": "indexing_complete", "data": {"record_id": "r1"}}

        proc.process_md_document = _fake_md

        html_parser = MagicMock()
        html_parser.replace_relative_image_urls = MagicMock(side_effect=lambda x: x)
        proc.parsers = {"html": html_parser}

        # Pass bytes that will be decoded after BS4 failure
        html = b"<not really html>"

        with patch("app.events.processor.BeautifulSoup", side_effect=Exception("parse error")):
            events = await _collect_events(
                proc.process_html_document("test.html", "r1", "1", "web", "org1", html, "vr1")
            )

        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_exception_raised(self):
        """Processor should propagate exceptions from downstream processing."""
        proc = _make_processor()

        async def _fail_md(*args, **kwargs):
            raise RuntimeError("downstream failure")
            yield  # make it a generator  # noqa: E501 - unreachable but needed for generator

        proc.process_md_document = _fail_md

        html_parser = MagicMock()
        html_parser.replace_relative_image_urls = MagicMock(side_effect=lambda x: x)
        proc.parsers = {"html": html_parser}

        with pytest.raises(RuntimeError, match="downstream failure"):
            await _collect_events(
                proc.process_html_document("t.html", "r1", "1", "w", "o1", b"<p>Hi</p>", "vr1")
            )


# ============================================================================
# process_md_document (Markdown)
# ============================================================================

class TestProcessMdDocument:
    @pytest.mark.asyncio
    async def test_success(self):
        """Full markdown pipeline: parse, create blocks, indexing."""
        proc = _make_processor()

        md_parser = MagicMock()
        md_parser.extract_and_replace_images.return_value = ("# Hello", [])
        md_parser.parse_string.return_value = b"# Hello"
        proc.parsers = {"md": md_parser}

        mock_processor = AsyncMock()
        mock_conv_res = MagicMock()
        mock_processor.parse_document = AsyncMock(return_value=mock_conv_res)
        mock_blocks = MagicMock()
        mock_blocks.blocks = []
        mock_processor.create_blocks = AsyncMock(return_value=mock_blocks)

        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(recordName="test.md"))

        with patch("app.events.processor.DoclingProcessor", return_value=mock_processor), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock()
            events = await _collect_events(
                proc.process_md_document("test.md", "r1", b"# Hello", "vr1")
            )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_empty_content_marks_empty(self):
        """Empty/whitespace markdown marks the record as empty."""
        proc = _make_processor()
        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(recordName="empty.md"))
        proc.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)

        events = await _collect_events(
            proc.process_md_document("empty.md", "r1", b"   ", "vr1")
        )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        """When record is missing from DB, indexing_complete is still yielded."""
        proc = _make_processor()

        md_parser = MagicMock()
        md_parser.extract_and_replace_images.return_value = ("text", [])
        md_parser.parse_string.return_value = b"text"
        proc.parsers = {"md": md_parser}

        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(return_value=MagicMock())
        mock_blocks = MagicMock()
        mock_blocks.blocks = []
        mock_processor.create_blocks = AsyncMock(return_value=mock_blocks)

        proc.graph_provider.get_document = AsyncMock(return_value=None)

        with patch("app.events.processor.DoclingProcessor", return_value=mock_processor):
            events = await _collect_events(
                proc.process_md_document("test.md", "r1", b"text", "vr1")
            )

        assert any(e["event"] == "indexing_complete" for e in events)


# ============================================================================
# process_txt_document (Plain text)
# ============================================================================

class TestProcessTxtDocument:
    @pytest.mark.asyncio
    async def test_success(self):
        """Text is decoded and forwarded to process_md_document."""
        proc = _make_processor()

        async def _fake_md(*args, **kwargs):
            yield {"event": "parsing_complete", "data": {"record_id": "r1"}}
            yield {"event": "indexing_complete", "data": {"record_id": "r1"}}

        proc.process_md_document = _fake_md

        events = await _collect_events(
            proc.process_txt_document(
                "test.txt", "r1", "1", "src", "o1",
                b"Plain text content", "vr1", "FILE", "UPLOAD", "UPLOAD"
            )
        )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_undecipherable_encoding_raises(self):
        """Binary that can't be decoded with any encoding raises ValueError."""
        proc = _make_processor()

        # Create bytes that fail all encodings by mocking decode
        bad_binary = MagicMock()
        bad_binary.decode = MagicMock(side_effect=UnicodeDecodeError("codec", b"", 0, 1, "bad"))

        with pytest.raises(ValueError, match="Unable to decode"):
            await _collect_events(
                proc.process_txt_document(
                    "test.txt", "r1", "1", "src", "o1",
                    bad_binary, "vr1", "FILE", "UPLOAD", "UPLOAD"
                )
            )


# ============================================================================
# process_excel_document
# ============================================================================

class TestProcessExcelDocument:
    @pytest.mark.asyncio
    async def test_success(self):
        """Excel workbook is loaded and blocks are created."""
        excel_parser = MagicMock()
        excel_parser.load_workbook_from_binary = MagicMock()
        excel_parser.create_blocks = AsyncMock(return_value=MagicMock())
        proc = _make_processor(parsers={"xlsx": excel_parser})

        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(recordName="test.xlsx"))

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm, \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            mock_llm.return_value = (MagicMock(), {})
            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_excel_document("test.xlsx", "r1", "1", "src", "o1", b"xldata", "vr1")
            )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_empty_binary(self):
        """No binary content marks the record as empty."""
        excel_parser = MagicMock()
        proc = _make_processor(parsers={"xlsx": excel_parser})

        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(recordName="empty.xlsx"))
        proc.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {})

            events = await _collect_events(
                proc.process_excel_document("empty.xlsx", "r1", "1", "src", "o1", b"", "vr1")
            )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        """Missing record yields indexing_complete without crashing."""
        excel_parser = MagicMock()
        excel_parser.load_workbook_from_binary = MagicMock()
        excel_parser.create_blocks = AsyncMock(return_value=MagicMock())
        proc = _make_processor(parsers={"xlsx": excel_parser})

        proc.graph_provider.get_document = AsyncMock(return_value=None)

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {})

            events = await _collect_events(
                proc.process_excel_document("test.xlsx", "r1", "1", "s", "o1", b"data", "vr1")
            )

        assert any(e["event"] == "indexing_complete" for e in events)


# ============================================================================
# process_pptx_document
# ============================================================================

class TestProcessPptxDocument:
    @pytest.mark.asyncio
    async def test_success(self):
        """PPTX is parsed via DoclingProcessor and indexed."""
        proc = _make_processor()

        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(return_value=MagicMock())
        mock_blocks = MagicMock()
        mock_blocks.blocks = []
        mock_processor.create_blocks = AsyncMock(return_value=mock_blocks)

        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(recordName="test.pptx"))

        with patch("app.events.processor.DoclingProcessor", return_value=mock_processor), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_pptx_document("test.pptx", "r1", "1", "src", "o1", b"pptxdata", "vr1")
            )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        """Missing record yields indexing_complete only."""
        proc = _make_processor()

        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(return_value=MagicMock())
        mock_blocks = MagicMock()
        mock_blocks.blocks = []
        mock_processor.create_blocks = AsyncMock(return_value=mock_blocks)

        proc.graph_provider.get_document = AsyncMock(return_value=None)

        with patch("app.events.processor.DoclingProcessor", return_value=mock_processor):
            events = await _collect_events(
                proc.process_pptx_document("test.pptx", "r1", "1", "src", "o1", b"data", "vr1")
            )

        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_exception_propagated(self):
        """Parsing errors bubble up."""
        proc = _make_processor()

        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(side_effect=RuntimeError("corrupt pptx"))

        with patch("app.events.processor.DoclingProcessor", return_value=mock_processor):
            with pytest.raises(RuntimeError, match="corrupt pptx"):
                await _collect_events(
                    proc.process_pptx_document("test.pptx", "r1", "1", "s", "o1", b"bad", "vr1")
                )


# ============================================================================
# process_docx_document
# ============================================================================

class TestProcessDocxDocument:
    @pytest.mark.asyncio
    async def test_success(self):
        """DOCX is parsed via DoclingProcessor and indexed."""
        proc = _make_processor()

        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(return_value=MagicMock())
        mock_blocks = MagicMock()
        mock_blocks.blocks = []
        mock_processor.create_blocks = AsyncMock(return_value=mock_blocks)

        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(recordName="test.docx"))

        with patch("app.events.processor.DoclingProcessor", return_value=mock_processor), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_docx_document("test.docx", "r1", "1", "src", "o1", b"docxdata", "vr1")
            )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        """Missing record yields indexing_complete without crashing."""
        proc = _make_processor()

        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(return_value=MagicMock())
        mock_blocks = MagicMock()
        mock_blocks.blocks = []
        mock_processor.create_blocks = AsyncMock(return_value=mock_blocks)

        proc.graph_provider.get_document = AsyncMock(return_value=None)

        with patch("app.events.processor.DoclingProcessor", return_value=mock_processor):
            events = await _collect_events(
                proc.process_docx_document("test.docx", "r1", "1", "src", "o1", b"data", "vr1")
            )

        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_exception_propagated(self):
        """Errors during parsing bubble up."""
        proc = _make_processor()

        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(side_effect=RuntimeError("corrupt"))

        with patch("app.events.processor.DoclingProcessor", return_value=mock_processor):
            with pytest.raises(RuntimeError, match="corrupt"):
                await _collect_events(
                    proc.process_docx_document("t.docx", "r1", "1", "s", "o1", b"bad", "vr1")
                )


# ============================================================================
# process_image
# ============================================================================

class TestProcessImage:
    @pytest.mark.asyncio
    async def test_success(self):
        """Image with a multimodal LLM is parsed and indexed."""
        image_parser = MagicMock()
        image_parser.parse_image = MagicMock(return_value=MagicMock())
        proc = _make_processor(parsers={"png": image_parser})

        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(
            recordName="photo.png", mimeType="image/png",
        ))

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm, \
             patch("app.events.processor.get_embedding_model_config", new_callable=AsyncMock) as mock_emb, \
             patch("app.events.processor.get_extension_from_mimetype", return_value="png"), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            mock_llm.return_value = (MagicMock(), {"isMultimodal": True})
            mock_emb.return_value = {"isMultimodal": False}
            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_image("r1", b"imgdata", "vr1")
            )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_non_multimodal_llm(self):
        """Without multimodal capability, the record status is updated and events are yielded."""
        proc = _make_processor()

        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(
            recordName="photo.png", mimeType="image/png",
        ))
        proc.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm, \
             patch("app.events.processor.get_embedding_model_config", new_callable=AsyncMock) as mock_emb:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            mock_emb.return_value = {"isMultimodal": False}

            events = await _collect_events(
                proc.process_image("r1", b"imgdata", "vr1")
            )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_no_content_raises(self):
        """No image data raises an exception."""
        proc = _make_processor()

        with pytest.raises(Exception, match="No image data"):
            await _collect_events(proc.process_image("r1", None, "vr1"))

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        """Missing record yields both events and returns."""
        proc = _make_processor()
        proc.graph_provider.get_document = AsyncMock(return_value=None)

        events = await _collect_events(proc.process_image("r1", b"data", "vr1"))

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)


# ============================================================================
# process_delimited_document (CSV/TSV)
# ============================================================================

class TestProcessDelimitedDocument:
    @pytest.mark.asyncio
    async def test_success_csv(self):
        """CSV is decoded, parsed into rows, and indexed."""
        csv_parser = MagicMock()
        csv_parser.read_raw_rows.return_value = [["a", "b"], ["1", "2"]]
        csv_parser.find_tables_in_csv.return_value = [
            {"rows": [["a", "b"], ["1", "2"]], "headers": ["a", "b"]}
        ]
        csv_parser.get_blocks_from_csv_with_multiple_tables = AsyncMock(return_value=MagicMock())
        proc = _make_processor(parsers={"csv": csv_parser})

        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(recordName="test.csv"))

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm, \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            mock_llm.return_value = (MagicMock(), {})
            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_delimited_document("test.csv", "r1", b"a,b\n1,2", "vr1")
            )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_empty_csv_marks_empty(self):
        """Empty/undecipherable CSV marks the record as EMPTY."""
        csv_parser = MagicMock()
        csv_parser.read_raw_rows.return_value = []
        proc = _make_processor(parsers={"csv": csv_parser})

        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(recordName="empty.csv"))
        proc.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {})

            events = await _collect_events(
                proc.process_delimited_document("empty.csv", "r1", b"", "vr1")
            )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_csv_record_not_found(self):
        """Missing record yields both events."""
        csv_parser = MagicMock()
        csv_parser.read_raw_rows.return_value = [["a", "b"], ["1", "2"]]
        csv_parser.find_tables_in_csv.return_value = [
            {"rows": [["a", "b"], ["1", "2"]], "headers": ["a", "b"]}
        ]
        proc = _make_processor(parsers={"csv": csv_parser})

        proc.graph_provider.get_document = AsyncMock(return_value=None)

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {})

            events = await _collect_events(
                proc.process_delimited_document("test.csv", "r1", b"a,b\n1,2", "vr1")
            )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_tsv_extension(self):
        """TSV extension uses the correct parser key."""
        tsv_parser = MagicMock()
        tsv_parser.read_raw_rows.return_value = [["a", "b"], ["1", "2"]]
        tsv_parser.find_tables_in_csv.return_value = [
            {"rows": [["a", "b"], ["1", "2"]], "headers": ["a", "b"]}
        ]
        tsv_parser.get_blocks_from_csv_with_multiple_tables = AsyncMock(return_value=MagicMock())
        proc = _make_processor(parsers={"tsv": tsv_parser})

        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(recordName="test.tsv"))

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm, \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            mock_llm.return_value = (MagicMock(), {})
            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_delimited_document("test.tsv", "r1", b"a\tb\n1\t2", "vr1", extension="tsv")
            )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)


# ============================================================================
# process_pdf_with_pymupdf
# ============================================================================

class TestProcessPdfWithPymupdf:
    @pytest.mark.asyncio
    async def test_success(self):
        """PyMuPDF PDF processing: parse, create blocks, index."""
        proc = _make_processor()

        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(return_value=MagicMock())
        mock_blocks = MagicMock()
        mock_blocks.blocks = []
        mock_processor.create_blocks = AsyncMock(return_value=mock_blocks)

        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(recordName="test.pdf"))

        with patch("app.events.processor.PyMuPDFOpenCVProcessor", return_value=mock_processor), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_pdf_with_pymupdf("test.pdf", "r1", b"pdfdata", "vr1")
            )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        """Missing record yields indexing_complete."""
        proc = _make_processor()

        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(return_value=MagicMock())
        mock_processor.create_blocks = AsyncMock(return_value=MagicMock())

        proc.graph_provider.get_document = AsyncMock(return_value=None)

        with patch("app.events.processor.PyMuPDFOpenCVProcessor", return_value=mock_processor):
            events = await _collect_events(
                proc.process_pdf_with_pymupdf("test.pdf", "r1", b"pdfdata", "vr1")
            )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_exception_propagated(self):
        """Errors during PDF processing propagate."""
        proc = _make_processor()

        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(side_effect=RuntimeError("bad pdf"))

        with patch("app.events.processor.PyMuPDFOpenCVProcessor", return_value=mock_processor):
            with pytest.raises(RuntimeError, match="bad pdf"):
                await _collect_events(
                    proc.process_pdf_with_pymupdf("test.pdf", "r1", b"bad", "vr1")
                )

    @pytest.mark.asyncio
    async def test_appends_pdf_extension(self):
        """If record name doesn't end in .pdf, it gets appended."""
        proc = _make_processor()

        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(return_value=MagicMock())
        mock_processor.create_blocks = AsyncMock(return_value=MagicMock())
        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(recordName="report"))

        with patch("app.events.processor.PyMuPDFOpenCVProcessor", return_value=mock_processor) as MockPyMu, \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock()

            await _collect_events(
                proc.process_pdf_with_pymupdf("report", "r1", b"pdfdata", "vr1")
            )

        # parse_document should receive the name with .pdf appended
        call_args = mock_processor.parse_document.call_args
        assert call_args[0][0] == "report.pdf"


# ============================================================================
# process_pdf_with_docling
# ============================================================================

class TestProcessPdfWithDocling:
    @pytest.mark.asyncio
    async def test_success(self):
        """Docling PDF processing: parse, create blocks, index."""
        proc = _make_processor()

        proc.docling_client.parse_pdf = AsyncMock(return_value=MagicMock())
        proc.docling_client.create_blocks = AsyncMock(return_value=MagicMock())
        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(recordName="test.pdf"))

        with patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_pdf_with_docling("test.pdf", "r1", b"pdfdata", "vr1")
            )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_parse_returns_none(self):
        """When docling parse_pdf returns None, yields docling_failed."""
        proc = _make_processor()
        proc.docling_client.parse_pdf = AsyncMock(return_value=None)

        events = await _collect_events(
            proc.process_pdf_with_docling("test.pdf", "r1", b"pdfdata", "vr1")
        )

        assert any(e["event"] == "docling_failed" for e in events)

    @pytest.mark.asyncio
    async def test_create_blocks_returns_none_raises(self):
        """When docling create_blocks returns None, an exception is raised."""
        proc = _make_processor()
        proc.docling_client.parse_pdf = AsyncMock(return_value=MagicMock())
        proc.docling_client.create_blocks = AsyncMock(return_value=None)

        with pytest.raises(Exception, match="failed to create blocks"):
            await _collect_events(
                proc.process_pdf_with_docling("test.pdf", "r1", b"data", "vr1")
            )

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        """Missing record yields indexing_complete."""
        proc = _make_processor()
        proc.docling_client.parse_pdf = AsyncMock(return_value=MagicMock())
        proc.docling_client.create_blocks = AsyncMock(return_value=MagicMock())
        proc.graph_provider.get_document = AsyncMock(return_value=None)

        events = await _collect_events(
            proc.process_pdf_with_docling("test.pdf", "r1", b"data", "vr1")
        )

        assert any(e["event"] == "indexing_complete" for e in events)


# ============================================================================
# process_doc_document (DOC -> DOCX delegation)
# ============================================================================

class TestProcessDocDocument:
    @pytest.mark.asyncio
    async def test_success(self):
        """DOC is converted to DOCX and delegated to process_docx_document."""
        doc_parser = MagicMock()
        doc_parser.convert_doc_to_docx.return_value = b"docx_binary"
        proc = _make_processor(parsers={"doc": doc_parser})

        async def _fake_docx(*args, **kwargs):
            yield {"event": "parsing_complete", "data": {"record_id": "r1"}}
            yield {"event": "indexing_complete", "data": {"record_id": "r1"}}

        proc.process_docx_document = _fake_docx

        events = await _collect_events(
            proc.process_doc_document("test.doc", "r1", "1", "src", "o1", b"docdata", "vr1")
        )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)
        doc_parser.convert_doc_to_docx.assert_called_once_with(b"docdata")


# ============================================================================
# process_xls_document (XLS -> XLSX delegation)
# ============================================================================

class TestProcessXlsDocument:
    @pytest.mark.asyncio
    async def test_success(self):
        """XLS is converted to XLSX and delegated to process_excel_document."""
        xls_parser = MagicMock()
        xls_parser.convert_xls_to_xlsx.return_value = b"xlsx_binary"
        proc = _make_processor(parsers={"xls": xls_parser})

        async def _fake_excel(*args, **kwargs):
            yield {"event": "parsing_complete", "data": {"record_id": "r1"}}
            yield {"event": "indexing_complete", "data": {"record_id": "r1"}}

        proc.process_excel_document = _fake_excel

        events = await _collect_events(
            proc.process_xls_document("test.xls", "r1", "1", "src", "o1", b"xlsdata", "vr1")
        )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)
        xls_parser.convert_xls_to_xlsx.assert_called_once_with(b"xlsdata")

    @pytest.mark.asyncio
    async def test_exception_propagated(self):
        """XLS conversion failure propagates."""
        xls_parser = MagicMock()
        xls_parser.convert_xls_to_xlsx.side_effect = RuntimeError("bad xls")
        proc = _make_processor(parsers={"xls": xls_parser})

        with pytest.raises(RuntimeError, match="bad xls"):
            await _collect_events(
                proc.process_xls_document("t.xls", "r1", "1", "s", "o1", b"bad", "vr1")
            )


# ============================================================================
# process_gmail_message
# ============================================================================

class TestProcessGmailMessage:
    @pytest.mark.asyncio
    async def test_success(self):
        """Gmail message delegates to process_html_document."""
        proc = _make_processor()

        async def _fake_html(*args, **kwargs):
            yield {"event": "parsing_complete", "data": {"record_id": "r1"}}
            yield {"event": "indexing_complete", "data": {"record_id": "r1"}}

        proc.process_html_document = _fake_html

        events = await _collect_events(
            proc.process_gmail_message("msg.html", "r1", "1", "gmail", "o1", b"<p>Hi</p>", "vr1")
        )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_exception_propagated(self):
        """Gmail processing errors propagate."""
        proc = _make_processor()

        async def _fail_html(*args, **kwargs):
            raise RuntimeError("html fail")
            yield  # noqa: E501 - needed for generator

        proc.process_html_document = _fail_html

        with pytest.raises(RuntimeError, match="html fail"):
            await _collect_events(
                proc.process_gmail_message("m.html", "r1", "1", "g", "o1", b"<p>X</p>", "vr1")
            )


# ============================================================================
# process_mdx_document
# ============================================================================

class TestProcessMdxDocument:
    @pytest.mark.asyncio
    async def test_success(self):
        """MDX is converted to MD and delegated to process_md_document."""
        mdx_parser = MagicMock()
        mdx_parser.convert_mdx_to_md.return_value = "# Hello from MDX"
        proc = _make_processor(parsers={"mdx": mdx_parser})

        async def _fake_md(*args, **kwargs):
            yield {"event": "parsing_complete", "data": {"record_id": "r1"}}
            yield {"event": "indexing_complete", "data": {"record_id": "r1"}}

        proc.process_md_document = _fake_md

        events = await _collect_events(
            proc.process_mdx_document("test.mdx", "r1", "1", "src", "o1", "# MDX content", "vr1")
        )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)
        mdx_parser.convert_mdx_to_md.assert_called_once_with("# MDX content")


# ============================================================================
# process_blocks
# ============================================================================

class TestProcessBlocks:
    @pytest.mark.asyncio
    async def test_success_dict_input(self):
        """Blocks from a dict are processed."""
        proc = _make_processor()

        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(recordName="test"))

        blocks_dict = {"blocks": [], "block_groups": []}

        with patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_blocks("test", "r1", "1", "src", "o1", blocks_dict, "vr1")
            )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_success_bytes_input(self):
        """Blocks from bytes JSON are processed."""
        import json
        proc = _make_processor()

        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(recordName="test"))

        blocks_bytes = json.dumps({"blocks": [], "block_groups": []}).encode("utf-8")

        with patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_blocks("test", "r1", "1", "src", "o1", blocks_bytes, "vr1")
            )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_success_string_input(self):
        """Blocks from a JSON string are processed."""
        import json
        proc = _make_processor()

        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(recordName="test"))

        blocks_str = json.dumps({"blocks": [], "block_groups": []})

        with patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_blocks("test", "r1", "1", "src", "o1", blocks_str, "vr1")
            )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_invalid_type_raises(self):
        """Non-dict, non-str, non-bytes input raises ValueError."""
        proc = _make_processor()

        with pytest.raises(ValueError, match="Invalid blocks_data type"):
            await _collect_events(
                proc.process_blocks("test", "r1", "1", "src", "o1", 12345, "vr1")
            )

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        """Missing record yields indexing_complete."""
        proc = _make_processor()
        proc.graph_provider.get_document = AsyncMock(return_value=None)

        blocks_dict = {"blocks": [], "block_groups": []}

        events = await _collect_events(
            proc.process_blocks("test", "r1", "1", "src", "o1", blocks_dict, "vr1")
        )

        assert any(e["event"] == "indexing_complete" for e in events)


# ============================================================================
# _mark_record
# ============================================================================

class TestMarkRecord:
    @pytest.mark.asyncio
    async def test_success(self):
        """_mark_record updates the record's indexing status."""
        from app.config.constants.arangodb import ProgressStatus
        proc = _make_processor()

        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict())
        proc.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)

        with patch("app.events.processor.get_epoch_timestamp_in_ms", return_value=12345):
            await proc._mark_record("r1", ProgressStatus.EMPTY)

        proc.graph_provider.batch_upsert_nodes.assert_called_once()
        call_args = proc.graph_provider.batch_upsert_nodes.call_args[0]
        doc = call_args[0][0]
        assert doc["indexingStatus"] == "EMPTY"
        assert doc["isDirty"] is False

    @pytest.mark.asyncio
    async def test_record_not_found_raises(self):
        """_mark_record raises when record is missing."""
        from app.config.constants.arangodb import ProgressStatus
        from app.exceptions.indexing_exceptions import DocumentProcessingError
        proc = _make_processor()

        proc.graph_provider.get_document = AsyncMock(return_value=None)

        with pytest.raises(DocumentProcessingError, match="Record not found"):
            await proc._mark_record("r1", ProgressStatus.EMPTY)

    @pytest.mark.asyncio
    async def test_upsert_failure_raises(self):
        """_mark_record raises when upsert returns False."""
        from app.config.constants.arangodb import ProgressStatus
        from app.exceptions.indexing_exceptions import DocumentProcessingError
        proc = _make_processor()

        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict())
        proc.graph_provider.batch_upsert_nodes = AsyncMock(return_value=False)

        with patch("app.events.processor.get_epoch_timestamp_in_ms", return_value=12345):
            with pytest.raises(DocumentProcessingError, match="Failed to update"):
                await proc._mark_record("r1", ProgressStatus.EMPTY)


# ============================================================================
# convert_record_dict_to_record
# ============================================================================

class TestConvertRecordDictToRecord:
    def test_valid_record(self):
        """A valid record dict produces a Record with correct fields."""
        from app.events.processor import convert_record_dict_to_record
        rec = convert_record_dict_to_record(_mock_record_dict())
        assert rec.id == "r1"
        assert rec.org_id == "o1"
        assert rec.record_name == "test.md"

    def test_unknown_connector_defaults(self):
        """Unknown connectorName defaults to KNOWLEDGE_BASE."""
        from app.events.processor import convert_record_dict_to_record
        from app.config.constants.arangodb import Connectors
        rec = convert_record_dict_to_record(_mock_record_dict(connectorName="UNKNOWN_XYZ"))
        assert rec.connector_name == Connectors.KNOWLEDGE_BASE

    def test_missing_connector_defaults(self):
        """Missing connectorName defaults to KNOWLEDGE_BASE."""
        from app.events.processor import convert_record_dict_to_record
        from app.config.constants.arangodb import Connectors
        d = _mock_record_dict()
        d.pop("connectorName", None)
        rec = convert_record_dict_to_record(d)
        assert rec.connector_name == Connectors.KNOWLEDGE_BASE

    def test_unknown_origin_defaults(self):
        """Unknown origin defaults to UPLOAD."""
        from app.events.processor import convert_record_dict_to_record
        from app.config.constants.arangodb import OriginTypes
        rec = convert_record_dict_to_record(_mock_record_dict(origin="BOGUS"))
        assert rec.origin == OriginTypes.UPLOAD

    def test_uses_id_fallback(self):
        """If _key is missing, id field is used."""
        from app.events.processor import convert_record_dict_to_record
        d = _mock_record_dict()
        del d["_key"]
        d["id"] = "id_fallback"
        rec = convert_record_dict_to_record(d)
        assert rec.id == "id_fallback"


# ============================================================================
# _separate_block_groups_by_index
# ============================================================================

class TestSeparateBlockGroupsByIndex:
    def test_separation(self):
        """Block groups are separated by whether index is None."""
        from app.models.blocks import BlockGroup
        proc = _make_processor()

        bg_with = MagicMock(spec=BlockGroup)
        bg_with.index = 0
        bg_without = MagicMock(spec=BlockGroup)
        bg_without.index = None

        with_idx, without_idx = proc._separate_block_groups_by_index([bg_with, bg_without])
        assert len(with_idx) == 1
        assert len(without_idx) == 1
        assert with_idx[0].index == 0
        assert without_idx[0].index is None

    def test_all_have_index(self):
        """When all block groups have indices, the second list is empty."""
        from app.models.blocks import BlockGroup
        proc = _make_processor()

        bg1 = MagicMock(spec=BlockGroup)
        bg1.index = 0
        bg2 = MagicMock(spec=BlockGroup)
        bg2.index = 1

        with_idx, without_idx = proc._separate_block_groups_by_index([bg1, bg2])
        assert len(with_idx) == 2
        assert len(without_idx) == 0


# ============================================================================
# _enhance_tables_with_llm
# ============================================================================

class TestEnhanceTablesWithLlm:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_table_without_markdown_skips(self):
        """TABLE group without table_markdown is skipped gracefully."""
        from app.models.blocks import BlocksContainer, GroupType
        proc = _make_processor()

        table_bg = MagicMock()
        table_bg.type = GroupType.TABLE
        table_bg.index = 0
        table_bg.data = None

        bc = MagicMock(spec=BlocksContainer)
        bc.block_groups = [table_bg]

        await proc._enhance_tables_with_llm(bc)
        # No error raised


# ============================================================================
# process_pdf_document_with_ocr
# ============================================================================

class TestProcessPdfDocumentWithOcr:
    @pytest.mark.asyncio
    async def test_vlm_ocr_success(self):
        """VLM OCR processes pages through DoclingProcessor and indexes."""
        proc = _make_processor()

        mock_handler = AsyncMock()
        mock_handler.process_document = AsyncMock(return_value={
            "pages": [
                {"page_number": 1, "markdown": "# Page 1 content"},
            ],
        })

        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(return_value=MagicMock())
        mock_blocks = MagicMock()
        mock_blocks.blocks = []
        mock_blocks.block_groups = []
        mock_processor.create_blocks = AsyncMock(return_value=mock_blocks)

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.pdf")
        )

        with patch("app.events.processor.OCRHandler", return_value=mock_handler), \
             patch("app.events.processor.DoclingProcessor", return_value=mock_processor), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock()
            proc.config_service.get_config = AsyncMock(return_value={
                "ocr": [{"provider": "vlmOCR"}],
                "llm": [],
            })

            events = await _collect_events(
                proc.process_pdf_document_with_ocr(
                    "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                )
            )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_vlm_ocr_empty_page_skipped(self):
        """Empty pages from VLM OCR are skipped."""
        proc = _make_processor()

        mock_handler = AsyncMock()
        mock_handler.process_document = AsyncMock(return_value={
            "pages": [
                {"page_number": 1, "markdown": ""},
                {"page_number": 2, "markdown": "# Content"},
            ],
        })

        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(return_value=MagicMock())
        mock_blocks = MagicMock()
        mock_blocks.blocks = []
        mock_blocks.block_groups = []
        mock_processor.create_blocks = AsyncMock(return_value=mock_blocks)

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.pdf")
        )

        with patch("app.events.processor.OCRHandler", return_value=mock_handler), \
             patch("app.events.processor.DoclingProcessor", return_value=mock_processor), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock()
            proc.config_service.get_config = AsyncMock(return_value={
                "ocr": [{"provider": "vlmOCR"}],
                "llm": [],
            })

            events = await _collect_events(
                proc.process_pdf_document_with_ocr(
                    "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                )
            )

        # Empty page 1 was skipped; only page 2 processed
        # parse_document called once for the non-empty page
        assert mock_processor.parse_document.await_count == 1
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_vlm_ocr_record_not_found(self):
        """Missing record after VLM OCR yields indexing_complete."""
        proc = _make_processor()

        mock_handler = AsyncMock()
        mock_handler.process_document = AsyncMock(return_value={
            "pages": [{"page_number": 1, "markdown": "# Content"}],
        })

        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(return_value=MagicMock())
        mock_blocks = MagicMock()
        mock_blocks.blocks = []
        mock_blocks.block_groups = []
        mock_processor.create_blocks = AsyncMock(return_value=mock_blocks)

        proc.graph_provider.get_document = AsyncMock(return_value=None)

        with patch("app.events.processor.OCRHandler", return_value=mock_handler), \
             patch("app.events.processor.DoclingProcessor", return_value=mock_processor):
            proc.config_service.get_config = AsyncMock(return_value={
                "ocr": [{"provider": "vlmOCR"}],
                "llm": [],
            })

            events = await _collect_events(
                proc.process_pdf_document_with_ocr(
                    "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                )
            )

        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_azure_ocr_fallback_to_ocrmypdf(self):
        """When Azure OCR fails, falls back to OCRmyPDF."""
        proc = _make_processor()

        call_count = 0
        async def _process_doc_side_effect(pdf_binary):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Azure failed")
            return {"blocks": [], "tables": []}

        mock_handler = AsyncMock()
        mock_handler.process_document = AsyncMock(side_effect=_process_doc_side_effect)

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.pdf")
        )

        with patch("app.events.processor.OCRHandler") as MockOCRHandler, \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            # First handler (Azure) fails, second (OCRmyPDF) succeeds
            azure_handler = AsyncMock()
            azure_handler.process_document = AsyncMock(
                side_effect=RuntimeError("Azure failed")
            )
            ocrmypdf_handler = AsyncMock()
            ocrmypdf_handler.process_document = AsyncMock(
                return_value={"blocks": [], "tables": []}
            )
            MockOCRHandler.side_effect = [azure_handler, ocrmypdf_handler]

            MockPipeline.return_value.apply = AsyncMock()
            proc.config_service.get_config = AsyncMock(return_value={
                "ocr": [
                    {"provider": "azureDI", "configuration": {"endpoint": "https://e", "apiKey": "k"}},
                ],
                "llm": [],
            })

            events = await _collect_events(
                proc.process_pdf_document_with_ocr(
                    "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                )
            )

        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_non_vlm_ocr_with_blocks(self):
        """Non-VLM OCR path with blocks creates BlocksContainer."""
        from app.models.blocks import Block, BlockType, DataFormat

        proc = _make_processor()

        mock_handler = AsyncMock()
        mock_block = Block(
            index=0,
            type=BlockType.TEXT,
            format=DataFormat.TXT,
            data="Hello",
            comments=[],
        )
        mock_handler.process_document = AsyncMock(return_value={
            "blocks": [mock_block],
            "tables": [],
        })

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.pdf")
        )

        with patch("app.events.processor.OCRHandler", return_value=mock_handler), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock()
            proc.config_service.get_config = AsyncMock(return_value={
                "ocr": [{"provider": "ocrmypdf"}],
                "llm": [],
            })

            events = await _collect_events(
                proc.process_pdf_document_with_ocr(
                    "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                )
            )

        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_no_handler_multimodal_llm_detected(self):
        """When no OCR handler is configured but multimodal LLM exists, uses VLM_OCR."""
        proc = _make_processor()

        mock_handler = AsyncMock()
        mock_handler.process_document = AsyncMock(return_value={
            "pages": [{"page_number": 1, "markdown": "# Content"}],
        })

        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(return_value=MagicMock())
        mock_blocks = MagicMock()
        mock_blocks.blocks = []
        mock_blocks.block_groups = []
        mock_processor.create_blocks = AsyncMock(return_value=mock_blocks)

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.pdf")
        )

        with patch("app.events.processor.OCRHandler", return_value=mock_handler), \
             patch("app.events.processor.DoclingProcessor", return_value=mock_processor), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"), \
             patch("app.events.processor.is_multimodal_llm", return_value=True):
            MockPipeline.return_value.apply = AsyncMock()
            proc.config_service.get_config = AsyncMock(return_value={
                "ocr": [],  # No OCR handlers configured
                "llm": [{"provider": "openai", "model": "gpt-4o"}],
            })

            events = await _collect_events(
                proc.process_pdf_document_with_ocr(
                    "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                )
            )

        assert any(e["event"] == "indexing_complete" for e in events)


# ============================================================================
# _run_indexing_pipeline (via process_md_document internals)
# ============================================================================

class TestRunIndexingPipeline:
    @pytest.mark.asyncio
    async def test_pipeline_error_propagates(self):
        """Errors from IndexingPipeline.apply propagate upward."""
        proc = _make_processor()

        md_parser = MagicMock()
        md_parser.extract_and_replace_images.return_value = ("# Hello", [])
        md_parser.parse_string.return_value = b"# Hello"
        proc.parsers = {"md": md_parser}

        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(return_value=MagicMock())
        mock_blocks = MagicMock()
        mock_blocks.blocks = []
        mock_processor.create_blocks = AsyncMock(return_value=mock_blocks)

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.md")
        )

        with patch("app.events.processor.DoclingProcessor", return_value=mock_processor), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock(
                side_effect=RuntimeError("pipeline error")
            )

            with pytest.raises(RuntimeError, match="pipeline error"):
                await _collect_events(
                    proc.process_md_document("test.md", "r1", b"# Hello", "vr1")
                )


# ============================================================================
# process_pdf_with_docling — additional branches
# ============================================================================

class TestProcessPdfWithDoclingAdditional:
    @pytest.mark.asyncio
    async def test_exception_in_pipeline(self):
        """Exception during pipeline processing propagates."""
        proc = _make_processor()
        proc.docling_client.parse_pdf = AsyncMock(return_value=MagicMock())
        proc.docling_client.create_blocks = AsyncMock(return_value=MagicMock())
        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.pdf")
        )

        with patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock(
                side_effect=RuntimeError("pipeline boom")
            )

            with pytest.raises(RuntimeError, match="pipeline boom"):
                await _collect_events(
                    proc.process_pdf_with_docling("test.pdf", "r1", b"data", "vr1")
                )

    @pytest.mark.asyncio
    async def test_appends_pdf_extension(self):
        """If record name doesn't end in .pdf, it gets appended."""
        proc = _make_processor()
        proc.docling_client.parse_pdf = AsyncMock(return_value=MagicMock())
        proc.docling_client.create_blocks = AsyncMock(return_value=MagicMock())
        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="report")
        )

        with patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock()

            await _collect_events(
                proc.process_pdf_with_docling("report", "r1", b"data", "vr1")
            )

        call_args = proc.docling_client.parse_pdf.call_args[0]
        assert call_args[0] == "report.pdf"


# ============================================================================
# process_image — additional branches
# ============================================================================

class TestProcessImageAdditional:
    @pytest.mark.asyncio
    async def test_multimodal_embedding(self):
        """Multimodal embedding model enables image processing even without multimodal LLM."""
        image_parser = MagicMock()
        image_parser.parse_image = MagicMock(return_value=MagicMock())
        proc = _make_processor(parsers={"png": image_parser})

        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(
            recordName="photo.png", mimeType="image/png",
        ))

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm, \
             patch("app.events.processor.get_embedding_model_config", new_callable=AsyncMock) as mock_emb, \
             patch("app.events.processor.get_extension_from_mimetype", return_value="png"), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            # LLM is not multimodal, but embedding is
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            mock_emb.return_value = {"isMultimodal": True}
            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_image("r1", b"imgdata", "vr1")
            )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_no_mime_type_raises(self):
        """Raises when record has no mime type."""
        proc = _make_processor()
        record_no_mime = _mock_record_dict(recordName="photo.png")
        record_no_mime["mimeType"] = None

        proc.graph_provider.get_document = AsyncMock(return_value=record_no_mime)

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm, \
             patch("app.events.processor.get_embedding_model_config", new_callable=AsyncMock) as mock_emb:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": True})
            mock_emb.return_value = {"isMultimodal": False}

            with pytest.raises(Exception, match="No mime type"):
                await _collect_events(proc.process_image("r1", b"imgdata", "vr1"))

    @pytest.mark.asyncio
    async def test_unsupported_extension_raises(self):
        """Raises when no parser exists for the image extension."""
        proc = _make_processor(parsers={})

        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(
            recordName="photo.tiff", mimeType="image/tiff",
        ))

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm, \
             patch("app.events.processor.get_embedding_model_config", new_callable=AsyncMock) as mock_emb, \
             patch("app.events.processor.get_extension_from_mimetype", return_value="tiff"):
            mock_llm.return_value = (MagicMock(), {"isMultimodal": True})
            mock_emb.return_value = {"isMultimodal": False}

            with pytest.raises(Exception, match="Unsupported extension"):
                await _collect_events(proc.process_image("r1", b"data", "vr1"))


# ============================================================================
# convert_record_dict_to_record — additional branches
# ============================================================================

class TestConvertRecordDictAdditional:
    def test_missing_origin_defaults_to_upload(self):
        """Missing origin field defaults to UPLOAD."""
        from app.events.processor import convert_record_dict_to_record
        from app.config.constants.arangodb import OriginTypes
        d = _mock_record_dict()
        d.pop("origin", None)
        rec = convert_record_dict_to_record(d)
        assert rec.origin == OriginTypes.UPLOAD

    def test_known_connector_name(self):
        """Known connectorName is preserved."""
        from app.events.processor import convert_record_dict_to_record
        from app.config.constants.arangodb import Connectors
        rec = convert_record_dict_to_record(
            _mock_record_dict(connectorName=Connectors.KNOWLEDGE_BASE.value)
        )
        assert rec.connector_name == Connectors.KNOWLEDGE_BASE

    def test_none_connector_name_defaults(self):
        """None connectorName defaults to KNOWLEDGE_BASE."""
        from app.events.processor import convert_record_dict_to_record
        from app.config.constants.arangodb import Connectors
        rec = convert_record_dict_to_record(
            _mock_record_dict(connectorName=None)
        )
        assert rec.connector_name == Connectors.KNOWLEDGE_BASE

    def test_optional_fields(self):
        """Optional fields are captured when present."""
        from app.events.processor import convert_record_dict_to_record
        d = _mock_record_dict(
            webUrl="https://example.com",
            summaryDocumentId="sum1",
            externalRevisionId="rev1",
            isVLMOcrProcessed=True,
        )
        rec = convert_record_dict_to_record(d)
        assert rec.weburl == "https://example.com"
        assert rec.summary_document_id == "sum1"
        assert rec.external_revision_id == "rev1"
        assert rec.is_vlm_ocr_processed is True


# ============================================================================
# _map_base64_images_to_blocks
# ============================================================================

class TestMapBase64ImagesToBlocks:
    def test_maps_images_by_caption(self):
        """Block data.uri is set from caption_map."""
        from app.models.blocks import BlockType
        proc = _make_processor()

        block = MagicMock()
        block.type = BlockType.IMAGE.value
        block.image_metadata = MagicMock()
        block.image_metadata.captions = "my_image"
        block.data = {}

        caption_map = {"my_image": "data:image/png;base64,abc123"}

        proc._map_base64_images_to_blocks([block], caption_map, 0)

        assert block.data["uri"] == "data:image/png;base64,abc123"

    def test_empty_caption_map_noop(self):
        """With empty caption_map, blocks are not modified."""
        proc = _make_processor()
        block = MagicMock()

        proc._map_base64_images_to_blocks([block], {}, 0)
        # No error

    def test_caption_not_in_map_warns(self):
        """Block with caption not in map logs a warning."""
        from app.models.blocks import BlockType
        proc = _make_processor()

        block = MagicMock()
        block.type = BlockType.IMAGE.value
        block.image_metadata = MagicMock()
        block.image_metadata.captions = "missing_caption"
        block.data = {}

        caption_map = {"other_caption": "data:image/png;base64,xyz"}

        proc._map_base64_images_to_blocks([block], caption_map, 0)

        # data should not have "uri"
        assert "uri" not in block.data

    def test_list_captions_uses_first(self):
        """When captions is a list, uses the first element."""
        from app.models.blocks import BlockType
        proc = _make_processor()

        block = MagicMock()
        block.type = BlockType.IMAGE.value
        block.image_metadata = MagicMock()
        block.image_metadata.captions = ["first_cap", "second_cap"]
        block.data = {}

        caption_map = {"first_cap": "data:image/png;base64,abc"}

        proc._map_base64_images_to_blocks([block], caption_map, 0)

        assert block.data["uri"] == "data:image/png;base64,abc"

    def test_block_data_is_not_dict_replaced(self):
        """When block.data is not a dict, it gets replaced."""
        from app.models.blocks import BlockType
        proc = _make_processor()

        block = MagicMock()
        block.type = BlockType.IMAGE.value
        block.image_metadata = MagicMock()
        block.image_metadata.captions = "cap"
        block.data = "some string"

        caption_map = {"cap": "data:image/png;base64,abc"}

        proc._map_base64_images_to_blocks([block], caption_map, 0)

        assert block.data == {"uri": "data:image/png;base64,abc"}

    def test_block_data_is_none_created(self):
        """When block.data is None, a dict with uri is created."""
        from app.models.blocks import BlockType
        proc = _make_processor()

        block = MagicMock()
        block.type = BlockType.IMAGE.value
        block.image_metadata = MagicMock()
        block.image_metadata.captions = "cap"
        block.data = None

        caption_map = {"cap": "data:image/png;base64,abc"}

        proc._map_base64_images_to_blocks([block], caption_map, 0)

        assert block.data == {"uri": "data:image/png;base64,abc"}


# ============================================================================
# process_pdf_document_with_ocr — additional OCR strategy branches
# ============================================================================

class TestProcessPdfDocumentWithOcrAdditional:
    @pytest.mark.asyncio
    async def test_ocrmypdf_provider(self):
        """OCRmyPDF provider is selected and processes document."""
        proc = _make_processor()

        mock_handler = AsyncMock()
        mock_handler.process_document = AsyncMock(return_value={
            "blocks": [],
            "tables": [],
        })

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.pdf")
        )

        with patch("app.events.processor.OCRHandler", return_value=mock_handler), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock()
            proc.config_service.get_config = AsyncMock(return_value={
                "ocr": [{"provider": "ocrmypdf"}],
                "llm": [],
            })

            events = await _collect_events(
                proc.process_pdf_document_with_ocr(
                    "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                )
            )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_no_handler_no_multimodal_llm_falls_to_ocrmypdf(self):
        """No OCR handler and no multimodal LLM falls back to OCRmyPDF."""
        proc = _make_processor()

        mock_handler = AsyncMock()
        mock_handler.process_document = AsyncMock(return_value={
            "blocks": [],
            "tables": [],
        })

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.pdf")
        )

        with patch("app.events.processor.OCRHandler", return_value=mock_handler), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"), \
             patch("app.events.processor.is_multimodal_llm", return_value=False):
            MockPipeline.return_value.apply = AsyncMock()
            proc.config_service.get_config = AsyncMock(return_value={
                "ocr": [],
                "llm": [{"provider": "openai", "model": "gpt-4"}],
            })

            events = await _collect_events(
                proc.process_pdf_document_with_ocr(
                    "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                )
            )

        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_vlm_ocr_fallback_to_ocrmypdf(self):
        """When VLM OCR fails, falls back to OCRmyPDF."""
        proc = _make_processor()

        azure_handler = AsyncMock()
        azure_handler.process_document = AsyncMock(
            side_effect=RuntimeError("VLM failed")
        )
        ocrmypdf_handler = AsyncMock()
        ocrmypdf_handler.process_document = AsyncMock(
            return_value={"blocks": [], "tables": []}
        )

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.pdf")
        )

        with patch("app.events.processor.OCRHandler") as MockOCRHandler, \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockOCRHandler.side_effect = [azure_handler, ocrmypdf_handler]
            MockPipeline.return_value.apply = AsyncMock()
            proc.config_service.get_config = AsyncMock(return_value={
                "ocr": [{"provider": "vlmOCR"}],
                "llm": [],
            })

            events = await _collect_events(
                proc.process_pdf_document_with_ocr(
                    "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                )
            )

        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_ocrmypdf_exception_not_caught(self):
        """When OCRmyPDF handler itself fails, exception propagates (no fallback for ocrmypdf)."""
        proc = _make_processor()

        mock_handler = AsyncMock()
        mock_handler.process_document = AsyncMock(
            side_effect=RuntimeError("OCR failed")
        )

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.pdf")
        )

        with patch("app.events.processor.OCRHandler", return_value=mock_handler), \
             patch("app.events.processor.TransformContext"):
            proc.config_service.get_config = AsyncMock(return_value={
                "ocr": [{"provider": "ocrmypdf"}],
                "llm": [],
            })

            with pytest.raises(RuntimeError, match="OCR failed"):
                await _collect_events(
                    proc.process_pdf_document_with_ocr(
                        "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                    )
                )

    @pytest.mark.asyncio
    async def test_non_vlm_ocr_with_paragraph_blocks(self):
        """Non-VLM OCR path with dict paragraph blocks (not Block instances)."""
        proc = _make_processor()

        mock_handler = AsyncMock()
        mock_handler.process_document = AsyncMock(return_value={
            "blocks": [
                {
                    "content": "Paragraph text",
                    "page_number": 1,
                    "bounding_box": [{"x": 0, "y": 0}, {"x": 100, "y": 0}, {"x": 100, "y": 100}, {"x": 0, "y": 100}],
                },
            ],
            "tables": [],
        })

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.pdf")
        )

        with patch("app.events.processor.OCRHandler", return_value=mock_handler), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock()
            proc.config_service.get_config = AsyncMock(return_value={
                "ocr": [{"provider": "ocrmypdf"}],
                "llm": [],
            })

            events = await _collect_events(
                proc.process_pdf_document_with_ocr(
                    "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                )
            )

        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_non_vlm_ocr_paragraph_with_bounding_box(self):
        """Non-VLM OCR paragraph block with bounding_box info (4 points required)."""
        proc = _make_processor()

        mock_handler = AsyncMock()
        mock_handler.process_document = AsyncMock(return_value={
            "blocks": [
                {
                    "content": "Text",
                    "page_number": 1,
                    "bounding_box": [
                        {"x": 0, "y": 0}, {"x": 100, "y": 0},
                        {"x": 100, "y": 100}, {"x": 0, "y": 100},
                    ],
                },
            ],
            "tables": [],
        })

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.pdf")
        )

        with patch("app.events.processor.OCRHandler", return_value=mock_handler), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock()
            proc.config_service.get_config = AsyncMock(return_value={
                "ocr": [{"provider": "ocrmypdf"}],
                "llm": [],
            })

            events = await _collect_events(
                proc.process_pdf_document_with_ocr(
                    "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                )
            )

        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_non_vlm_ocr_empty_paragraph_skipped(self):
        """Non-VLM OCR paragraphs with empty content are skipped."""
        proc = _make_processor()

        mock_handler = AsyncMock()
        mock_handler.process_document = AsyncMock(return_value={
            "blocks": [
                {"content": ""},  # empty content
                None,  # null paragraph
            ],
            "tables": [],
        })

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.pdf")
        )

        with patch("app.events.processor.OCRHandler", return_value=mock_handler), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock()
            proc.config_service.get_config = AsyncMock(return_value={
                "ocr": [{"provider": "ocrmypdf"}],
                "llm": [],
            })

            events = await _collect_events(
                proc.process_pdf_document_with_ocr(
                    "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                )
            )

        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_non_vlm_ocr_record_not_found(self):
        """Non-VLM OCR path: missing record yields indexing_complete."""
        proc = _make_processor()

        mock_handler = AsyncMock()
        mock_handler.process_document = AsyncMock(return_value={
            "blocks": [],
            "tables": [],
        })

        proc.graph_provider.get_document = AsyncMock(return_value=None)

        with patch("app.events.processor.OCRHandler", return_value=mock_handler):
            proc.config_service.get_config = AsyncMock(return_value={
                "ocr": [{"provider": "ocrmypdf"}],
                "llm": [],
            })

            events = await _collect_events(
                proc.process_pdf_document_with_ocr(
                    "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                )
            )

        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_no_handler_multimodal_llm_check_exception(self):
        """Exception during multimodal LLM check is handled gracefully."""
        proc = _make_processor()

        mock_handler = AsyncMock()
        mock_handler.process_document = AsyncMock(return_value={
            "blocks": [],
            "tables": [],
        })

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.pdf")
        )

        with patch("app.events.processor.OCRHandler", return_value=mock_handler), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"), \
             patch("app.events.processor.is_multimodal_llm", side_effect=Exception("check failed")):
            MockPipeline.return_value.apply = AsyncMock()
            proc.config_service.get_config = AsyncMock(return_value={
                "ocr": [],
                "llm": [{"provider": "openai"}],
            })

            events = await _collect_events(
                proc.process_pdf_document_with_ocr(
                    "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                )
            )

        # Falls back to OCRmyPDF when multimodal check fails
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_non_vlm_ocr_with_table_row_blocks(self):
        """Non-VLM OCR path with TABLE_ROW Block instances."""
        from app.models.blocks import Block, BlockGroup, BlockType, DataFormat, GroupType

        proc = _make_processor()

        table_row_block = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.TXT,
            data="row data",
            comments=[],
            parent_index=0,
        )
        mock_handler = AsyncMock()
        mock_handler.process_document = AsyncMock(return_value={
            "blocks": [table_row_block],
            "tables": [BlockGroup(
                index=0,
                type=GroupType.TABLE,
                name="table1",
            )],
        })

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.pdf")
        )

        with patch("app.events.processor.OCRHandler", return_value=mock_handler), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock()
            proc.config_service.get_config = AsyncMock(return_value={
                "ocr": [{"provider": "ocrmypdf"}],
                "llm": [],
            })

            events = await _collect_events(
                proc.process_pdf_document_with_ocr(
                    "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                )
            )

        assert any(e["event"] == "indexing_complete" for e in events)


# ============================================================================
# _run_indexing_pipeline — additional error propagation
# ============================================================================

class TestRunIndexingPipelineAdditional:
    @pytest.mark.asyncio
    async def test_pipeline_error_in_docx(self):
        """Pipeline error from process_docx_document propagates."""
        proc = _make_processor()

        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(return_value=MagicMock())
        mock_blocks = MagicMock()
        mock_blocks.blocks = []
        mock_processor.create_blocks = AsyncMock(return_value=mock_blocks)

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.docx")
        )

        with patch("app.events.processor.DoclingProcessor", return_value=mock_processor), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock(
                side_effect=RuntimeError("docx pipeline error")
            )

            with pytest.raises(RuntimeError, match="docx pipeline error"):
                await _collect_events(
                    proc.process_docx_document(
                        "test.docx", "r1", "1", "src", "o1", b"docx", "vr1"
                    )
                )

    @pytest.mark.asyncio
    async def test_pipeline_error_in_pptx(self):
        """Pipeline error from process_pptx_document propagates."""
        proc = _make_processor()

        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(return_value=MagicMock())
        mock_blocks = MagicMock()
        mock_blocks.blocks = []
        mock_processor.create_blocks = AsyncMock(return_value=mock_blocks)

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.pptx")
        )

        with patch("app.events.processor.DoclingProcessor", return_value=mock_processor), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock(
                side_effect=RuntimeError("pptx pipeline error")
            )

            with pytest.raises(RuntimeError, match="pptx pipeline error"):
                await _collect_events(
                    proc.process_pptx_document(
                        "test.pptx", "r1", "1", "src", "o1", b"pptx", "vr1"
                    )
                )


# ============================================================================
# process_blocks — additional branches
# ============================================================================

class TestProcessBlocksAdditional:
    @pytest.mark.asyncio
    async def test_blocks_exception_propagates(self):
        """Exception during blocks processing propagates."""
        proc = _make_processor()

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test")
        )

        blocks_dict = {"blocks": [], "block_groups": []}

        with patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            MockPipeline.return_value.apply = AsyncMock(
                side_effect=RuntimeError("blocks pipeline error")
            )

            with pytest.raises(RuntimeError, match="blocks pipeline error"):
                await _collect_events(
                    proc.process_blocks("test", "r1", "1", "src", "o1", blocks_dict, "vr1")
                )


# ============================================================================
# process_image — batch_upsert_nodes failure
# ============================================================================

class TestProcessImageBatchUpsertFailure:
    @pytest.mark.asyncio
    async def test_non_multimodal_upsert_failure_raises(self):
        """When batch_upsert_nodes returns False during non-multimodal status update, raises."""
        from app.exceptions.indexing_exceptions import DocumentProcessingError
        proc = _make_processor()

        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(
            recordName="photo.png", mimeType="image/png",
        ))
        proc.graph_provider.batch_upsert_nodes = AsyncMock(return_value=False)

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm, \
             patch("app.events.processor.get_embedding_model_config", new_callable=AsyncMock) as mock_emb:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            mock_emb.return_value = {"isMultimodal": False}

            with pytest.raises(DocumentProcessingError):
                await _collect_events(
                    proc.process_image("r1", b"imgdata", "vr1")
                )


# ============================================================================
# _calculate_index_shift_map
# ============================================================================

class TestCalculateIndexShiftMap:
    def test_no_processing_results(self):
        """With no processing results, all shifts are 0."""
        from app.models.blocks import BlockGroup
        proc = _make_processor()

        bg1 = MagicMock(spec=BlockGroup)
        bg1.index = 0
        bg2 = MagicMock(spec=BlockGroup)
        bg2.index = 1

        result = proc._calculate_index_shift_map([bg1, bg2], {})
        assert result == {0: 0, 1: 0}

    def test_with_processing_results(self):
        """Cumulative shift is calculated correctly."""
        from app.models.blocks import BlockGroup
        proc = _make_processor()

        bg1 = MagicMock(spec=BlockGroup)
        bg1.index = 0
        bg2 = MagicMock(spec=BlockGroup)
        bg2.index = 1
        bg3 = MagicMock(spec=BlockGroup)
        bg3.index = 2

        # bg1 processed with 3 new block groups
        processing_results = {
            0: ([MagicMock(), MagicMock(), MagicMock()], []),
        }

        result = proc._calculate_index_shift_map([bg1, bg2, bg3], processing_results)
        assert result[0] == 0
        assert result[1] == 3
        assert result[2] == 3


# ============================================================================
# convert_record_dict_to_record
# ============================================================================

class TestConvertRecordDictToRecord:
    def test_basic_conversion(self):
        from app.events.processor import convert_record_dict_to_record
        record_dict = _mock_record_dict()
        record = convert_record_dict_to_record(record_dict)
        assert record.id == "r1"
        assert record.org_id == "o1"
        assert record.record_name == "test.md"

    def test_unknown_connector_defaults(self):
        from app.events.processor import convert_record_dict_to_record
        record_dict = _mock_record_dict(connectorName="unknown_connector_xyz")
        record = convert_record_dict_to_record(record_dict)
        # Unknown connector should default to KNOWLEDGE_BASE
        assert record.connector_name is not None

    def test_none_connector_defaults(self):
        from app.events.processor import convert_record_dict_to_record
        record_dict = _mock_record_dict()
        record_dict.pop("connectorName", None)
        record = convert_record_dict_to_record(record_dict)
        assert record.connector_name is not None

    def test_unknown_origin_defaults(self):
        from app.events.processor import convert_record_dict_to_record
        record_dict = _mock_record_dict(origin="UNKNOWN_ORIGIN")
        record = convert_record_dict_to_record(record_dict)
        assert record.origin is not None

    def test_missing_origin_defaults(self):
        from app.events.processor import convert_record_dict_to_record
        record_dict = _mock_record_dict()
        record_dict.pop("origin", None)
        record = convert_record_dict_to_record(record_dict)
        assert record.origin is not None

    def test_all_fields_mapped(self):
        from app.events.processor import convert_record_dict_to_record
        record_dict = _mock_record_dict(
            webUrl="https://example.com",
            externalRevisionId="rev1",
            summaryDocumentId="sum1",
            sourceCreatedAtTimestamp=500,
            sourceLastModifiedTimestamp=600,
            isVLMOcrProcessed=True,
        )
        record = convert_record_dict_to_record(record_dict)
        assert record.weburl == "https://example.com"
        assert record.external_revision_id == "rev1"
        assert record.is_vlm_ocr_processed is True


# ============================================================================
# process_md_document — additional edge cases
# ============================================================================

class TestProcessMdDocumentAdditional:
    pass
class TestProcessHtmlDocumentAdditional:
    @pytest.mark.asyncio
    async def test_html_with_image_replacement(self):
        """HTML parser replaces relative image URLs."""
        proc = _make_processor()

        async def _fake_md(*args, **kwargs):
            yield {"event": "parsing_complete", "data": {"record_id": "r1"}}
            yield {"event": "indexing_complete", "data": {"record_id": "r1"}}

        proc.process_md_document = _fake_md

        html_parser = MagicMock()
        html_parser.replace_relative_image_urls = MagicMock(side_effect=lambda x: x.replace("relative", "absolute"))
        proc.parsers = {"html": html_parser}

        html = b"<html><body><img src='relative/img.png'/></body></html>"
        events = await _collect_events(
            proc.process_html_document("test.html", "r1", "1", "web", "org1", html, "vr1")
        )
        assert any(e["event"] == "indexing_complete" for e in events)


# ============================================================================
# process_txt_document — encoding detection
# ============================================================================

class TestProcessTxtDocumentAdditional:
    @pytest.mark.asyncio
    async def test_utf8_encoding(self):
        """UTF-8 encoded text is decoded correctly."""
        proc = _make_processor()

        async def _fake_md(*args, **kwargs):
            yield {"event": "parsing_complete", "data": {}}
            yield {"event": "indexing_complete", "data": {}}

        proc.process_md_document = _fake_md

        text = "Hello UTF-8 world".encode("utf-8")
        events = await _collect_events(
            proc.process_txt_document("test.txt", "r1", "1", "src", "o1", text, "vr1", "FILE", "UPLOAD", "UPLOAD")
        )
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_latin1_encoding(self):
        """Latin-1 encoded text is decoded when UTF-8 fails."""
        proc = _make_processor()

        async def _fake_md(*args, **kwargs):
            yield {"event": "parsing_complete", "data": {}}
            yield {"event": "indexing_complete", "data": {}}

        proc.process_md_document = _fake_md

        text = "Caf\xe9".encode("latin-1")
        events = await _collect_events(
            proc.process_txt_document("test.txt", "r1", "1", "src", "o1", text, "vr1", "FILE", "UPLOAD", "UPLOAD")
        )
        assert any(e["event"] == "indexing_complete" for e in events)


# ============================================================================
# process_excel_document — additional
# ============================================================================

class TestProcessExcelDocumentAdditional:
    @pytest.mark.asyncio
    async def test_parser_exception(self):
        """Exception during workbook load propagates."""
        excel_parser = MagicMock()
        excel_parser.load_workbook_from_binary = MagicMock(side_effect=RuntimeError("corrupt excel"))
        proc = _make_processor(parsers={"xlsx": excel_parser})

        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(recordName="bad.xlsx"))

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {})
            with pytest.raises(RuntimeError, match="corrupt excel"):
                await _collect_events(
                    proc.process_excel_document("bad.xlsx", "r1", "1", "src", "o1", b"data", "vr1")
                )


# ============================================================================
# process_image — additional
# ============================================================================

class TestProcessImageAdditional:
    @pytest.mark.asyncio
    async def test_multimodal_embedding(self):
        """Image with multimodal embedding (not LLM) is handled."""
        image_parser = MagicMock()
        image_parser.parse_image = MagicMock(return_value=MagicMock())
        proc = _make_processor(parsers={"png": image_parser})

        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict(
            recordName="photo.png", mimeType="image/png",
        ))

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm, \
             patch("app.events.processor.get_embedding_model_config", new_callable=AsyncMock) as mock_emb, \
             patch("app.events.processor.get_extension_from_mimetype", return_value="png"), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch("app.events.processor.TransformContext"):
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            mock_emb.return_value = {"isMultimodal": True}
            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_image("r1", b"imgdata", "vr1")
            )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_empty_image_data(self):
        """Empty bytes for image raises exception."""
        proc = _make_processor()

        with pytest.raises(Exception, match="No image data"):
            await _collect_events(proc.process_image("r1", b"", "vr1"))


# ============================================================================
# Processor.__init__
# ============================================================================

class TestProcessorInit:
    def test_initialization(self):
        proc = _make_processor()
        assert proc.graph_provider is not None
        assert proc.parsers == {}
        assert proc.indexing_pipeline is not None
