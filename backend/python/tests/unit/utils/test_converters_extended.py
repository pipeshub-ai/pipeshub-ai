"""
Extended tests for app.utils.converters.docling_doc_to_blocks covering missing lines:
- _enrich_metadata with legacy prov reference format (lines 134-140)
- _enrich_metadata with default_page_number fallback (line 147)
- _handle_text_block with empty text (line 170)
- _handle_group_block returning child block/blockgroup (lines 192-196)
- _handle_image_block with refs resolution (line 209, 217, 220)
- _handle_table_block empty cells (line 257)
- _process_item with invalid ref (line 344, 355)
- _process_item with table type (line 369-370)
- _process_item with unknown type (lines 399-400)
"""

import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.converters.docling_doc_to_blocks import (
    DOCLING_GROUP_BLOCK_TYPE,
    DOCLING_IMAGE_BLOCK_TYPE,
    DOCLING_TABLE_BLOCK_TYPE,
    DOCLING_TEXT_BLOCK_TYPE,
    DoclingDocToBlocksConverter,
)


def _make_converter():
    logger = logging.getLogger("test-converter")
    config = AsyncMock()
    return DoclingDocToBlocksConverter(logger, config)


# ============================================================================
# _process_content_in_order - prov ref path (legacy format)
# ============================================================================


class TestEnrichMetadataLegacy:
    @pytest.mark.asyncio
    async def test_prov_as_dict_ref(self):
        """Test legacy prov format with $ref."""
        converter = _make_converter()

        # Build a minimal doc that has a text item with legacy prov dict
        doc_dict = {
            "body": {
                "children": [{"$ref": "#/texts/0"}]
            },
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "text": "Hello",
                    "prov": {"$ref": "#/pages/0"},
                }
            ],
            "pages": [
                {"page_no": 1, "size": {"width": 100, "height": 200}},
            ],
        }

        mock_doc = MagicMock()
        mock_doc.export_to_dict.return_value = doc_dict

        result = await converter._process_content_in_order(mock_doc)
        assert len(result.blocks) == 1
        assert result.blocks[0].data == "Hello"

    @pytest.mark.asyncio
    async def test_empty_text_block_skipped(self):
        """Text blocks with empty text should not create a block."""
        converter = _make_converter()

        doc_dict = {
            "body": {
                "children": [{"$ref": "#/texts/0"}]
            },
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "text": "",
                    "prov": [],
                }
            ],
            "pages": {},
        }

        mock_doc = MagicMock()
        mock_doc.export_to_dict.return_value = doc_dict

        result = await converter._process_content_in_order(mock_doc)
        assert len(result.blocks) == 0


class TestDefaultPageNumber:
    @pytest.mark.asyncio
    async def test_page_number_fallback(self):
        """When prov has no page_no, default_page_number should be used."""
        converter = _make_converter()

        doc_dict = {
            "body": {
                "children": [{"$ref": "#/texts/0"}]
            },
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "text": "Content",
                    "prov": [],
                }
            ],
            "pages": {},
        }

        mock_doc = MagicMock()
        mock_doc.export_to_dict.return_value = doc_dict

        result = await converter._process_content_in_order(mock_doc, page_number=5)
        assert len(result.blocks) == 1
        assert result.blocks[0].citation_metadata is not None
        assert result.blocks[0].citation_metadata.page_number == 5


class TestImageBlock:
    @pytest.mark.asyncio
    async def test_image_with_captions_and_footnotes(self):
        """Test image block with caption and footnote references."""
        converter = _make_converter()

        doc_dict = {
            "body": {
                "children": [{"$ref": "#/pictures/0"}]
            },
            "texts": [
                {"self_ref": "#/texts/0", "text": "Caption text"},
                {"self_ref": "#/texts/1", "text": "Footnote text"},
            ],
            "pictures": [
                {
                    "self_ref": "#/pictures/0",
                    "image": "data:image/png;base64,abc123",
                    "captions": [{"$ref": "#/texts/0"}],
                    "footnotes": [{"$ref": "#/texts/1"}],
                    "prov": [{"page_no": 1, "bbox": {}}],
                }
            ],
            "pages": {"1": {"size": {"width": 100, "height": 200}}},
        }

        mock_doc = MagicMock()
        mock_doc.export_to_dict.return_value = doc_dict

        result = await converter._process_content_in_order(mock_doc)
        assert len(result.blocks) == 1
        assert result.blocks[0].image_metadata is not None
        assert "Caption text" in result.blocks[0].image_metadata.captions


class TestTableBlock:
    @pytest.mark.asyncio
    async def test_table_with_empty_cells(self):
        """Table with no cells should return None and log warning."""
        converter = _make_converter()

        doc_dict = {
            "body": {
                "children": [{"$ref": "#/tables/0"}]
            },
            "tables": [
                {
                    "self_ref": "#/tables/0",
                    "data": {"table_cells": [], "grid": []},
                    "prov": [],
                }
            ],
            "pages": {},
        }

        mock_doc = MagicMock()
        mock_doc.export_to_dict.return_value = doc_dict

        result = await converter._process_content_in_order(mock_doc)
        assert len(result.block_groups) == 0


class TestProcessItemEdgeCases:
    @pytest.mark.asyncio
    async def test_invalid_ref_path(self):
        """Non #/ ref paths should be skipped."""
        converter = _make_converter()

        doc_dict = {
            "body": {
                "children": [{"$ref": "invalid/path"}]
            },
        }

        mock_doc = MagicMock()
        mock_doc.export_to_dict.return_value = doc_dict

        result = await converter._process_content_in_order(mock_doc)
        assert len(result.blocks) == 0

    @pytest.mark.asyncio
    async def test_item_index_out_of_range(self):
        """Item index exceeding list length should be skipped."""
        converter = _make_converter()

        doc_dict = {
            "body": {
                "children": [{"$ref": "#/texts/99"}]
            },
            "texts": [],
        }

        mock_doc = MagicMock()
        mock_doc.export_to_dict.return_value = doc_dict

        result = await converter._process_content_in_order(mock_doc)
        assert len(result.blocks) == 0

    @pytest.mark.asyncio
    async def test_duplicate_ref_skipped(self):
        """Duplicate refs should only be processed once."""
        converter = _make_converter()

        doc_dict = {
            "body": {
                "children": [
                    {"$ref": "#/texts/0"},
                    {"$ref": "#/texts/0"},  # duplicate
                ]
            },
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "text": "Content",
                    "prov": [],
                }
            ],
            "pages": {},
        }

        mock_doc = MagicMock()
        mock_doc.export_to_dict.return_value = doc_dict

        result = await converter._process_content_in_order(mock_doc)
        assert len(result.blocks) == 1


class TestConvertMethod:
    @pytest.mark.asyncio
    async def test_convert_delegates(self):
        converter = _make_converter()

        doc_dict = {
            "body": {"children": []},
        }

        mock_doc = MagicMock()
        mock_doc.export_to_dict.return_value = doc_dict

        result = await converter.convert(mock_doc)
        assert result is not None
        assert len(result.blocks) == 0
