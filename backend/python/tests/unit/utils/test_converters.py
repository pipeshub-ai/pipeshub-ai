"""Tests for DoclingDocToBlocksConverter."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import (
    Block,
    BlockGroup,
    BlocksContainer,
    BlockType,
    DataFormat,
    GroupType,
)
from app.utils.converters.docling_doc_to_blocks import (
    DOCLING_GROUP_BLOCK_TYPE,
    DOCLING_IMAGE_BLOCK_TYPE,
    DOCLING_REF_NODE,
    DOCLING_TABLE_BLOCK_TYPE,
    DOCLING_TEXT_BLOCK_TYPE,
    DoclingDocToBlocksConverter,
)


@pytest.fixture
def logger():
    import logging
    return logging.getLogger("test_converter")


@pytest.fixture
def config():
    return MagicMock()


@pytest.fixture
def converter(logger, config):
    return DoclingDocToBlocksConverter(logger, config)


def _make_doc(doc_dict):
    """Create a mock DoclingDocument that returns the given dict on export."""
    doc = MagicMock()
    doc.export_to_dict.return_value = doc_dict
    return doc


class TestDoclingDocToBlocksConverterConvert:
    """Tests for the convert() entry point."""

    @pytest.mark.asyncio
    async def test_convert_empty_document(self, converter):
        """Empty document returns empty BlocksContainer."""
        doc = _make_doc({"body": {"children": []}, "texts": [], "pages": {}})
        result = await converter.convert(doc)
        assert isinstance(result, BlocksContainer)
        assert result.blocks == []
        assert result.block_groups == []

    @pytest.mark.asyncio
    async def test_convert_with_page_number(self, converter):
        """Page number is passed through to _process_content_in_order."""
        doc = _make_doc({"body": {"children": []}, "texts": [], "pages": {}})
        result = await converter.convert(doc, page_number=3)
        assert isinstance(result, BlocksContainer)


class TestProcessContentInOrder:
    """Tests for _process_content_in_order."""

    @pytest.mark.asyncio
    async def test_single_text_block(self, converter):
        """Single text block is converted to a Block."""
        doc_dict = {
            "body": {
                "children": [
                    {DOCLING_REF_NODE: "#/texts/0"}
                ]
            },
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "text": "Hello World",
                    "prov": [],
                }
            ],
            "pages": {},
        }
        doc = _make_doc(doc_dict)
        result = await converter._process_content_in_order(doc)

        assert isinstance(result, BlocksContainer)
        assert len(result.blocks) == 1
        assert result.blocks[0].type == BlockType.TEXT
        assert result.blocks[0].data == "Hello World"
        assert result.blocks[0].source_id == "#/texts/0"

    @pytest.mark.asyncio
    async def test_empty_text_skipped(self, converter):
        """Text blocks with empty text are not added."""
        doc_dict = {
            "body": {
                "children": [
                    {DOCLING_REF_NODE: "#/texts/0"}
                ]
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
        doc = _make_doc(doc_dict)
        result = await converter._process_content_in_order(doc)

        assert len(result.blocks) == 0

    @pytest.mark.asyncio
    async def test_duplicate_refs_skipped(self, converter):
        """Duplicate references are processed only once."""
        doc_dict = {
            "body": {
                "children": [
                    {DOCLING_REF_NODE: "#/texts/0"},
                    {DOCLING_REF_NODE: "#/texts/0"},
                ]
            },
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "text": "Only once",
                    "prov": [],
                }
            ],
            "pages": {},
        }
        doc = _make_doc(doc_dict)
        result = await converter._process_content_in_order(doc)

        assert len(result.blocks) == 1

    @pytest.mark.asyncio
    async def test_invalid_ref_path_skipped(self, converter):
        """Non-standard ref paths are skipped."""
        doc_dict = {
            "body": {
                "children": [
                    {DOCLING_REF_NODE: "invalid/path"},
                    {DOCLING_REF_NODE: ""},
                ]
            },
            "texts": [],
            "pages": {},
        }
        doc = _make_doc(doc_dict)
        result = await converter._process_content_in_order(doc)

        assert len(result.blocks) == 0

    @pytest.mark.asyncio
    async def test_index_out_of_range_skipped(self, converter):
        """References pointing beyond the items list are skipped."""
        doc_dict = {
            "body": {
                "children": [
                    {DOCLING_REF_NODE: "#/texts/5"}  # Out of range
                ]
            },
            "texts": [
                {"self_ref": "#/texts/0", "text": "Only one", "prov": []}
            ],
            "pages": {},
        }
        doc = _make_doc(doc_dict)
        result = await converter._process_content_in_order(doc)

        assert len(result.blocks) == 0

    @pytest.mark.asyncio
    async def test_image_block(self, converter):
        """Image blocks are properly converted."""
        doc_dict = {
            "body": {
                "children": [
                    {DOCLING_REF_NODE: "#/pictures/0"}
                ]
            },
            "pictures": [
                {
                    "self_ref": "#/pictures/0",
                    "image": "data:image/png;base64,abc123",
                    "prov": [],
                    "captions": [],
                    "footnotes": [],
                }
            ],
            "pages": {},
        }
        doc = _make_doc(doc_dict)
        result = await converter._process_content_in_order(doc)

        assert len(result.blocks) == 1
        block = result.blocks[0]
        assert block.type == BlockType.IMAGE
        assert block.format == DataFormat.BASE64
        assert block.data == "data:image/png;base64,abc123"

    @pytest.mark.asyncio
    async def test_image_block_with_captions_and_footnotes(self, converter):
        """Image block captions and footnotes from refs are resolved."""
        doc_dict = {
            "body": {
                "children": [
                    {DOCLING_REF_NODE: "#/pictures/0"}
                ]
            },
            "pictures": [
                {
                    "self_ref": "#/pictures/0",
                    "image": "data:image/png;base64,abc",
                    "prov": [],
                    "captions": [{DOCLING_REF_NODE: "#/texts/0"}],
                    "footnotes": ["Literal footnote"],
                }
            ],
            "texts": [
                {"self_ref": "#/texts/0", "text": "Caption text", "prov": []}
            ],
            "pages": {},
        }
        doc = _make_doc(doc_dict)
        result = await converter._process_content_in_order(doc)

        assert len(result.blocks) == 1
        block = result.blocks[0]
        assert block.image_metadata is not None
        assert "Caption text" in block.image_metadata.captions
        assert "Literal footnote" in block.image_metadata.footnotes

    @pytest.mark.asyncio
    async def test_group_block_with_recognized_label(self, converter):
        """Group blocks with recognized labels create BlockGroups."""
        doc_dict = {
            "body": {
                "children": [
                    {DOCLING_REF_NODE: "#/groups/0"}
                ]
            },
            "groups": [
                {
                    "self_ref": "#/groups/0",
                    "label": "list",
                    "children": [
                        {DOCLING_REF_NODE: "#/texts/0"}
                    ],
                    "prov": [],
                }
            ],
            "texts": [
                {"self_ref": "#/texts/0", "text": "Item 1", "prov": []}
            ],
            "pages": {},
        }
        doc = _make_doc(doc_dict)
        result = await converter._process_content_in_order(doc)

        assert len(result.block_groups) == 1
        assert result.block_groups[0].type == GroupType.LIST
        assert len(result.blocks) == 1

    @pytest.mark.asyncio
    async def test_group_block_with_unrecognized_label(self, converter):
        """Group blocks with unrecognized labels process children but no BlockGroup created."""
        doc_dict = {
            "body": {
                "children": [
                    {DOCLING_REF_NODE: "#/groups/0"}
                ]
            },
            "groups": [
                {
                    "self_ref": "#/groups/0",
                    "label": "unknown_type",
                    "children": [
                        {DOCLING_REF_NODE: "#/texts/0"}
                    ],
                    "prov": [],
                }
            ],
            "texts": [
                {"self_ref": "#/texts/0", "text": "Child text", "prov": []}
            ],
            "pages": {},
        }
        doc = _make_doc(doc_dict)
        result = await converter._process_content_in_order(doc)

        # The group itself doesn't create a BlockGroup for unrecognized labels
        assert len(result.block_groups) == 0
        # But the child text block is still processed
        assert len(result.blocks) == 1

    @pytest.mark.asyncio
    async def test_table_block(self, converter):
        """Table blocks are converted to BlockGroup with TABLE_ROW children."""
        table_data = {
            "table_cells": [
                {"text": "A1", "row": 0, "col": 0},
                {"text": "B1", "row": 0, "col": 1},
            ],
            "grid": [
                [{"text": "A1"}, {"text": "B1"}],
            ],
            "num_rows": 1,
            "num_cols": 2,
        }

        doc_dict = {
            "body": {
                "children": [
                    {DOCLING_REF_NODE: "#/tables/0"}
                ]
            },
            "tables": [
                {
                    "self_ref": "#/tables/0",
                    "name": "Table 1",
                    "data": table_data,
                    "prov": [],
                    "captions": [],
                    "footnotes": [],
                }
            ],
            "texts": [],
            "pages": {},
        }
        doc = _make_doc(doc_dict)

        mock_summary_response = MagicMock()
        mock_summary_response.summary = "A summary"
        mock_summary_response.headers = ["A", "B"]

        mock_get_summary = AsyncMock(return_value=mock_summary_response)
        mock_get_rows = AsyncMock(return_value=(["Row 1 text"], [{"row": 1}]))

        with patch("app.utils.converters.docling_doc_to_blocks.get_table_summary_n_headers", mock_get_summary), \
             patch("app.utils.converters.docling_doc_to_blocks.get_rows_text", mock_get_rows):

            result = await converter._process_content_in_order(doc)

        assert len(result.block_groups) == 1
        assert result.block_groups[0].type == GroupType.TABLE
        assert result.block_groups[0].name == "Table 1"
        assert len(result.blocks) >= 1  # At least one table row block

    @pytest.mark.asyncio
    async def test_table_block_no_cells_skipped(self, converter):
        """Table with no cells returns None / is skipped."""
        doc_dict = {
            "body": {
                "children": [
                    {DOCLING_REF_NODE: "#/tables/0"}
                ]
            },
            "tables": [
                {
                    "self_ref": "#/tables/0",
                    "name": "Empty Table",
                    "data": {"table_cells": [], "grid": []},
                    "prov": [],
                    "captions": [],
                    "footnotes": [],
                }
            ],
            "texts": [],
            "pages": {},
        }
        doc = _make_doc(doc_dict)
        result = await converter._process_content_in_order(doc)

        assert len(result.block_groups) == 0

    @pytest.mark.asyncio
    async def test_unknown_item_type_skipped(self, converter):
        """Unknown item types (not texts/groups/pictures/tables) are skipped."""
        doc_dict = {
            "body": {
                "children": [
                    {DOCLING_REF_NODE: "#/unknown_type/0"}
                ]
            },
            "unknown_type": [
                {"self_ref": "#/unknown_type/0", "data": "something"}
            ],
            "pages": {},
        }
        doc = _make_doc(doc_dict)
        result = await converter._process_content_in_order(doc)

        assert len(result.blocks) == 0
        assert len(result.block_groups) == 0


class TestEnrichMetadata:
    """Tests for metadata enrichment (citation metadata)."""

    @pytest.mark.asyncio
    async def test_text_block_with_page_provenance(self, converter):
        """Text block with prov page_no sets citation metadata."""
        doc_dict = {
            "body": {
                "children": [
                    {DOCLING_REF_NODE: "#/texts/0"}
                ]
            },
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "text": "With page info",
                    "prov": [
                        {
                            "page_no": 2,
                            "bbox": {"l": 0, "t": 10.1, "r": 100.0, "b": 50.0, "coord_origin": "BOTTOMLEFT"},
                        }
                    ],
                }
            ],
            "pages": {
                "2": {
                    "size": {"width": 612, "height": 792}
                }
            },
        }
        doc = _make_doc(doc_dict)

        with patch("app.utils.converters.docling_doc_to_blocks.transform_bbox_to_corners",
                    return_value=[(0, 0), (100, 0), (100, 50), (0, 50)]), \
             patch("app.utils.converters.docling_doc_to_blocks.normalize_corner_coordinates",
                    return_value=[(0, 0), (0.16, 0), (0.16, 0.06), (0, 0.06)]):

            result = await converter._process_content_in_order(doc)

        assert len(result.blocks) == 1
        block = result.blocks[0]
        assert block.citation_metadata is not None
        assert block.citation_metadata.page_number == 2
        assert block.citation_metadata.bounding_boxes is not None
        assert len(block.citation_metadata.bounding_boxes) == 4

    @pytest.mark.asyncio
    async def test_default_page_number_fallback(self, converter):
        """When prov has no page_no, default_page_number is used."""
        doc_dict = {
            "body": {
                "children": [
                    {DOCLING_REF_NODE: "#/texts/0"}
                ]
            },
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "text": "No prov page",
                    "prov": [],
                }
            ],
            "pages": {},
        }
        doc = _make_doc(doc_dict)
        result = await converter._process_content_in_order(doc, page_number=5)

        assert len(result.blocks) == 1
        block = result.blocks[0]
        assert block.citation_metadata is not None
        assert block.citation_metadata.page_number == 5

    @pytest.mark.asyncio
    async def test_bbox_processing_failure_handled(self, converter):
        """Failed bbox processing does not crash - bounding_boxes not set."""
        doc_dict = {
            "body": {
                "children": [
                    {DOCLING_REF_NODE: "#/texts/0"}
                ]
            },
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "text": "Bad bbox",
                    "prov": [
                        {
                            "page_no": 1,
                            "bbox": {"l": 0, "t": 10, "r": 100, "b": 50, "coord_origin": "BOTTOMLEFT"},
                        }
                    ],
                }
            ],
            "pages": {
                "1": {"size": {"width": 612, "height": 792}}
            },
        }
        doc = _make_doc(doc_dict)

        with patch("app.utils.converters.docling_doc_to_blocks.transform_bbox_to_corners",
                    side_effect=ValueError("bad bbox")):
            result = await converter._process_content_in_order(doc)

        assert len(result.blocks) == 1
        block = result.blocks[0]
        assert block.citation_metadata is not None
        assert block.citation_metadata.page_number == 1
        # bounding_boxes should not be set due to failure
        assert block.citation_metadata.bounding_boxes is None


class TestMultipleBlockTypes:
    """Integration test with multiple block types."""

    @pytest.mark.asyncio
    async def test_mixed_content(self, converter):
        """Document with mixed text and image blocks."""
        doc_dict = {
            "body": {
                "children": [
                    {DOCLING_REF_NODE: "#/texts/0"},
                    {DOCLING_REF_NODE: "#/pictures/0"},
                    {DOCLING_REF_NODE: "#/texts/1"},
                ]
            },
            "texts": [
                {"self_ref": "#/texts/0", "text": "First paragraph", "prov": []},
                {"self_ref": "#/texts/1", "text": "Second paragraph", "prov": []},
            ],
            "pictures": [
                {
                    "self_ref": "#/pictures/0",
                    "image": "data:image/png;base64,xyz",
                    "prov": [],
                    "captions": [],
                    "footnotes": [],
                }
            ],
            "pages": {},
        }
        doc = _make_doc(doc_dict)
        result = await converter._process_content_in_order(doc)

        assert len(result.blocks) == 3
        assert result.blocks[0].type == BlockType.TEXT
        assert result.blocks[0].data == "First paragraph"
        assert result.blocks[1].type == BlockType.IMAGE
        assert result.blocks[2].type == BlockType.TEXT
        assert result.blocks[2].data == "Second paragraph"

    @pytest.mark.asyncio
    async def test_blocks_have_sequential_indices(self, converter):
        """Blocks are assigned sequential indices starting from 0."""
        doc_dict = {
            "body": {
                "children": [
                    {DOCLING_REF_NODE: "#/texts/0"},
                    {DOCLING_REF_NODE: "#/texts/1"},
                    {DOCLING_REF_NODE: "#/texts/2"},
                ]
            },
            "texts": [
                {"self_ref": "#/texts/0", "text": "A", "prov": []},
                {"self_ref": "#/texts/1", "text": "B", "prov": []},
                {"self_ref": "#/texts/2", "text": "C", "prov": []},
            ],
            "pages": {},
        }
        doc = _make_doc(doc_dict)
        result = await converter._process_content_in_order(doc)

        assert [b.index for b in result.blocks] == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_nested_group_with_children(self, converter):
        """Group blocks properly process nested children."""
        doc_dict = {
            "body": {
                "children": [
                    {DOCLING_REF_NODE: "#/groups/0"}
                ]
            },
            "groups": [
                {
                    "self_ref": "#/groups/0",
                    "label": "ordered_list",
                    "children": [
                        {DOCLING_REF_NODE: "#/texts/0"},
                        {DOCLING_REF_NODE: "#/texts/1"},
                    ],
                    "prov": [],
                }
            ],
            "texts": [
                {"self_ref": "#/texts/0", "text": "Item 1", "prov": []},
                {"self_ref": "#/texts/1", "text": "Item 2", "prov": []},
            ],
            "pages": {},
        }
        doc = _make_doc(doc_dict)
        result = await converter._process_content_in_order(doc)

        assert len(result.block_groups) == 1
        assert result.block_groups[0].type == GroupType.ORDERED_LIST
        assert len(result.blocks) == 2
        # Children should have the block_group as parent
        assert result.blocks[0].parent_index == 0
        assert result.blocks[1].parent_index == 0
