"""Coverage-gap tests for app.events.processor.

Targets uncovered branches from coverage report on processor.py:
IndexingError re-raises, OCR VLM edge arcs, non-VLM OCR path,
_enhance_tables_with_llm edges, HTML blockgroups, empty HTML,
build container orphans, delimited UnicodeDecodeError, structured docs.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.ai_models import OCRProvider
from app.config.constants.arangodb import ProgressStatus
from app.exceptions.indexing_exceptions import DocumentProcessingError, IndexingError
from app.models.blocks import (
    Block,
    BlockContainerIndex,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    DataFormat,
    GroupType,
    TableMetadata,
    TableRowMetadata,
)
from app.services.messaging.config import IndexingEvent

log = logging.getLogger("test_processor_coverage_gaps")
log.setLevel(logging.CRITICAL)


def _make_processor(**overrides):
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
        return Processor(**kwargs)


def _record(**overrides):
    base = {
        "_key": "r1",
        "orgId": "o1",
        "recordName": "test.bin",
        "recordType": "FILE",
        "indexingStatus": "NOT_STARTED",
        "externalRecordId": "ext1",
        "connectorId": "c1",
        "mimeType": "application/octet-stream",
        "createdAtTimestamp": 1000,
        "updatedAtTimestamp": 2000,
        "version": 1,
    }
    base.update(overrides)
    return base


async def _collect(async_gen):
    events = []
    async for ev in async_gen:
        events.append(ev)
    return events


# ---------------------------------------------------------------------------
# IndexingError / DocumentProcessingError re-raise arcs
# ---------------------------------------------------------------------------


class TestIndexingErrorReraise:
    @pytest.mark.asyncio
    async def test_process_image_document_processing_error_reraise(self):
        proc = _make_processor()
        proc.graph_provider.get_document = AsyncMock(
            return_value=_record(recordName="photo.png", mimeType="image/png")
        )
        proc.graph_provider.update_node = AsyncMock(
            side_effect=DocumentProcessingError("status boom", doc_id="r1")
        )

        with patch(
            "app.events.processor.get_llm_for_role", new_callable=AsyncMock
        ) as mock_llm, patch(
            "app.events.processor.get_embedding_model_config", new_callable=AsyncMock
        ) as mock_emb:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            mock_emb.return_value = {"isMultimodal": False}

            with pytest.raises(DocumentProcessingError, match="status boom"):
                await _collect(proc.process_image("r1", b"img", "vr1"))

    @pytest.mark.asyncio
    async def test_process_pdf_with_pdf_plumber_indexing_error(self):
        proc = _make_processor()
        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(
            side_effect=IndexingError("plumber fail", record_id="r1")
        )

        with patch(
            "app.events.processor.PDFPlumberOpenCVProcessor", return_value=mock_processor
        ):
            with pytest.raises(IndexingError, match="plumber fail"):
                await _collect(
                    proc.process_pdf_with_pdf_plumber("doc.pdf", "r1", b"%PDF", "vr1")
                )

    @pytest.mark.asyncio
    async def test_process_pdf_with_docling_indexing_error(self):
        proc = _make_processor()
        proc.docling_client.parse_pdf = AsyncMock(
            side_effect=IndexingError("docling fail", record_id="r1")
        )

        with pytest.raises(IndexingError, match="docling fail"):
            await _collect(proc.process_pdf_with_docling("doc.pdf", "r1", b"%PDF", "vr1"))

    @pytest.mark.asyncio
    async def test_process_docx_indexing_error(self):
        proc = _make_processor()
        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(
            side_effect=IndexingError("docx fail", record_id="r1")
        )

        with patch("app.events.processor.DoclingProcessor", return_value=mock_processor):
            with pytest.raises(IndexingError, match="docx fail"):
                await _collect(
                    proc.process_docx_document(
                        "doc.docx", "r1", "1", "kb", "o1", b"docx", "vr1"
                    )
                )

    @pytest.mark.asyncio
    async def test_process_excel_indexing_error(self):
        from app.config.constants.arangodb import ExtensionTypes

        proc = _make_processor()
        parser = MagicMock()
        parser.load_workbook_from_binary = MagicMock()
        parser.create_blocks = AsyncMock(
            side_effect=IndexingError("xlsx fail", record_id="r1")
        )
        proc.parsers = {ExtensionTypes.XLSX.value: parser}

        with patch("app.events.processor.get_llm_for_role", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {})
            with pytest.raises(IndexingError, match="xlsx fail"):
                await _collect(
                    proc.process_excel_document(
                        "a.xlsx", "r1", "1", "kb", "o1", b"xlsx", "vr1"
                    )
                )

    @pytest.mark.asyncio
    async def test_process_xls_indexing_error(self):
        from app.config.constants.arangodb import ExtensionTypes

        proc = _make_processor()
        xls_parser = MagicMock()
        xls_parser.convert_xls_to_xlsx = MagicMock(
            side_effect=IndexingError("xls fail", record_id="r1")
        )
        proc.parsers = {ExtensionTypes.XLS.value: xls_parser}

        with pytest.raises(IndexingError, match="xls fail"):
            await _collect(
                proc.process_xls_document("a.xls", "r1", "1", "kb", "o1", b"xls", "vr1")
            )

    @pytest.mark.asyncio
    async def test_process_delimited_indexing_error(self):
        from app.config.constants.arangodb import ExtensionTypes

        proc = _make_processor()
        parser = MagicMock()
        parser.read_raw_rows = MagicMock(return_value=[["a", "b"]])
        parser.find_tables_in_csv = MagicMock(return_value=[[[ "a", "b" ]]])
        parser.get_blocks_from_csv_with_multiple_tables = AsyncMock(
            side_effect=IndexingError("csv fail", record_id="r1")
        )
        proc.parsers = {ExtensionTypes.CSV.value: parser}
        proc.graph_provider.get_document = AsyncMock(return_value=_record(recordName="a.csv"))

        with patch("app.events.processor.get_llm_for_role", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {})
            with pytest.raises(IndexingError, match="csv fail"):
                await _collect(
                    proc.process_delimited_document("a.csv", "r1", b"a,b\n1,2", "vr1")
                )

    @pytest.mark.asyncio
    async def test_process_html_indexing_error(self):
        from app.config.constants.arangodb import ExtensionTypes

        proc = _make_processor()
        html_parser = MagicMock()
        html_parser.clean_html = MagicMock(side_effect=lambda x: x)
        html_parser.replace_relative_image_urls = MagicMock(side_effect=lambda x: x)
        html_parser.extract_and_replace_images = MagicMock(side_effect=lambda x: (x, []))
        html_parser.parse_to_blocks = AsyncMock(
            side_effect=IndexingError("html fail", record_id="r1")
        )
        proc.parsers = {ExtensionTypes.HTML.value: html_parser}

        with pytest.raises(IndexingError, match="html fail"):
            await _collect(
                proc.process_html_document(
                    "a.html", "r1", "1", "web", "o1", b"<p>hi</p>", "vr1"
                )
            )

    @pytest.mark.asyncio
    async def test_process_txt_indexing_error(self):
        proc = _make_processor()

        async def _boom(*_a, **_k):
            raise IndexingError("txt fail", record_id="r1")
            yield  # pragma: no cover — make this an async generator

        with patch.object(proc, "process_md_document", side_effect=_boom):
            with pytest.raises(IndexingError, match="txt fail"):
                await _collect(
                    proc.process_txt_document(
                        "a.txt",
                        "r1",
                        "1",
                        "kb",
                        "o1",
                        b"hello",
                        "vr1",
                        "FILE",
                        "KB",
                        "UPLOAD",
                    )
                )

    @pytest.mark.asyncio
    async def test_process_pptx_indexing_error(self):
        proc = _make_processor()
        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(
            side_effect=IndexingError("pptx fail", record_id="r1")
        )

        with patch("app.events.processor.DoclingProcessor", return_value=mock_processor):
            with pytest.raises(IndexingError, match="pptx fail"):
                await _collect(
                    proc.process_pptx_document(
                        "a.pptx", "r1", "1", "kb", "o1", b"pptx", "vr1"
                    )
                )


# ---------------------------------------------------------------------------
# OCR: multimodal skip, VLM page edges, non-VLM Azure-style path
# ---------------------------------------------------------------------------


class TestOcrCoverageGaps:
    @pytest.mark.asyncio
    async def test_non_multimodal_llm_skipped_then_vlm_used(self):
        """Line 393→392: non-multimodal LLM configs are skipped in the loop."""
        proc = _make_processor()
        proc.config_service.get_config = AsyncMock(
            return_value={"ocr": [], "llm": [{"provider": "openai"}, {"provider": "mm"}]}
        )
        proc.graph_provider.get_document = AsyncMock(
            return_value=_record(recordName="a.pdf")
        )
        handler = MagicMock()
        handler.process_document = AsyncMock(return_value={"pages": []})

        with patch(
            "app.events.processor.is_multimodal_llm", side_effect=[False, True]
        ), patch(
            "app.events.processor.OCRHandler", return_value=handler
        ), patch(
            "app.events.processor.DoclingProcessor"
        ), patch(
            "app.events.processor.IndexingPipeline"
        ) as MockPipeline:
            MockPipeline.return_value.apply = AsyncMock()
            events = await _collect(
                proc.process_pdf_document_with_ocr(
                    "a.pdf", "r1", "1", "kb", "o1", b"%PDF", "vr1"
                )
            )

        handler.process_document.assert_awaited_once()
        assert any(e.event == IndexingEvent.PARSING_COMPLETE for e in events)
        assert any(e.event == IndexingEvent.INDEXING_COMPLETE for e in events)

    @pytest.mark.asyncio
    async def test_vlm_empty_create_blocks_skips_page(self):
        """459→452: create_blocks returns None/empty → skip page merge."""
        proc = _make_processor()
        proc.config_service.get_config = AsyncMock(
            return_value={"ocr": [{"provider": OCRProvider.VLM_OCR.value}], "llm": []}
        )
        proc.graph_provider.get_document = AsyncMock(return_value=_record(recordName="a.pdf"))

        handler = MagicMock()
        handler.process_document = AsyncMock(
            return_value={
                "pages": [
                    {"page_number": 1, "markdown": "# page one"},
                    {"page_number": 2, "markdown": "# page two"},
                ]
            }
        )
        processor = MagicMock()
        processor.parse_document = AsyncMock(side_effect=[MagicMock(), MagicMock()])
        processor.create_blocks = AsyncMock(
            side_effect=[
                None,
                BlocksContainer(
                    blocks=[
                        Block(
                            index=0,
                            type=BlockType.TEXT,
                            format=DataFormat.TXT,
                            data="p2",
                            parent_index=None,
                        )
                    ],
                    block_groups=[],
                ),
            ]
        )

        with patch("app.events.processor.OCRHandler", return_value=handler), patch(
            "app.events.processor.DoclingProcessor", return_value=processor
        ), patch("app.events.processor.IndexingPipeline") as MockPipeline:
            MockPipeline.return_value.apply = AsyncMock()
            events = await _collect(
                proc.process_pdf_document_with_ocr(
                    "a.pdf", "r1", "1", "kb", "o1", b"%PDF", "vr1"
                )
            )

        assert any(e.event == IndexingEvent.INDEXING_COMPLETE for e in events)

    @pytest.mark.asyncio
    async def test_vlm_parent_index_and_children_offsets(self):
        """463/470/472: parent_index None vs set; children present vs absent."""
        proc = _make_processor()
        proc.config_service.get_config = AsyncMock(
            return_value={"ocr": [{"provider": OCRProvider.VLM_OCR.value}], "llm": []}
        )
        proc.graph_provider.get_document = AsyncMock(return_value=_record(recordName="a.pdf"))

        handler = MagicMock()
        handler.process_document = AsyncMock(
            return_value={"pages": [{"page_number": 1, "markdown": "# t"}, {"page_number": 2, "markdown": "# u"}]}
        )

        bg_with_children = BlockGroup(index=0, type=GroupType.TABLE, parent_index=None)
        bg_with_children.children = BlockGroupChildren.from_indices(block_indices=[0])

        bg_nested = BlockGroup(index=0, type=GroupType.TABLE, parent_index=0)
        bg_nested.children = None

        page1 = BlocksContainer(
            blocks=[
                Block(
                    index=0,
                    type=BlockType.TEXT,
                    format=DataFormat.TXT,
                    data="a",
                    parent_index=None,
                ),
                Block(
                    index=1,
                    type=BlockType.TEXT,
                    format=DataFormat.TXT,
                    data="b",
                    parent_index=0,
                ),
            ],
            block_groups=[bg_with_children],
        )
        page2 = BlocksContainer(
            blocks=[
                Block(
                    index=0,
                    type=BlockType.TEXT,
                    format=DataFormat.TXT,
                    data="c",
                    parent_index=0,
                )
            ],
            block_groups=[bg_nested],
        )

        processor = MagicMock()
        processor.parse_document = AsyncMock(side_effect=[MagicMock(), MagicMock()])
        processor.create_blocks = AsyncMock(side_effect=[page1, page2])

        with patch("app.events.processor.OCRHandler", return_value=handler), patch(
            "app.events.processor.DoclingProcessor", return_value=processor
        ), patch("app.events.processor.IndexingPipeline") as MockPipeline:
            MockPipeline.return_value.apply = AsyncMock()
            events = await _collect(
                proc.process_pdf_document_with_ocr(
                    "a.pdf", "r1", "1", "kb", "o1", b"%PDF", "vr1"
                )
            )

        assert any(e.event == IndexingEvent.INDEXING_COMPLETE for e in events)

    @pytest.mark.asyncio
    async def test_non_vlm_ocr_dict_and_block_path(self):
        """514–586: non-VLM OCR branch (dict paragraphs + Block TABLE_ROW + tables)."""
        proc = _make_processor()
        proc.config_service.get_config = AsyncMock(
            return_value={"ocr": [{"provider": "vlmOCR"}], "llm": []}
        )
        proc.graph_provider.get_document = AsyncMock(return_value=_record(recordName="a.pdf"))

        table_bg = BlockGroup(index=0, type=GroupType.TABLE)
        empty_table = BlockGroup(index=99, type=GroupType.TABLE)
        row = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data={"cells": ["x"]},
            parent_index=0,
        )
        handler = MagicMock()
        handler.process_document = AsyncMock(
            return_value={
                "blocks": [
                    row,
                    {
                        "content": "hello",
                        "page_number": 1,
                        "bounding_box": [
                            {"x": 1, "y": 2},
                            {"x": 3, "y": 4},
                            {"x": 5, "y": 6},
                            {"x": 7, "y": 8},
                        ],
                    },
                    {
                        "content": "bad-bbox",
                        "page_number": 2,
                        "bounding_box": "not-a-list",
                    },
                    {"content": "", "page_number": 3},
                ],
                "tables": [table_bg, empty_table],
            }
        )

        class FlipValue:
            def __init__(self):
                self.n = 0

            def __eq__(self, other):
                # provider (str) == FlipValue → this method runs
                self.n += 1
                return self.n == 1

            def __hash__(self):
                return hash("flip")

            def __str__(self):
                return "vlmOCR"

        flip = FlipValue()

        class _VlmProxy:
            value = flip

        with patch("app.events.processor.OCRProvider") as mock_ocr_provider, patch(
            "app.events.processor.OCRHandler", return_value=handler
        ), patch("app.events.processor.IndexingPipeline") as MockPipeline:
            mock_ocr_provider.VLM_OCR = _VlmProxy()
            mock_ocr_provider.AZURE_DI = OCRProvider.AZURE_DI
            MockPipeline.return_value.apply = AsyncMock()
            events = await _collect(
                proc.process_pdf_document_with_ocr(
                    "a.pdf", "r1", "1", "kb", "o1", b"%PDF", "vr1"
                )
            )

        assert any(e.event == IndexingEvent.PARSING_COMPLETE for e in events)
        assert any(e.event == IndexingEvent.INDEXING_COMPLETE for e in events)
        assert table_bg.children is not None
        assert empty_table.children is None


# ---------------------------------------------------------------------------
# _enhance_tables_with_llm edges
# ---------------------------------------------------------------------------


class TestEnhanceTablesCoverageGaps:
    @pytest.mark.asyncio
    async def test_data_none_initialized_before_summary_keys(self):
        proc = _make_processor()
        bg = BlockGroup(index=0, type=GroupType.TABLE, data={"table_markdown": "| A |"})
        container = BlocksContainer(blocks=[], block_groups=[bg])
        response = MagicMock(summary="sum", headers=["A"])

        async def _summary(*_a, **_k):
            bg.data = None
            return response

        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            side_effect=_summary,
        ), patch(
            "app.utils.indexing_helpers.get_rows_text",
            new_callable=AsyncMock,
            return_value=([], []),
        ):
            await proc._enhance_tables_with_llm(container)

        assert bg.data["table_summary"] == "sum"
        assert bg.data["column_headers"] == ["A"]

    @pytest.mark.asyncio
    async def test_blockgroup_children_edge_filters(self):
        proc = _make_processor()
        # Keep row_blocks/row_dicts aligned: every TABLE_ROW that enters row_blocks
        # must also contribute a row_dict (data with a "cells" key).
        blocks = [
            Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="t"),
            Block(
                index=1,
                type=BlockType.TABLE_ROW,
                format=DataFormat.JSON,
                data={"cells": ["c1"]},
                table_row_metadata=TableRowMetadata(is_header=True),
            ),
            Block(
                index=2,
                type=BlockType.TABLE_ROW,
                format=DataFormat.JSON,
                data={"cells": "not-list"},
            ),
            Block(
                index=3,
                type=BlockType.TABLE_ROW,
                format=DataFormat.JSON,
                data={"cells": ["v"]},
            ),
            Block(
                index=4,
                type=BlockType.TABLE_ROW,
                format=DataFormat.JSON,
                data={"cells": ["w"]},
            ),
        ]
        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            data={"table_markdown": "| A |"},
            table_metadata=TableMetadata(),
        )
        # OOB (10), TEXT (0), header (1), non-list cells (2), good (3), extra non-header (4)
        bg.children = BlockGroupChildren.from_indices(block_indices=[10, 0, 1, 2, 3, 4])
        container = BlocksContainer(blocks=blocks, block_groups=[bg])
        response = MagicMock(summary="s", headers=["A"])

        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=response,
        ), patch(
            "app.utils.indexing_helpers.get_rows_text",
            new_callable=AsyncMock,
            return_value=(["only one desc"], []),
        ) as mock_rows:
            await proc._enhance_tables_with_llm(container)

        mock_rows.assert_awaited_once()
        # Header skipped; sole description maps to first non-header row_blocks entry
        # (index 2 — non-list cells), later non-header rows get nothing.
        assert blocks[1].data.get("row_natural_language_text") is None
        assert blocks[2].data.get("row_natural_language_text") == "only one desc"
        assert blocks[3].data.get("row_natural_language_text") is None
        assert blocks[4].data.get("row_natural_language_text") is None

    @pytest.mark.asyncio
    async def test_blockgroup_row_without_cells_skipped_for_row_dicts(self):
        """Cover no-cells filter (751): TABLE_ROW enters row_blocks but not row_dicts."""
        proc = _make_processor()
        row_no_cells = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data={"no_cells": True},
        )
        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            data={"table_markdown": "| A |"},
        )
        bg.children = BlockGroupChildren.from_indices(block_indices=[0])
        container = BlocksContainer(blocks=[row_no_cells], block_groups=[bg])
        response = MagicMock(summary="s", headers=["A"])

        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=response,
        ), patch(
            "app.utils.indexing_helpers.get_rows_text",
            new_callable=AsyncMock,
        ) as mock_rows:
            await proc._enhance_tables_with_llm(container)

        mock_rows.assert_not_awaited()
        assert "row_natural_language_text" not in row_no_cells.data

    @pytest.mark.asyncio
    async def test_blockgroup_falsy_data_skips_description_write(self):
        """Cover falsy row_block.data write skip (820)."""
        proc = _make_processor()
        row_cleared = MagicMock()
        row_cleared.type = BlockType.TABLE_ROW
        row_cleared.table_row_metadata = None
        row_cleared.data = {"cells": ["z"]}

        bg = MagicMock()
        bg.type = GroupType.TABLE
        bg.index = 0
        bg.data = {"table_markdown": "| A |"}
        bg.table_metadata = None
        bg.description = None
        bg.children = BlockGroupChildren.from_indices(block_indices=[0])

        container = MagicMock()
        container.block_groups = [bg]
        container.blocks = [row_cleared]
        response = MagicMock(summary="s", headers=["A"])

        async def _rows(*_a, **_k):
            row_cleared.data = None
            return (["d1"], [])

        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=response,
        ), patch(
            "app.utils.indexing_helpers.get_rows_text",
            new_callable=AsyncMock,
            side_effect=_rows,
        ):
            await proc._enhance_tables_with_llm(container)

        assert row_cleared.data is None

    @pytest.mark.asyncio
    async def test_legacy_list_children_edge_filters(self):
        """Bypass pydantic children converter with MagicMock so list branch runs."""
        proc = _make_processor()
        blocks = [
            Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="t"),
            Block(
                index=1,
                type=BlockType.TABLE_ROW,
                format=DataFormat.JSON,
                data={"no_cells": True},
            ),
            Block(
                index=2,
                type=BlockType.TABLE_ROW,
                format=DataFormat.JSON,
                data={"cells": ["x"]},
            ),
        ]
        bg = MagicMock()
        bg.type = GroupType.TABLE
        bg.index = 0
        bg.data = {"table_markdown": "| A |"}
        bg.table_metadata = None
        bg.description = None
        bg.children = [
            "bad",
            BlockContainerIndex(block_index=None),
            BlockContainerIndex(block_index=99),
            BlockContainerIndex(block_index=0),
            BlockContainerIndex(block_index=1),
            BlockContainerIndex(block_index=2),
        ]
        container = MagicMock()
        container.block_groups = [bg]
        container.blocks = blocks
        response = MagicMock(summary="s", headers=[])

        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=response,
        ), patch(
            "app.utils.indexing_helpers.get_rows_text",
            new_callable=AsyncMock,
            return_value=([], []),
        ):
            await proc._enhance_tables_with_llm(container)

        assert bg.data["table_summary"] == "s"

    @pytest.mark.asyncio
    async def test_children_neither_list_nor_blockgroup_children(self):
        proc = _make_processor()
        bg = MagicMock()
        bg.type = GroupType.TABLE
        bg.index = 0
        bg.data = {"table_markdown": "| A |"}
        bg.table_metadata = None
        bg.description = None
        bg.children = {"unexpected": True}
        container = MagicMock()
        container.block_groups = [bg]
        container.blocks = []
        response = MagicMock(summary="s", headers=["A"])

        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=response,
        ):
            await proc._enhance_tables_with_llm(container)

        assert bg.data["table_summary"] == "s"


# ---------------------------------------------------------------------------
# BlockGroup images / HTML processing
# ---------------------------------------------------------------------------


class TestBlockGroupHtmlAndImages:
    @pytest.mark.asyncio
    async def test_process_blockgroup_images_falsy_base64_skipped(self):
        from app.config.constants.arangodb import ExtensionTypes

        proc = _make_processor()
        md_parser = MagicMock()
        md_parser.extract_and_replace_images = MagicMock(
            return_value=(
                "md",
                [{"url": "http://x", "new_alt_text": "img1"}],
            )
        )
        image_parser = MagicMock()
        image_parser.urls_to_base64 = AsyncMock(return_value=[None])
        proc.parsers = {
            ExtensionTypes.MD.value: md_parser,
            ExtensionTypes.PNG.value: image_parser,
        }

        modified, caption_map = await proc._process_blockgroup_images("# hi", 0)
        assert modified == "md"
        assert caption_map == {}

    @pytest.mark.asyncio
    async def test_process_single_blockgroup_html_invalid_data(self):
        proc = _make_processor()
        bg = BlockGroup(index=0, type=GroupType.TEXT_SECTION, data=None, format=DataFormat.HTML)
        with pytest.raises(ValueError, match="no valid HTML data"):
            await proc._process_single_blockgroup_html(bg, "rec")

    @pytest.mark.asyncio
    async def test_process_single_blockgroup_html_missing_parser(self):
        proc = _make_processor()
        proc.parsers = {}
        bg = BlockGroup(
            index=0, type=GroupType.TEXT_SECTION, data="<p>x</p>", format=DataFormat.HTML
        )
        with pytest.raises(ValueError, match="HTML parser is not configured"):
            await proc._process_single_blockgroup_html(bg, "rec")

    @pytest.mark.asyncio
    async def test_process_single_blockgroup_html_images_and_parse(self):
        from app.config.constants.arangodb import ExtensionTypes

        proc = _make_processor()
        html_parser = MagicMock()
        html_parser.clean_html = MagicMock(side_effect=lambda x: x)
        html_parser.extract_and_replace_images = MagicMock(
            return_value=(
                "<p>x</p>",
                [
                    {"url": "http://a", "new_alt_text": "a1"},
                    {"url": "http://b", "new_alt_text": "b1"},
                ],
            )
        )
        html_parser.parse_to_blocks = AsyncMock(
            return_value=BlocksContainer(
                blocks=[
                    Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="x")
                ],
                block_groups=[],
            )
        )
        image_parser = MagicMock()
        image_parser.urls_to_base64 = AsyncMock(return_value=["b64", ""])
        proc.parsers = {
            ExtensionTypes.HTML.value: html_parser,
            ExtensionTypes.PNG.value: image_parser,
        }
        bg = BlockGroup(
            index=3,
            type=GroupType.TEXT_SECTION,
            data="<p>x</p>",
            format=DataFormat.HTML,
            name="bg",
        )

        new_bgs, new_blocks = await proc._process_single_blockgroup_html(bg, "rec")
        assert len(new_blocks) == 1
        html_parser.parse_to_blocks.assert_awaited_once()
        call_kwargs = html_parser.parse_to_blocks.await_args.kwargs
        assert call_kwargs["caption_map"] == {"a1": "b64"}

    @pytest.mark.asyncio
    async def test_process_blockgroups_routes_html_format(self):
        proc = _make_processor()
        md_parser = MagicMock()
        proc.parsers = {"md": md_parser}
        bg = BlockGroup(
            index=0,
            type=GroupType.TEXT_SECTION,
            data="<p>hi</p>",
            format=DataFormat.HTML,
            requires_processing=True,
        )
        container = BlocksContainer(blocks=[], block_groups=[bg])

        with patch.object(
            proc,
            "_process_single_blockgroup_html",
            new_callable=AsyncMock,
            return_value=([], [Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="hi")]),
        ) as mock_html, patch.object(
            proc, "_process_single_blockgroup", new_callable=AsyncMock
        ) as mock_md:
            result = await proc._process_blockgroups(container, "rec")

        mock_html.assert_awaited_once()
        mock_md.assert_not_awaited()
        assert len(result.blocks) == 1


# ---------------------------------------------------------------------------
# _build_updated_blocks_container edges
# ---------------------------------------------------------------------------


class TestBuildUpdatedBlocksContainerGaps:
    def test_orphan_blocks_and_missing_shift_and_unprocessed_bg(self):
        proc = _make_processor()
        orphan = Block(
            index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="o", parent_index=None
        )
        b1 = Block(
            index=1, type=BlockType.TEXT, format=DataFormat.TXT, data="a", parent_index=0
        )
        b2 = Block(
            index=2, type=BlockType.TEXT, format=DataFormat.TXT, data="b", parent_index=0
        )
        bg0 = BlockGroup(index=0, type=GroupType.TEXT_SECTION, requires_processing=False)
        bg0.children = BlockGroupChildren.from_indices(block_group_indices=[5])  # missing from shift map
        bg1 = BlockGroup(index=1, type=GroupType.TEXT_SECTION, requires_processing=False)
        # processed path with children already set
        bg2 = BlockGroup(index=2, type=GroupType.TEXT_SECTION, requires_processing=True)
        bg2.children = BlockGroupChildren()

        container = BlocksContainer(blocks=[orphan, b1, b2], block_groups=[bg0, bg1, bg2])
        processing_results = {
            2: (
                [],
                [Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="n")],
            )
        }
        index_shift_map = {0: 0, 1: 0, 2: 0}

        result = proc._build_updated_blocks_container(
            container,
            [bg0, bg1, bg2],
            [],
            processing_results,
            index_shift_map,
            3,
        )
        # orphan dropped from parent map; bg1 has no existing blocks and not processed
        assert any(b.data == "n" for b in result.blocks)
        assert any(b.data == "a" for b in result.blocks)


# ---------------------------------------------------------------------------
# HTML / MD document edges
# ---------------------------------------------------------------------------


class TestHtmlMdDocumentGaps:
    @pytest.mark.asyncio
    async def test_empty_html_marks_empty(self):
        proc = _make_processor()
        proc._mark_record = AsyncMock()
        events = await _collect(
            proc.process_html_document(
                "a.html", "r1", "1", "web", "o1", b"   \n", "vr1"
            )
        )
        proc._mark_record.assert_awaited_once_with("r1", ProgressStatus.EMPTY)
        assert any(e.event == IndexingEvent.PARSING_COMPLETE for e in events)
        assert any(e.event == IndexingEvent.INDEXING_COMPLETE for e in events)

    @pytest.mark.asyncio
    async def test_empty_html_mark_record_dpe_reraise(self):
        proc = _make_processor()
        proc._mark_record = AsyncMock(
            side_effect=DocumentProcessingError("gone", doc_id="r1")
        )
        with pytest.raises(DocumentProcessingError, match="gone"):
            await _collect(
                proc.process_html_document(
                    "a.html", "r1", "1", "web", "o1", "", "vr1"
                )
            )

    @pytest.mark.asyncio
    async def test_empty_html_mark_record_generic_wrap(self):
        proc = _make_processor()
        proc._mark_record = AsyncMock(side_effect=RuntimeError("db down"))
        with pytest.raises(DocumentProcessingError, match="Error updating record status"):
            await _collect(
                proc.process_html_document(
                    "a.html", "r1", "1", "web", "o1", "  ", "vr1"
                )
            )

    @pytest.mark.asyncio
    async def test_html_images_base64_map(self):
        from app.config.constants.arangodb import ExtensionTypes

        proc = _make_processor()
        html_parser = MagicMock()
        html_parser.clean_html = MagicMock(side_effect=lambda x: x)
        html_parser.replace_relative_image_urls = MagicMock(side_effect=lambda x: x)
        html_parser.extract_and_replace_images = MagicMock(
            return_value=(
                "<p>x</p>",
                [
                    {"url": "http://a", "new_alt_text": "a1"},
                    {"url": "http://b", "new_alt_text": "b1"},
                ],
            )
        )
        html_parser.parse_to_blocks = AsyncMock(
            return_value=BlocksContainer(blocks=[], block_groups=[])
        )
        image_parser = MagicMock()
        image_parser.urls_to_base64 = AsyncMock(return_value=["b64a", None])
        proc.parsers = {
            ExtensionTypes.HTML.value: html_parser,
            ExtensionTypes.PNG.value: image_parser,
        }
        proc.graph_provider.get_document = AsyncMock(
            return_value=_record(recordName="a.html")
        )

        with patch("app.events.processor.IndexingPipeline") as MockPipeline:
            MockPipeline.return_value.apply = AsyncMock()
            await _collect(
                proc.process_html_document(
                    "a.html", "r1", "1", "web", "o1", b"<p>x</p>", "vr1"
                )
            )

        kwargs = html_parser.parse_to_blocks.await_args.kwargs
        assert kwargs["caption_map"] == {"a1": "b64a"}

    @pytest.mark.asyncio
    async def test_md_falsy_base64_skipped(self):
        from app.config.constants.arangodb import ExtensionTypes

        proc = _make_processor()
        md_parser = MagicMock()
        md_parser.extract_and_replace_images = MagicMock(
            return_value=(
                "md",
                [{"url": "http://a", "new_alt_text": "a1"}],
            )
        )
        md_parser.parse_to_blocks = AsyncMock(
            return_value=BlocksContainer(blocks=[], block_groups=[])
        )
        image_parser = MagicMock()
        image_parser.urls_to_base64 = AsyncMock(return_value=[""])
        proc.parsers = {
            ExtensionTypes.MD.value: md_parser,
            ExtensionTypes.PNG.value: image_parser,
        }
        proc.graph_provider.get_document = AsyncMock(
            return_value=_record(recordName="a.md")
        )

        with patch("app.events.processor.IndexingPipeline") as MockPipeline:
            MockPipeline.return_value.apply = AsyncMock()
            await _collect(
                proc.process_md_document(
                    "a.md", "r1", "# hi\n![x](http://a)", "vr1"
                )
            )

        kwargs = md_parser.parse_to_blocks.await_args.kwargs
        assert kwargs["caption_map"] is None


# ---------------------------------------------------------------------------
# Delimited UnicodeDecodeError continue
# ---------------------------------------------------------------------------


class TestDelimitedUnicodeDecode:
    @pytest.mark.asyncio
    async def test_utf8_fails_latin1_succeeds(self):
        from app.config.constants.arangodb import ExtensionTypes

        proc = _make_processor()
        parser = MagicMock()
        parser.read_raw_rows = MagicMock(return_value=[["café"]])
        parser.find_tables_in_csv = MagicMock(return_value=[[["café"]]])
        parser.get_blocks_from_csv_with_multiple_tables = AsyncMock(
            return_value=BlocksContainer(blocks=[], block_groups=[])
        )
        proc.parsers = {ExtensionTypes.CSV.value: parser}
        proc.graph_provider.get_document = AsyncMock(
            return_value=_record(recordName="a.csv")
        )

        # Invalid utf-8 start byte that latin1 accepts
        payload = b"\xff\xfe caf\xe9"

        class DecodingBytes(bytes):
            def decode(self, encoding="utf-8", errors="strict"):
                if encoding == "utf-8":
                    raise UnicodeDecodeError("utf-8", self, 0, 1, "bad")
                return bytes.decode(self, encoding, errors)

        with patch(
            "app.events.processor.get_llm_for_role", new_callable=AsyncMock
        ) as mock_llm, patch("app.events.processor.IndexingPipeline") as MockPipeline:
            mock_llm.return_value = (MagicMock(), {})
            MockPipeline.return_value.apply = AsyncMock()
            events = await _collect(
                proc.process_delimited_document(
                    "a.csv", "r1", DecodingBytes(payload), "vr1"
                )
            )

        assert any(e.event == IndexingEvent.INDEXING_COMPLETE for e in events)


# ---------------------------------------------------------------------------
# process_structured_document
# ---------------------------------------------------------------------------


class TestProcessStructuredDocument:
    @pytest.mark.asyncio
    async def test_unknown_extension_marks_failed(self):
        proc = _make_processor()
        proc.parsers = {}
        proc._mark_record = AsyncMock()
        events = await _collect(
            proc.process_structured_document("a.json", "r1", b"{}", "vr1", "json")
        )
        proc._mark_record.assert_awaited_once_with("r1", ProgressStatus.FAILED)
        assert any(e.event == IndexingEvent.PARSING_COMPLETE for e in events)
        assert any(e.event == IndexingEvent.INDEXING_COMPLETE for e in events)

    @pytest.mark.asyncio
    async def test_dict_list_str_coercion_and_empty(self):
        proc = _make_processor()
        parser = MagicMock()
        parser.parse = AsyncMock(
            return_value=SimpleNamespace(
                block_container=BlocksContainer(blocks=[], block_groups=[])
            )
        )
        proc.parsers = {"json": parser}
        proc._mark_record = AsyncMock()

        for content in ({"a": 1}, [{"a": 1}], '{"a":1}'):
            parser.parse.reset_mock()
            proc._mark_record.reset_mock()
            events = await _collect(
                proc.process_structured_document("a.json", "r1", content, "vr1", "json")
            )
            parser.parse.assert_awaited_once()
            assert isinstance(parser.parse.await_args.args[0], bytes)
            proc._mark_record.assert_awaited_once_with("r1", ProgressStatus.EMPTY)
            assert any(e.event == IndexingEvent.INDEXING_COMPLETE for e in events)

    @pytest.mark.asyncio
    async def test_happy_path(self):
        proc = _make_processor()
        parser = MagicMock()
        parser.parse = AsyncMock(
            return_value=SimpleNamespace(
                block_container=BlocksContainer(
                    blocks=[
                        Block(
                            index=0,
                            type=BlockType.TEXT,
                            format=DataFormat.TXT,
                            data="x",
                        )
                    ],
                    block_groups=[],
                )
            )
        )
        proc.parsers = {"yaml": parser}
        proc.graph_provider.get_document = AsyncMock(
            return_value=_record(recordName="a.yaml")
        )

        with patch("app.events.processor.IndexingPipeline") as MockPipeline:
            MockPipeline.return_value.apply = AsyncMock()
            events = await _collect(
                proc.process_structured_document(
                    "a.yaml", "r1", b"a: 1", "vr1", "yaml"
                )
            )

        MockPipeline.return_value.apply.assert_awaited_once()
        assert any(e.event == IndexingEvent.INDEXING_COMPLETE for e in events)

    @pytest.mark.asyncio
    async def test_record_missing_raises_dpe(self):
        proc = _make_processor()
        parser = MagicMock()
        parser.parse = AsyncMock(
            return_value=SimpleNamespace(
                block_container=BlocksContainer(
                    blocks=[
                        Block(
                            index=0,
                            type=BlockType.TEXT,
                            format=DataFormat.TXT,
                            data="x",
                        )
                    ],
                    block_groups=[],
                )
            )
        )
        proc.parsers = {"json": parser}
        proc.graph_provider.get_document = AsyncMock(return_value=None)

        with pytest.raises(DocumentProcessingError, match="Record not found"):
            await _collect(
                proc.process_structured_document("a.json", "r1", b"{}", "vr1", "json")
            )

    @pytest.mark.asyncio
    async def test_indexing_error_reraise(self):
        proc = _make_processor()
        parser = MagicMock()
        parser.parse = AsyncMock(side_effect=IndexingError("struct fail", record_id="r1"))
        proc.parsers = {"json": parser}

        with pytest.raises(IndexingError, match="struct fail"):
            await _collect(
                proc.process_structured_document("a.json", "r1", b"{}", "vr1", "json")
            )

    @pytest.mark.asyncio
    async def test_generic_exception_wrapped(self):
        proc = _make_processor()
        parser = MagicMock()
        parser.parse = AsyncMock(side_effect=RuntimeError("boom"))
        proc.parsers = {"json": parser}

        with pytest.raises(DocumentProcessingError, match="Failed to process document"):
            await _collect(
                proc.process_structured_document("a.json", "r1", b"{}", "vr1", "json")
            )
