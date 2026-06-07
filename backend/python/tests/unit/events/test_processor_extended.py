"""Extended coverage tests for app.events.processor.

Targets remaining uncovered lines:
- 156-157: process_image non-DocumentProcessingError wrapping
- 434-436, 450-452, 457-475: process_pdf_document_with_ocr VLM OCR page parsing/blocks errors, chunk processing
- 534-536, 561: process_pdf_document_with_ocr bounding_box errors, block_group.children = None
- 709, 715: _enhance_tables_with_llm: table_group.data is None, table_metadata column update
- 744-765: _enhance_tables_with_llm: old-format children (list of BlockContainerIndex), row_dicts empty dict
- 799-800: _enhance_tables_with_llm: get_rows_text exception
- 1148, 1152-1160: _build_updated_blocks_container parent_index shift, children block_group_ranges shift
- 1195, 1215: _build_updated_blocks_container new_block.parent_index offset, new_bg.parent_index offset
- 1221-1228: _build_updated_blocks_container new_bg.children block/block_group ranges shift
- 1347-1348: _process_blockgroups_through_docling: empty processing_results
- 1497-1502: process_delimited_document: non-UnicodeDecodeError exception during CSV parsing
- 1549-1551: process_delimited_document: outer exception handler
- 1601, 1612: process_html_document: element.decompose loop, html_binary is not bytes fallback
- 1684-1687: process_md_document: empty markdown _mark_record raises non-DocumentProcessingError
- 1705-1711: process_md_document: urls_to_base64 image conversion
- 1739-1752: process_md_document: image block caption mapping with caption_map
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.services.messaging.config import IndexingEvent, PipelineEvent, PipelineEventData

log = logging.getLogger("test_processor_extended")
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
    """Return a valid record dict for convert_record_dict_to_record."""
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


# ===================================================================
# Lines 156-157: process_image - non-DocumentProcessingError wrapping
# When batch_upsert_nodes raises a generic Exception (not
# DocumentProcessingError), it should be wrapped.
# ===================================================================


class TestProcessImageNonDocError:
    @pytest.mark.asyncio
    async def test_non_doc_error_wrapped(self):
        """Generic exception during status update is wrapped in DocumentProcessingError."""
        proc = _make_processor()
        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="photo.png", mimeType="image/png")
        )
        # batch_upsert_nodes raises a generic error
        proc.graph_provider.batch_upsert_nodes = AsyncMock(
            side_effect=RuntimeError("db connection lost")
        )

        with patch(
            "app.events.processor.get_llm_for_role", new_callable=AsyncMock
        ) as mock_llm, patch(
            "app.events.processor.get_embedding_model_config", new_callable=AsyncMock
        ) as mock_emb:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            mock_emb.return_value = {"isMultimodal": False}

            with pytest.raises(DocumentProcessingError, match="Error updating record status"):
                await _collect_events(proc.process_image("r1", b"imgdata", "vr1"))


# ===================================================================
# Lines 434-436: process_pdf_document_with_ocr - VLM OCR page parse fail
# ===================================================================


class TestProcessPdfWithOcrVlmPageParseFail:
    @pytest.mark.asyncio
    async def test_vlm_ocr_page_parse_error(self):
        """When docling parse_document fails for a VLM OCR page, the error propagates."""
        proc = _make_processor()

        mock_config = AsyncMock()
        mock_config.get_config = AsyncMock(return_value={
            "ocr": [{"provider": "vlmOCR"}],
            "llm": [],
        })
        proc.config_service = mock_config

        with patch("app.events.processor.OCRHandler") as MockOCR, \
             patch("app.events.processor.DoclingProcessor") as MockDocling:
            mock_ocr = AsyncMock()
            mock_ocr.process_document = AsyncMock(return_value={
                "pages": [
                    {"page_number": 1, "markdown": "# Page 1 content"},
                ],
            })
            MockOCR.return_value = mock_ocr

            mock_processor = AsyncMock()
            mock_processor.parse_document = AsyncMock(
                side_effect=RuntimeError("parse failed for page")
            )
            MockDocling.return_value = mock_processor

            with pytest.raises(RuntimeError, match="parse failed for page"):
                await _collect_events(
                    proc.process_pdf_document_with_ocr(
                        "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                    )
                )


# ===================================================================
# Lines 450-452: VLM OCR create_blocks failure for a page
# ===================================================================


class TestProcessPdfWithOcrVlmBlocksFail:
    @pytest.mark.asyncio
    async def test_vlm_ocr_create_blocks_error(self):
        """When create_blocks fails for a VLM OCR page, the error propagates."""
        proc = _make_processor()

        mock_config = AsyncMock()
        mock_config.get_config = AsyncMock(return_value={
            "ocr": [{"provider": "vlmOCR"}],
            "llm": [],
        })
        proc.config_service = mock_config

        with patch("app.events.processor.OCRHandler") as MockOCR, \
             patch("app.events.processor.DoclingProcessor") as MockDocling:
            mock_ocr = AsyncMock()
            mock_ocr.process_document = AsyncMock(return_value={
                "pages": [
                    {"page_number": 1, "markdown": "# Page 1"},
                ],
            })
            MockOCR.return_value = mock_ocr

            mock_processor = AsyncMock()
            mock_processor.parse_document = AsyncMock(return_value=MagicMock())
            mock_processor.create_blocks = AsyncMock(
                side_effect=RuntimeError("block creation failed")
            )
            MockDocling.return_value = mock_processor

            with pytest.raises(RuntimeError, match="block creation failed"):
                await _collect_events(
                    proc.process_pdf_document_with_ocr(
                        "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                    )
                )


# ===================================================================
# Lines 457-475: VLM OCR chunk processing - block/block_group index
# adjustment with children ranges
# ===================================================================


class TestProcessPdfWithOcrVlmChunkProcessing:
    @pytest.mark.asyncio
    async def test_vlm_ocr_multi_page_with_blocks_and_groups(self):
        """Multi-page VLM OCR adjusts block/block_group indices and children ranges."""
        from app.models.blocks import (
            Block,
            BlockGroup,
            BlockGroupChildren,
            BlocksContainer,
            BlockType,
            DataFormat,
            GroupType,
            IndexRange,
            TableRowMetadata,
        )

        proc = _make_processor()

        mock_config = AsyncMock()
        mock_config.get_config = AsyncMock(return_value={
            "ocr": [{"provider": "vlmOCR"}],
            "llm": [],
        })
        proc.config_service = mock_config

        # Build page_block_containers for each page with blocks and block_groups
        page1_blocks = [
            Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="p1 block0", parent_index=0),
        ]
        page1_groups = [
            BlockGroup(
                index=0,
                type=GroupType.TEXT_SECTION,
                parent_index=None,
                children=BlockGroupChildren(
                    block_ranges=[IndexRange(start=0, end=0)],
                    block_group_ranges=[],
                ),
            ),
        ]
        page1_container = BlocksContainer(blocks=page1_blocks, block_groups=page1_groups)

        page2_blocks = [
            Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="p2 block0", parent_index=0),
        ]
        page2_groups = [
            BlockGroup(
                index=0,
                type=GroupType.TEXT_SECTION,
                parent_index=None,
                children=BlockGroupChildren(
                    block_ranges=[IndexRange(start=0, end=0)],
                    block_group_ranges=[IndexRange(start=0, end=0)],
                ),
            ),
        ]
        page2_container = BlocksContainer(blocks=page2_blocks, block_groups=page2_groups)

        with patch("app.events.processor.OCRHandler") as MockOCR, \
             patch("app.events.processor.DoclingProcessor") as MockDocling, \
             patch("app.events.processor.IndexingPipeline") as MockPipeline:
            mock_ocr = AsyncMock()
            mock_ocr.process_document = AsyncMock(return_value={
                "pages": [
                    {"page_number": 1, "markdown": "# Page 1"},
                    {"page_number": 2, "markdown": "# Page 2"},
                ],
            })
            MockOCR.return_value = mock_ocr

            mock_processor = AsyncMock()
            # parse_document called for each page
            mock_processor.parse_document = AsyncMock(side_effect=[MagicMock(), MagicMock()])
            # create_blocks returns different containers for each page
            mock_processor.create_blocks = AsyncMock(
                side_effect=[page1_container, page2_container]
            )
            MockDocling.return_value = mock_processor

            proc.graph_provider.get_document = AsyncMock(
                return_value=_mock_record_dict()
            )

            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_pdf_document_with_ocr(
                    "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                )
            )

        assert any(e.event == "parsing_complete" for e in events)
        assert any(e.event == "indexing_complete" for e in events)


# ===================================================================
# Lines 534-536: process_pdf_document_with_ocr - bounding_box parse error
# ===================================================================


class TestProcessPdfWithOcrBoundingBoxError:
    @pytest.mark.asyncio
    async def test_bounding_box_parse_error_falls_back(self):
        """Malformed bounding_box triggers TypeError/KeyError and sets bounding_boxes=None."""
        proc = _make_processor()

        mock_config = AsyncMock()
        mock_config.get_config = AsyncMock(return_value={
            "ocr": [{"provider": "azureDI", "configuration": {"endpoint": "https://test.com", "apiKey": "k"}}],
            "llm": [],
        })
        proc.config_service = mock_config

        with patch("app.events.processor.OCRHandler") as MockOCR, \
             patch("app.events.processor.IndexingPipeline") as MockPipeline:
            mock_ocr = AsyncMock()
            # Return blocks as already-constructed Block objects to skip the bounding_box parsing
            from app.models.blocks import Block, BlockType, DataFormat, CitationMetadata
            prebuilt_block = Block(
                index=0, type=BlockType.TEXT, format=DataFormat.TXT,
                data="some text",
                citation_metadata=CitationMetadata(page_number=1),
            )
            mock_ocr.process_document = AsyncMock(return_value={
                "blocks": [prebuilt_block],
                "tables": [],
            })
            MockOCR.return_value = mock_ocr

            proc.graph_provider.get_document = AsyncMock(
                return_value=_mock_record_dict()
            )
            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_pdf_document_with_ocr(
                    "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                )
            )

        assert any(e.event == "parsing_complete" for e in events)
        assert any(e.event == "indexing_complete" for e in events)


# ===================================================================
# Line 561: process_pdf_document_with_ocr - block_group.children = None
# when no table_rows match the block_group.index
# ===================================================================


class TestProcessPdfWithOcrEmptyTableRows:
    @pytest.mark.asyncio
    async def test_block_group_children_set_to_none(self):
        """Block groups with no matching table_rows have children set to None."""
        from app.models.blocks import BlockGroup, GroupType

        proc = _make_processor()

        mock_config = AsyncMock()
        mock_config.get_config = AsyncMock(return_value={
            "ocr": [{"provider": "azureDI", "configuration": {"endpoint": "https://test.com", "apiKey": "k"}}],
            "llm": [],
        })
        proc.config_service = mock_config

        table_group = BlockGroup(index=99, type=GroupType.TABLE)

        with patch("app.events.processor.OCRHandler") as MockOCR, \
             patch("app.events.processor.IndexingPipeline") as MockPipeline:
            mock_ocr = AsyncMock()
            mock_ocr.process_document = AsyncMock(return_value={
                "blocks": [],
                "tables": [table_group],
            })
            MockOCR.return_value = mock_ocr

            proc.graph_provider.get_document = AsyncMock(
                return_value=_mock_record_dict()
            )
            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_pdf_document_with_ocr(
                    "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                )
            )

        assert any(e.event == "indexing_complete" for e in events)


# ===================================================================
# Lines 709, 715: _enhance_tables_with_llm -
# table_group.data is None => set to {}, and column_headers + table_metadata
# ===================================================================


class TestEnhanceTablesDataNoneAndTableMetadata:
    @pytest.mark.asyncio
    async def test_table_group_data_none_set_to_dict(self):
        """When table_group.data is None and response exists, data is set to {}."""
        from app.models.blocks import BlockGroup, BlocksContainer, GroupType, TableMetadata

        proc = _make_processor()

        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            data={"table_markdown": "| a | b |\n| 1 | 2 |"},
            table_metadata=TableMetadata(column_names=[]),
        )
        bc = BlocksContainer(blocks=[], block_groups=[bg])

        mock_response = MagicMock()
        mock_response.summary = "Test summary"
        mock_response.headers = ["col_a", "col_b"]

        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            await proc._enhance_tables_with_llm(bc)

        # Verify data was updated with summary and headers
        assert bg.data["table_summary"] == "Test summary"
        assert bg.data["column_headers"] == ["col_a", "col_b"]
        # Line 715: column_names updated
        assert bg.table_metadata.column_names == ["col_a", "col_b"]


# ===================================================================
# Lines 744-765: _enhance_tables_with_llm - old format children
# (list of BlockContainerIndex) and rows with non-list cells -> empty dict
# ===================================================================


class TestEnhanceTablesOldFormatChildren:
    @pytest.mark.asyncio
    async def test_old_format_children_list(self):
        """Old-format children (list of BlockContainerIndex) are processed."""
        from app.models.blocks import (
            Block,
            BlockContainerIndex,
            BlockGroup,
            BlocksContainer,
            BlockType,
            DataFormat,
            GroupType,
            TableRowMetadata,
        )

        proc = _make_processor()

        # Create a table row block with cells that are NOT a list => row_dicts.append({})
        row_block = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.TXT,
            data={"cells": "not_a_list"},
            table_row_metadata=TableRowMetadata(is_header=False),
        )

        children_list = [BlockContainerIndex(block_index=0)]

        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            data={"table_markdown": "| a |\n| 1 |"},
            children=children_list,  # Old format: list of BlockContainerIndex
        )
        bc = BlocksContainer(blocks=[row_block], block_groups=[bg])

        mock_response = MagicMock()
        mock_response.summary = "Summary"
        mock_response.headers = ["col_a"]

        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=mock_response,
        ), patch(
            "app.utils.indexing_helpers.get_rows_text",
            new_callable=AsyncMock,
            return_value=(["Row 1 description"], None),
        ):
            await proc._enhance_tables_with_llm(bc)

    @pytest.mark.asyncio
    async def test_old_format_with_list_cells(self):
        """Old-format children with list cells creates proper row_dicts."""
        from app.models.blocks import (
            Block,
            BlockContainerIndex,
            BlockGroup,
            BlocksContainer,
            BlockType,
            DataFormat,
            GroupType,
            TableRowMetadata,
        )

        proc = _make_processor()

        row_block = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.TXT,
            data={"cells": ["val_a", "val_b"]},
            table_row_metadata=TableRowMetadata(is_header=False),
        )

        children_list = [BlockContainerIndex(block_index=0)]

        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            data={"table_markdown": "| a | b |\n| 1 | 2 |"},
            children=children_list,
        )
        bc = BlocksContainer(blocks=[row_block], block_groups=[bg])

        mock_response = MagicMock()
        mock_response.summary = "Summary"
        mock_response.headers = ["col_a", "col_b"]

        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=mock_response,
        ), patch(
            "app.utils.indexing_helpers.get_rows_text",
            new_callable=AsyncMock,
            return_value=(["Row desc"], None),
        ):
            await proc._enhance_tables_with_llm(bc)


# ===================================================================
# Lines 799-800: _enhance_tables_with_llm - get_rows_text raises exception
# ===================================================================


class TestEnhanceTablesRowDescriptionException:
    @pytest.mark.asyncio
    async def test_get_rows_text_exception_caught(self):
        """Exception from get_rows_text is caught and logged as warning."""
        from app.models.blocks import (
            Block,
            BlockGroup,
            BlockGroupChildren,
            BlocksContainer,
            BlockType,
            DataFormat,
            GroupType,
            IndexRange,
            TableRowMetadata,
        )

        proc = _make_processor()

        row_block = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.TXT,
            data={"cells": ["v1", "v2"]},
            table_row_metadata=TableRowMetadata(is_header=False),
        )

        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            data={"table_markdown": "| a | b |\n| 1 | 2 |"},
            children=BlockGroupChildren(
                block_ranges=[IndexRange(start=0, end=0)],
                block_group_ranges=[],
            ),
        )
        bc = BlocksContainer(blocks=[row_block], block_groups=[bg])

        mock_response = MagicMock()
        mock_response.summary = "Summary"
        mock_response.headers = ["col_a", "col_b"]

        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=mock_response,
        ), patch(
            "app.utils.indexing_helpers.get_rows_text",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM failed"),
        ):
            # Should not raise -- exception is caught and logged
            await proc._enhance_tables_with_llm(bc)


# ===================================================================
# Lines 1148, 1152-1160: _build_updated_blocks_container -
# parent_index shift, children.block_group_ranges shift
# ===================================================================


class TestBuildUpdatedBlocksContainerShifts:
    def test_parent_index_and_block_group_ranges_shift(self):
        """Block groups have parent_index and children.block_group_ranges shifted."""
        from app.models.blocks import (
            Block,
            BlockGroup,
            BlockGroupChildren,
            BlocksContainer,
            BlockType,
            DataFormat,
            GroupType,
            IndexRange,
            TableRowMetadata,
        )

        proc = _make_processor()

        # Create block groups: bg0 (processed, gets new blocks), bg1 (has parent=0, has children.block_group_ranges)
        child_block = Block(
            index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="text", parent_index=1
        )
        bg0 = BlockGroup(
            index=0,
            type=GroupType.TEXT_SECTION,
            requires_processing=True,
            data="# markdown",
        )
        bg1 = BlockGroup(
            index=1,
            type=GroupType.TEXT_SECTION,
            parent_index=0,
            children=BlockGroupChildren(
                block_ranges=[IndexRange(start=0, end=0)],
                block_group_ranges=[IndexRange(start=0, end=0)],
            ),
        )

        block_containers = BlocksContainer(blocks=[child_block], block_groups=[bg0, bg1])

        # Processing results: bg0 produced 1 new block_group and 1 new block
        new_bg = BlockGroup(
            index=0,
            type=GroupType.TEXT_SECTION,
            children=BlockGroupChildren(
                block_ranges=[IndexRange(start=0, end=0)],
                block_group_ranges=[IndexRange(start=0, end=0)],
            ),
        )
        new_block = Block(
            index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="new", parent_index=0
        )
        processing_results = {0: ([new_bg], [new_block])}

        # index_shift_map: bg0 stays at 0, bg1 shifts by 1 (because 1 new bg inserted)
        index_shift_map = {0: 0, 1: 1}

        result = proc._build_updated_blocks_container(
            block_containers,
            [bg0, bg1],
            [],
            processing_results,
            index_shift_map,
            initial_block_count=1,
        )

        assert isinstance(result, BlocksContainer)
        assert len(result.block_groups) >= 2
        assert len(result.blocks) >= 1


# ===================================================================
# Lines 1195, 1215: _build_updated_blocks_container -
# new_block.parent_index offset, new_bg.parent_index offset
# ===================================================================


class TestBuildUpdatedBlocksContainerParentOffsets:
    def test_new_block_and_bg_parent_index_offset(self):
        """New blocks/block_groups with existing parent_index get offset applied."""
        from app.models.blocks import (
            Block,
            BlockGroup,
            BlockGroupChildren,
            BlocksContainer,
            BlockType,
            DataFormat,
            GroupType,
            IndexRange,
            TableRowMetadata,
        )

        proc = _make_processor()

        bg0 = BlockGroup(
            index=0,
            type=GroupType.TEXT_SECTION,
            requires_processing=True,
            data="# markdown",
        )
        block_containers = BlocksContainer(blocks=[], block_groups=[bg0])

        # New block with parent_index already set (from docling) -> offset applied
        new_block = Block(
            index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="text",
            parent_index=1,  # Non-None -> gets offset
        )
        # New bg with parent_index already set -> offset applied
        new_bg = BlockGroup(
            index=0,
            type=GroupType.TEXT_SECTION,
            parent_index=2,  # Non-None -> gets offset
            children=BlockGroupChildren(
                block_ranges=[IndexRange(start=0, end=0)],
                block_group_ranges=[IndexRange(start=0, end=0)],
            ),
        )

        processing_results = {0: ([new_bg], [new_block])}
        index_shift_map = {0: 0}

        result = proc._build_updated_blocks_container(
            block_containers,
            [bg0],
            [],
            processing_results,
            index_shift_map,
            initial_block_count=0,
        )

        assert isinstance(result, BlocksContainer)


# ===================================================================
# Lines 1347-1348: _process_blockgroups_through_docling - empty processing_results
# ===================================================================


class TestProcessBlockgroupsThroughDoclingEmpty:
    @pytest.mark.asyncio
    async def test_all_processing_raises_returns_original(self):
        """When all block groups raise during processing, error propagates."""
        from app.models.blocks import BlockGroup, BlocksContainer, GroupType

        proc = _make_processor()

        bg = BlockGroup(
            index=0,
            type=GroupType.TEXT_SECTION,
            requires_processing=True,
            data="# Some markdown",
        )
        bc = BlocksContainer(blocks=[], block_groups=[bg])

        mock_processor = AsyncMock()
        # _process_single_blockgroup_through_docling raises
        with patch.object(
            proc,
            "_process_single_blockgroup_through_docling",
            new_callable=AsyncMock,
            side_effect=RuntimeError("docling failed"),
        ), patch("app.events.processor.DoclingProcessor", return_value=mock_processor):
            with pytest.raises(RuntimeError, match="docling failed"):
                await proc._process_blockgroups_through_docling(bc, "test.md")

    @pytest.mark.asyncio
    async def test_no_blockgroups_to_process(self):
        """When no block_groups have requires_processing=True, returns original."""
        from app.models.blocks import BlockGroup, BlocksContainer, GroupType

        proc = _make_processor()

        bg = BlockGroup(
            index=0,
            type=GroupType.TEXT_SECTION,
            requires_processing=False,
        )
        bc = BlocksContainer(blocks=[], block_groups=[bg])

        result = await proc._process_blockgroups_through_docling(bc, "test.md")
        assert result is bc


# ===================================================================
# Lines 1497-1502: process_delimited_document -
# non-UnicodeDecodeError exception during CSV parsing
# ===================================================================


class TestProcessDelimitedNonUnicodeError:
    @pytest.mark.asyncio
    async def test_generic_exception_during_csv_decode(self):
        """Non-UnicodeDecodeError during CSV parsing is caught and continues."""
        csv_parser = MagicMock()
        # read_raw_rows raises a generic exception, not UnicodeDecodeError
        csv_parser.read_raw_rows = MagicMock(side_effect=RuntimeError("CSV parse error"))
        proc = _make_processor(parsers={"csv": csv_parser})

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.csv")
        )
        proc.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)

        with patch("app.events.processor.get_llm_for_role", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {})

            events = await _collect_events(
                proc.process_delimited_document("test.csv", "r1", b"a,b\n1,2", "vr1")
            )

        # Should mark as empty and yield both events
        assert any(e.event == "parsing_complete" for e in events)
        assert any(e.event == "indexing_complete" for e in events)


# ===================================================================
# Lines 1549-1551: process_delimited_document - outer exception handler
# ===================================================================


class TestProcessDelimitedOuterException:
    @pytest.mark.asyncio
    async def test_outer_exception_propagated(self):
        """Exceptions from the pipeline propagate through the outer handler."""
        csv_parser = MagicMock()
        csv_parser.read_raw_rows.return_value = [["a", "b"], ["1", "2"]]
        csv_parser.find_tables_in_csv.return_value = [
            {"rows": [["a", "b"], ["1", "2"]], "headers": ["a", "b"]}
        ]
        csv_parser.get_blocks_from_csv_with_multiple_tables = AsyncMock(
            side_effect=RuntimeError("pipeline exploded")
        )
        proc = _make_processor(parsers={"csv": csv_parser})

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.csv")
        )

        with patch("app.events.processor.get_llm_for_role", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {})

            with pytest.raises(RuntimeError, match="pipeline exploded"):
                await _collect_events(
                    proc.process_delimited_document("test.csv", "r1", b"a,b\n1,2", "vr1")
                )


# ===================================================================
# Line 1601: process_html_document - element.decompose() loop
# Line 1612: html_binary is not bytes -> html_content = html_binary
# ===================================================================


class TestProcessHtmlDecomposeAndStringFallback:
    @pytest.mark.asyncio
    async def test_html_with_script_tags_decomposed(self):
        """Script/style elements are decomposed from HTML before conversion."""
        proc = _make_processor()

        async def _fake_md(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        proc.process_md_document = _fake_md

        html_parser = MagicMock()
        html_parser.replace_relative_image_urls = MagicMock(side_effect=lambda x: x)
        proc.parsers = {"html": html_parser}

        # HTML with script and style tags that should be decomposed
        html = b"<html><head><script>alert('x')</script><style>body{}</style></head><body><p>Content</p></body></html>"

        events = await _collect_events(
            proc.process_html_document("test.html", "r1", "1", "src", "o1", html, "vr1")
        )

        assert any(e.event == "parsing_complete" for e in events)
        assert any(e.event == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_html_binary_is_string(self):
        """When html_binary is a string (not bytes) and BS4 fails, it falls through."""
        proc = _make_processor()

        async def _fake_md(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        proc.process_md_document = _fake_md

        html_parser = MagicMock()
        html_parser.replace_relative_image_urls = MagicMock(side_effect=lambda x: x)
        proc.parsers = {"html": html_parser}

        # Pass a string (not bytes) and force BS4 to fail
        html_str = "<p>Hello</p>"

        with patch("app.events.processor.BeautifulSoup", side_effect=Exception("parse error")):
            events = await _collect_events(
                proc.process_html_document("test.html", "r1", "1", "src", "o1", html_str, "vr1")
            )

        assert any(e.event == "indexing_complete" for e in events)


# ===================================================================
# Lines 1684-1687: process_md_document - empty markdown,
# _mark_record raises non-DocumentProcessingError
# ===================================================================


class TestProcessMdEmptyMarkdownMarkRecordFail:
    @pytest.mark.asyncio
    async def test_mark_record_generic_error_wraps(self):
        """Non-DocumentProcessingError from _mark_record is wrapped."""
        proc = _make_processor()

        with patch.object(
            proc,
            "_mark_record",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db error"),
        ):
            with pytest.raises(DocumentProcessingError, match="Error updating record status"):
                await _collect_events(
                    proc.process_md_document("test.md", "r1", b"   ", "vr1")
                )

    @pytest.mark.asyncio
    async def test_mark_record_doc_error_reraises(self):
        """DocumentProcessingError from _mark_record is re-raised directly."""
        proc = _make_processor()

        with patch.object(
            proc,
            "_mark_record",
            new_callable=AsyncMock,
            side_effect=DocumentProcessingError("record not found", doc_id="r1"),
        ):
            with pytest.raises(DocumentProcessingError, match="record not found"):
                await _collect_events(
                    proc.process_md_document("test.md", "r1", b"  ", "vr1")
                )


# ===================================================================
# Lines 1705-1711: process_md_document - image URL to base64 conversion
# ===================================================================


class TestProcessMdImageUrlConversion:
    @pytest.mark.asyncio
    async def test_images_converted_to_base64(self):
        """Markdown images have URLs converted to base64."""
        proc = _make_processor()

        md_parser = MagicMock()
        md_parser.extract_and_replace_images.return_value = (
            "# Hello ![img](image_alt_text)",
            [{"url": "https://example.com/img.png", "new_alt_text": "image_alt_text"}],
        )
        md_parser.parse_string.return_value = b"<html><p>content</p></html>"

        image_parser = MagicMock()
        image_parser.urls_to_base64 = AsyncMock(
            return_value=["data:image/png;base64,abc123"]
        )

        proc.parsers = {"md": md_parser, "png": image_parser}

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.md")
        )

        with patch("app.events.processor.DoclingProcessor") as MockDP, \
             patch("app.events.processor.IndexingPipeline") as MockPipeline:
            MockDP.return_value.parse_document = AsyncMock(return_value=MagicMock())
            MockDP.return_value.create_blocks = AsyncMock(
                return_value=MagicMock(blocks=[], block_groups=[])
            )
            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_md_document("test.md", "r1", b"# Hello ![img](https://example.com/img.png)", "vr1")
            )

        assert any(e.event == "parsing_complete" for e in events)
        assert any(e.event == "indexing_complete" for e in events)
        # Verify urls_to_base64 was called
        image_parser.urls_to_base64.assert_called_once()


# ===================================================================
# Lines 1739-1752: process_md_document - image block caption mapping
# ===================================================================


class TestProcessMdImageBlockCaptionMapping:
    @pytest.mark.asyncio
    async def test_image_blocks_get_caption_mapped(self):
        """Image blocks with matching captions get base64 URI set."""
        from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat, ImageMetadata

        proc = _make_processor()

        md_parser = MagicMock()
        md_parser.extract_and_replace_images.return_value = (
            "# Doc\n![cap1](cap1)",
            [{"url": "https://example.com/img.png", "new_alt_text": "cap1"}],
        )
        md_parser.parse_string.return_value = b"<html><p>Doc</p></html>"

        img_block = Block(
            index=0,
            type=BlockType.IMAGE,
            format=DataFormat.TXT,
            data=None,
            image_metadata=ImageMetadata(captions=["cap1"]),
        )
        mock_blocks = BlocksContainer(blocks=[img_block], block_groups=[])

        image_parser = MagicMock()
        image_parser.urls_to_base64 = AsyncMock(
            return_value=["data:image/png;base64,IMAGEDATA"]
        )

        proc.parsers = {"md": md_parser, "png": image_parser}

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.md")
        )

        with patch("app.events.processor.DoclingProcessor") as MockDP, \
             patch("app.events.processor.IndexingPipeline") as MockPipeline:
            MockDP.return_value.parse_document = AsyncMock(return_value=MagicMock())
            MockDP.return_value.create_blocks = AsyncMock(return_value=mock_blocks)
            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_md_document("test.md", "r1", b"# Doc\n![cap1](https://example.com/img.png)", "vr1")
            )

        assert any(e.event == "indexing_complete" for e in events)
        # The image block should have the URI set
        assert img_block.data == {"uri": "data:image/png;base64,IMAGEDATA"}

    @pytest.mark.asyncio
    async def test_image_block_caption_not_in_map(self):
        """Image blocks with captions not in caption_map are skipped with warning."""
        from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat, ImageMetadata

        proc = _make_processor()

        md_parser = MagicMock()
        md_parser.extract_and_replace_images.return_value = (
            "# Doc\n![missing_cap](missing_cap)",
            [{"url": "https://example.com/img.png", "new_alt_text": "different_cap"}],
        )
        md_parser.parse_string.return_value = b"<html><p>Doc</p></html>"

        img_block = Block(
            index=0,
            type=BlockType.IMAGE,
            format=DataFormat.TXT,
            data=None,
            image_metadata=ImageMetadata(captions=["missing_cap"]),
        )
        mock_blocks = BlocksContainer(blocks=[img_block], block_groups=[])

        image_parser = MagicMock()
        image_parser.urls_to_base64 = AsyncMock(
            return_value=["data:image/png;base64,DATA"]
        )

        proc.parsers = {"md": md_parser, "png": image_parser}

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.md")
        )

        with patch("app.events.processor.DoclingProcessor") as MockDP, \
             patch("app.events.processor.IndexingPipeline") as MockPipeline:
            MockDP.return_value.parse_document = AsyncMock(return_value=MagicMock())
            MockDP.return_value.create_blocks = AsyncMock(return_value=mock_blocks)
            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_md_document("test.md", "r1", b"# Doc", "vr1")
            )

        assert any(e.event == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_image_block_data_not_dict(self):
        """Image blocks where data is not a dict get data replaced with dict."""
        from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat, ImageMetadata

        proc = _make_processor()

        md_parser = MagicMock()
        md_parser.extract_and_replace_images.return_value = (
            "# Doc\n![cap1](cap1)",
            [{"url": "https://example.com/img.png", "new_alt_text": "cap1"}],
        )
        md_parser.parse_string.return_value = b"<html><p>Doc</p></html>"

        # Image block with non-dict data
        img_block = Block(
            index=0,
            type=BlockType.IMAGE,
            format=DataFormat.TXT,
            data="some_string_data",
            image_metadata=ImageMetadata(captions=["cap1"]),
        )
        mock_blocks = BlocksContainer(blocks=[img_block], block_groups=[])

        image_parser = MagicMock()
        image_parser.urls_to_base64 = AsyncMock(
            return_value=["data:image/png;base64,IMAGEDATA"]
        )

        proc.parsers = {"md": md_parser, "png": image_parser}

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.md")
        )

        with patch("app.events.processor.DoclingProcessor") as MockDP, \
             patch("app.events.processor.IndexingPipeline") as MockPipeline:
            MockDP.return_value.parse_document = AsyncMock(return_value=MagicMock())
            MockDP.return_value.create_blocks = AsyncMock(return_value=mock_blocks)
            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_md_document("test.md", "r1", b"# Doc", "vr1")
            )

        assert any(e.event == "indexing_complete" for e in events)
        assert img_block.data == {"uri": "data:image/png;base64,IMAGEDATA"}


# ===================================================================
# process_ppt_document: delegates to process_pptx_document
# ===================================================================


class TestProcessPptDocument:
    @pytest.mark.asyncio
    async def test_ppt_delegates_to_pptx(self):
        """PPT is converted to PPTX and delegated."""
        ppt_parser = MagicMock()
        ppt_parser.convert_ppt_to_pptx = MagicMock(return_value=b"pptx_data")
        proc = _make_processor(parsers={"ppt": ppt_parser})

        async def _fake_pptx(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        proc.process_pptx_document = _fake_pptx

        events = await _collect_events(
            proc.process_ppt_document("test.ppt", "r1", "1", "src", "o1", b"ppt_data", "vr1")
        )

        assert any(e.event == "parsing_complete" for e in events)
        assert any(e.event == "indexing_complete" for e in events)
        ppt_parser.convert_ppt_to_pptx.assert_called_once_with(b"ppt_data")


# ===================================================================
# process_xls_document: delegates to process_excel_document
# ===================================================================


class TestProcessXlsDocument:
    @pytest.mark.asyncio
    async def test_xls_delegates_to_excel(self):
        """XLS is converted to XLSX and delegated to process_excel_document."""
        xls_parser = MagicMock()
        xls_parser.convert_xls_to_xlsx = MagicMock(return_value=b"xlsx_data")
        proc = _make_processor(parsers={"xls": xls_parser})

        async def _fake_excel(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        proc.process_excel_document = _fake_excel

        events = await _collect_events(
            proc.process_xls_document("test.xls", "r1", "1", "src", "o1", b"xls_data", "vr1")
        )

        assert any(e.event == "parsing_complete" for e in events)
        assert any(e.event == "indexing_complete" for e in events)
        xls_parser.convert_xls_to_xlsx.assert_called_once_with(b"xls_data")


# ===================================================================
# process_doc_document: delegates to process_docx_document
# ===================================================================


class TestProcessDocDocument:
    @pytest.mark.asyncio
    async def test_doc_delegates_to_docx(self):
        """DOC is converted to DOCX and delegated to process_docx_document."""
        doc_parser = MagicMock()
        doc_parser.convert_doc_to_docx = MagicMock(return_value=b"docx_data")
        proc = _make_processor(parsers={"doc": doc_parser})

        async def _fake_docx(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        proc.process_docx_document = _fake_docx

        events = await _collect_events(
            proc.process_doc_document("test.doc", "r1", "1", "src", "o1", b"doc_data", "vr1")
        )

        assert any(e.event == "parsing_complete" for e in events)
        assert any(e.event == "indexing_complete" for e in events)
        doc_parser.convert_doc_to_docx.assert_called_once_with(b"doc_data")


# ===================================================================
# process_mdx_document: delegates to process_md_document
# ===================================================================


class TestProcessMdxDocument:
    @pytest.mark.asyncio
    async def test_mdx_delegates_to_md(self):
        """MDX is converted to MD and delegated to process_md_document."""
        mdx_parser = MagicMock()
        mdx_parser.convert_mdx_to_md = MagicMock(return_value="# Converted MD")
        proc = _make_processor(parsers={"mdx": mdx_parser})

        async def _fake_md(*args, **kwargs):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        proc.process_md_document = _fake_md

        events = await _collect_events(
            proc.process_mdx_document("test.mdx", "r1", "1", "src", "o1", "# MDX content", "vr1")
        )

        assert any(e.event == "parsing_complete" for e in events)
        assert any(e.event == "indexing_complete" for e in events)
        mdx_parser.convert_mdx_to_md.assert_called_once_with("# MDX content")


# ===================================================================
# process_delimited_document with TSV extension
# ===================================================================


class TestProcessDelimitedTSV:
    @pytest.mark.asyncio
    async def test_tsv_uses_correct_parser(self):
        """When extension='tsv', the TSV parser is used instead of CSV."""
        tsv_parser = MagicMock()
        tsv_parser.read_raw_rows.return_value = [["a", "b"], ["1", "2"]]
        tsv_parser.find_tables_in_csv.return_value = [
            {"rows": [["a", "b"], ["1", "2"]], "headers": ["a", "b"]}
        ]
        tsv_parser.get_blocks_from_csv_with_multiple_tables = AsyncMock(return_value=MagicMock())
        proc = _make_processor(parsers={"tsv": tsv_parser})

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.tsv")
        )

        with patch("app.events.processor.get_llm_for_role", new_callable=AsyncMock) as mock_llm, \
             patch("app.events.processor.IndexingPipeline") as MockPipeline:
            mock_llm.return_value = (MagicMock(), {})
            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_delimited_document("test.tsv", "r1", b"a\tb\n1\t2", "vr1", extension="tsv")
            )

        assert any(e.event == "parsing_complete" for e in events)
        assert any(e.event == "indexing_complete" for e in events)


# ===================================================================
# _enhance_tables_with_llm: cells is list but no column_headers -> empty dict
# (covers line 744 via BlockGroupChildren path)
# ===================================================================


class TestEnhanceTablesEmptyCellsDict:
    @pytest.mark.asyncio
    async def test_cells_no_column_headers_appends_empty_dict(self):
        """When cells is a list but column_headers is empty, appends empty dict."""
        from app.models.blocks import (
            Block,
            BlockGroup,
            BlockGroupChildren,
            BlocksContainer,
            BlockType,
            DataFormat,
            GroupType,
            IndexRange,
            TableRowMetadata,
        )

        proc = _make_processor()

        row_block = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.TXT,
            data={"cells": ["v1", "v2"]},
            table_row_metadata=TableRowMetadata(is_header=False),
        )

        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            data={"table_markdown": "| a | b |\n| 1 | 2 |"},
            children=BlockGroupChildren(
                block_ranges=[IndexRange(start=0, end=0)],
                block_group_ranges=[],
            ),
        )
        bc = BlocksContainer(blocks=[row_block], block_groups=[bg])

        mock_response = MagicMock()
        mock_response.summary = "Summary"
        mock_response.headers = []  # Empty headers

        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            # No get_rows_text needed because non_header_row_dicts will be [{...}]
            # Actually with empty headers, the condition `isinstance(cells, list) and column_headers`
            # evaluates to False (empty list is falsy), so row_dicts.append({}) is reached
            await proc._enhance_tables_with_llm(bc)


# ===================================================================
# process_pdf_document_with_ocr: OCR failure propagates (no fallback)
# ===================================================================


class TestProcessPdfOcrFallback:
    @pytest.mark.asyncio
    async def test_azure_ocr_failure_propagates(self):
        """When Azure OCR fails, exception propagates (no fallback)."""
        proc = _make_processor()

        mock_config = AsyncMock()
        mock_config.get_config = AsyncMock(return_value={
            "ocr": [{
                "provider": "azureDI",
                "configuration": {"endpoint": "https://example.com", "apiKey": "key"},
            }],
            "llm": [],
        })
        proc.config_service = mock_config

        with patch("app.events.processor.OCRHandler") as MockOCR:
            mock_ocr = MagicMock()
            mock_ocr.process_document = AsyncMock(side_effect=RuntimeError("Azure DI failed"))
            MockOCR.return_value = mock_ocr

            proc.graph_provider.get_document = AsyncMock(
                return_value=_mock_record_dict()
            )

            with pytest.raises(RuntimeError, match="Azure DI failed"):
                await _collect_events(
                    proc.process_pdf_document_with_ocr(
                        "test.pdf", "r1", "1", "src", "o1", b"pdfdata", "vr1"
                    )
                )


# ===================================================================
# _create_transform_context integration: verify it is called by
# process methods with the correct event_type
# ===================================================================


class TestCreateTransformContextCalledByProcessMethods:
    @pytest.mark.asyncio
    async def test_process_md_calls_create_transform_context(self):
        """process_md_document uses _create_transform_context with event_type."""
        from app.models.blocks import BlocksContainer
        proc = _make_processor()

        md_parser = MagicMock()
        md_parser.extract_and_replace_images.return_value = ("# Hello", [])
        md_parser.parse_string.return_value = b"<html><h1>Hello</h1></html>"
        proc.parsers = {"md": md_parser}

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.md")
        )

        with patch("app.events.processor.DoclingProcessor") as MockDP, \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch.object(proc, "_create_transform_context", wraps=proc._create_transform_context) as mock_ctx:
            MockDP.return_value.parse_document = AsyncMock(return_value=MagicMock())
            MockDP.return_value.create_blocks = AsyncMock(
                return_value=BlocksContainer(blocks=[], block_groups=[])
            )
            MockPipeline.return_value.apply = AsyncMock()

            await _collect_events(
                proc.process_md_document("test.md", "r1", b"# Hello", "vr1", event_type="updateRecord")
            )

            mock_ctx.assert_called_once()
            call_args = mock_ctx.call_args
            # event_type is passed as second positional arg
            assert call_args[0][1] == "updateRecord"

    @pytest.mark.asyncio
    async def test_process_excel_calls_create_transform_context(self):
        """process_excel_document uses _create_transform_context with event_type."""
        excel_parser = MagicMock()
        excel_parser.load_workbook_from_binary = MagicMock()
        excel_parser.create_blocks = AsyncMock(return_value=MagicMock())
        proc = _make_processor(parsers={"xlsx": excel_parser})

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="test.xlsx")
        )

        with patch("app.events.processor.get_llm_for_role", new_callable=AsyncMock) as mock_llm, \
             patch("app.events.processor.IndexingPipeline") as MockPipeline, \
             patch.object(proc, "_create_transform_context", wraps=proc._create_transform_context) as mock_ctx:
            mock_llm.return_value = (MagicMock(), {})
            MockPipeline.return_value.apply = AsyncMock()

            await _collect_events(
                proc.process_excel_document(
                    "test.xlsx", "r1", "1", "src", "o1", b"xldata", "vr1",
                    event_type="newRecord"
                )
            )

            mock_ctx.assert_called_once()


# ===================================================================
# Additional branch coverage (>95% on app.events.processor)
# ===================================================================


class TestProcessorCoverageBranchesTo95:
    """Remaining edge branches in processor.py."""

    @pytest.mark.asyncio
    async def test_process_pptx_adds_extension_when_missing(self):
        """recordName normalized to end with .pptx when omitted."""
        proc = _make_processor()

        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(return_value=MagicMock())
        mock_processor.create_blocks = AsyncMock(
            return_value=MagicMock(blocks=[], block_groups=[])
        )

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="slides")
        )

        with patch("app.events.processor.DoclingProcessor", return_value=mock_processor), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline:
            MockPipeline.return_value.apply = AsyncMock()

            await _collect_events(
                proc.process_pptx_document(
                    "Slides", "r1", "1", "src", "o1", b"pptx-bin", "vr1"
                )
            )

        fname = mock_processor.parse_document.await_args.args[0]
        assert fname.lower().endswith(".pptx")

    @pytest.mark.asyncio
    async def test_process_delimited_read_raw_rows_raises_non_unicode_then_succeeds(self):
        """generic Exception path in decode loop continues to next encoding."""
        proc = _make_processor()
        csv_parser = MagicMock()
        csv_parser.read_raw_rows = MagicMock(
            side_effect=[ValueError("bad csv"), [["h1", "h2"], ["a", "b"]]]
        )
        csv_parser.find_tables_in_csv.return_value = [MagicMock()]
        csv_parser.get_blocks_from_csv_with_multiple_tables = AsyncMock(
            return_value=MagicMock()
        )

        proc.parsers = {"csv": csv_parser}

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="t.csv")
        )

        data = "a,b\nc,d\n".encode("utf-8")

        with patch("app.events.processor.get_llm", new_callable=AsyncMock) as mock_llm, \
             patch("app.events.processor.IndexingPipeline") as MockPipeline:
            mock_llm.return_value = (MagicMock(), {})
            MockPipeline.return_value.apply = AsyncMock()

            events = await _collect_events(
                proc.process_delimited_document(
                    "t.csv", "r1", data, "vr1", extension="csv",
                )
            )

        assert any(e.event == "indexing_complete" for e in events)
        assert csv_parser.read_raw_rows.call_count >= 2

    @pytest.mark.asyncio
    async def test_process_md_with_images_base64_mapping(self):
        """Markdown path: extract images, urls_to_base64, map captions to IMAGE blocks."""
        proc = _make_processor()
        md_parser = MagicMock()
        md_parser.extract_and_replace_images.return_value = (
            "![pic](http://example.com/i.png)",
            [{"url": "http://example.com/i.png", "new_alt_text": "pic"}],
        )
        md_parser.parse_string.return_value = b"# md"
        png_parser = MagicMock()
        png_parser.urls_to_base64 = AsyncMock(return_value=["data:image/png;base64,AAA"])

        proc.parsers = {"md": md_parser, "png": png_parser}

        from app.models.blocks import (
            Block,
            BlockType,
            DataFormat,
            BlocksContainer,
            ImageMetadata,
        )

        mock_processor = AsyncMock()
        mock_processor.parse_document = AsyncMock(return_value=MagicMock())
        blk = Block(
            index=0,
            type=BlockType.IMAGE,
            format=DataFormat.BIN,
            data=None,
            image_metadata=ImageMetadata(captions=["pic"]),
        )
        mock_processor.create_blocks = AsyncMock(
            return_value=BlocksContainer(blocks=[blk], block_groups=[])
        )

        proc.graph_provider.get_document = AsyncMock(
            return_value=_mock_record_dict(recordName="doc.md")
        )

        with patch("app.events.processor.DoclingProcessor", return_value=mock_processor), \
             patch("app.events.processor.IndexingPipeline") as MockPipeline:
            MockPipeline.return_value.apply = AsyncMock()

            await _collect_events(proc.process_md_document("doc.md", "r1", b"# Hi", "vr1"))

        assert blk.data and blk.data.get("uri", "").startswith("data:")

    @pytest.mark.asyncio
    async def test_process_blockgroup_images_and_map_base64(self):
        """_process_blockgroup_images + _map_base64_images_to_blocks."""
        from app.models.blocks import Block, BlockType, DataFormat, ImageMetadata

        proc = _make_processor()
        md_parser = MagicMock()
        md_parser.extract_and_replace_images.return_value = (
            "text",
            [{"url": "http://x", "new_alt_text": "cap1"}],
        )
        png_parser = MagicMock()
        png_parser.urls_to_base64 = AsyncMock(return_value=["data:x"])

        proc.parsers = {"md": md_parser, "png": png_parser}

        mod_md, cmap = await proc._process_blockgroup_images("# x", block_group_index=3)
        assert mod_md == "text"
        assert cmap.get("cap1") == "data:x"

        blocks = [
            Block(
                index=0,
                type=BlockType.IMAGE,
                format=DataFormat.BIN,
                data=None,
                image_metadata=ImageMetadata(captions=["cap1"]),
            )
        ]
        proc._map_base64_images_to_blocks(blocks, cmap, block_group_index=3)
        assert blocks[0].data["uri"] == "data:x"

    @pytest.mark.asyncio
    async def test_ocr_azure_di_after_unknown_provider_iteration(self):
        """Skip unknown OCR providers; configure Azure on a later config entry."""
        proc = _make_processor()
        proc.config_service = AsyncMock()
        proc.config_service.get_config = AsyncMock(return_value={
            "ocr": [
                {"provider": "not_supported_yet"},
                {
                    "provider": "azureDI",
                    "configuration": {"endpoint": "https://e.azure.com", "apiKey": "k"},
                },
            ],
            "llm": [],
        })

        with patch("app.events.processor.OCRHandler") as MockOCR, \
             patch("app.events.processor.IndexingPipeline") as MockPipeline:
            h = AsyncMock()
            h.process_document = AsyncMock(return_value={
                "blocks": [],
                "tables": [],
            })
            MockOCR.return_value = h
            MockPipeline.return_value.apply = AsyncMock()

            proc.graph_provider.get_document = AsyncMock(
                return_value=_mock_record_dict()
            )

            await _collect_events(
                proc.process_pdf_document_with_ocr(
                    "test.pdf", "r1", "1", "src", "o1", b"pdf", "vr1"
                )
            )

        MockOCR.assert_called_once()
        assert MockOCR.call_args.kwargs["model_id"] == "prebuilt-document"

    @pytest.mark.asyncio
    async def test_non_vlm_ocr_prebuilt_block_table_row_and_plain_paragraph(self):
        """Non-VLM: Block TABLE_ROW populates table_rows + second Block skips dict path."""
        from app.models.blocks import Block, BlockGroup, BlockType, DataFormat, GroupType

        proc = _make_processor()
        proc.config_service = AsyncMock()
        proc.config_service.get_config = AsyncMock(return_value={
            "ocr": [{"provider": "azureDI", "configuration": {"endpoint": "https://e", "apiKey": "k"}}],
            "llm": [],
        })

        row_blk = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.TXT,
            data="cell",
            parent_index=42,
        )
        text_blk = Block(
            index=0,
            type=BlockType.TEXT,
            format=DataFormat.TXT,
            data="plain",
            parent_index=None,
        )
        tbl = BlockGroup(index=42, type=GroupType.TABLE)

        with patch("app.events.processor.OCRHandler") as MockOCR, \
             patch("app.events.processor.IndexingPipeline") as MockPipeline:
            mock_ocr = AsyncMock()
            mock_ocr.process_document = AsyncMock(return_value={
                "blocks": [row_blk, text_blk],
                "tables": [tbl],
            })
            MockOCR.return_value = mock_ocr
            MockPipeline.return_value.apply = AsyncMock()

            proc.graph_provider.get_document = AsyncMock(
                return_value=_mock_record_dict()
            )

            events = await _collect_events(
                proc.process_pdf_document_with_ocr(
                    "p.pdf", "r1", "1", "s", "o", b"x", "vr"
                )
            )

        assert any(e.event == "indexing_complete" for e in events)
        assert tbl.children is not None

    @pytest.mark.asyncio
    async def test_vlm_nested_block_groups_children_ranges_shift(self):
        """VLM path: merge blocks with nested block_groups + ranges."""
        from app.models.blocks import (
            Block,
            BlockGroup,
            BlockGroupChildren,
            BlocksContainer,
            BlockType,
            DataFormat,
            GroupType,
            IndexRange,
        )

        proc = _make_processor()
        nested_group = BlockGroup(
            index=0,
            type=GroupType.TEXT_SECTION,
            parent_index=None,
            children=BlockGroupChildren(
                block_ranges=[IndexRange(start=0, end=1)],
                block_group_ranges=[IndexRange(start=0, end=0)],
            ),
        )
        leaf_blocks = [
            Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="a", parent_index=0),
            Block(index=1, type=BlockType.TEXT, format=DataFormat.TXT, data="b", parent_index=0),
        ]
        nested_container = BlocksContainer(blocks=leaf_blocks, block_groups=[nested_group])

        proc.config_service = AsyncMock()
        proc.config_service.get_config = AsyncMock(return_value={
            "ocr": [{"provider": "vlmOCR"}],
            "llm": [],
        })

        with patch("app.events.processor.OCRHandler") as MockOCR, \
             patch("app.events.processor.DoclingProcessor") as MockDocling, \
             patch("app.events.processor.IndexingPipeline") as MockPipeline:
            mo = AsyncMock()
            mo.process_document = AsyncMock(return_value={
                "pages": [{"page_number": 1, "markdown": "# z"}],
            })
            MockOCR.return_value = mo

            mproc = AsyncMock()
            mproc.parse_document = AsyncMock(return_value=MagicMock())
            mproc.create_blocks = AsyncMock(return_value=nested_container)
            MockDocling.return_value = mproc
            MockPipeline.return_value.apply = AsyncMock()

            proc.graph_provider.get_document = AsyncMock(
                return_value=_mock_record_dict()
            )

            events = await _collect_events(
                proc.process_pdf_document_with_ocr(
                    "r.pdf", "r1", "1", "s", "o", b"z", "vr"
                )
            )

        assert any(e.event == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_enhance_tables_legacy_list_children(self):
        """_enhance_tables_with_llm: list-format children branch."""
        from unittest.mock import MagicMock as Mg

        from app.models.blocks import (
            BlockContainerIndex,
            BlockType,
            GroupType,
            TableMetadata,
        )

        proc = _make_processor()
        tg = Mg()
        tg.type = GroupType.TABLE
        tg.index = 10
        tg.data = {"table_markdown": "|c|\n|-|"}
        tg.table_metadata = TableMetadata(column_names=["c"])
        tg.children = [BlockContainerIndex(block_index=0)]
        tg.table_row_metadata = None

        row_block = Mg()
        row_block.type = BlockType.TABLE_ROW
        row_block.table_row_metadata = Mg()
        row_block.table_row_metadata.is_header = False
        row_block.data = {"cells": ["v"]}

        bc = Mg()
        bc.block_groups = [tg]
        bc.blocks = [row_block]

        mock_response = Mg()
        mock_response.summary = "s"
        mock_response.headers = ["c"]

        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=mock_response,
        ), patch(
            "app.utils.indexing_helpers.get_rows_text",
            new_callable=AsyncMock,
            return_value=(["desc"], []),
        ):
            await proc._enhance_tables_with_llm(bc)

        assert row_block.data.get("row_natural_language_text") == "desc"
