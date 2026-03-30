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
import logging
from app.models.blocks import (
    BlockType,
    GroupType,
)
from app.utils.converters.docling_doc_to_blocks import (
    DOCLING_REF_NODE,
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

# =============================================================================
# Merged from test_converters_coverage.py
# =============================================================================

def _make_converter():
    logger = logging.getLogger("test-converter-coverage")
    config = AsyncMock()
    return DoclingDocToBlocksConverter(logger, config)


def _make_doc_cov(doc_dict):
    doc = MagicMock()
    doc.export_to_dict.return_value = doc_dict
    return doc


class TestDefaultPageNumberOverridesExisting:
    """Line 147: when prov sets citation_metadata but default_page_number is also provided,
    the default_page_number should override the prov-derived page_number."""

    @pytest.mark.asyncio
    async def test_page_number_overrides_prov_page_number(self):
        converter = _make_converter()
        doc_dict = {
            "body": {
                "children": [{DOCLING_REF_NODE: "#/texts/0"}]
            },
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "text": "Some text",
                    "prov": [
                        {
                            "page_no": 3,
                            "bbox": {"l": 0, "t": 10, "r": 100, "b": 50, "coord_origin": "BOTTOMLEFT"},
                        }
                    ],
                }
            ],
            "pages": {
                "3": {"size": {"width": 612, "height": 792}}
            },
        }
        doc = _make_doc_cov(doc_dict)

        with patch(
            "app.utils.converters.docling_doc_to_blocks.transform_bbox_to_corners",
            return_value=[(0, 0), (100, 0), (100, 50), (0, 50)],
        ), patch(
            "app.utils.converters.docling_doc_to_blocks.normalize_corner_coordinates",
            return_value=[(0, 0), (0.16, 0), (0.16, 0.06), (0, 0.06)],
        ):
            result = await converter._process_content_in_order(doc, page_number=99)

        assert len(result.blocks) == 1
        block = result.blocks[0]
        assert block.citation_metadata is not None
        # Line 147: default_page_number overrides the prov-derived page_number
        assert block.citation_metadata.page_number == 99


class TestTextBlockWithChildren:
    """Line 170: text block that has children should recursively process them."""

    @pytest.mark.asyncio
    async def test_text_block_children_processed(self):
        converter = _make_converter()
        doc_dict = {
            "body": {
                "children": [{DOCLING_REF_NODE: "#/texts/0"}]
            },
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "text": "Parent text",
                    "prov": [],
                    "children": [
                        {DOCLING_REF_NODE: "#/texts/1"}
                    ],
                },
                {
                    "self_ref": "#/texts/1",
                    "text": "Child text",
                    "prov": [],
                },
            ],
            "pages": {},
        }
        doc = _make_doc_cov(doc_dict)
        result = await converter._process_content_in_order(doc)

        assert len(result.blocks) == 2
        assert result.blocks[0].data == "Parent text"
        assert result.blocks[1].data == "Child text"


class TestGroupBlockWithNestedBlockGroup:
    """Lines 195-196: group block containing a child that is itself a group (BlockGroup)."""

    @pytest.mark.asyncio
    async def test_nested_group_returns_block_group(self):
        converter = _make_converter()
        doc_dict = {
            "body": {
                "children": [{DOCLING_REF_NODE: "#/groups/0"}]
            },
            "groups": [
                {
                    "self_ref": "#/groups/0",
                    "label": "list",
                    "children": [
                        {DOCLING_REF_NODE: "#/groups/1"},
                        {DOCLING_REF_NODE: "#/texts/0"},
                    ],
                    "prov": [],
                },
                {
                    "self_ref": "#/groups/1",
                    "label": "inline",
                    "children": [
                        {DOCLING_REF_NODE: "#/texts/1"},
                    ],
                    "prov": [],
                },
            ],
            "texts": [
                {"self_ref": "#/texts/0", "text": "Top level text", "prov": []},
                {"self_ref": "#/texts/1", "text": "Nested group text", "prov": []},
            ],
            "pages": {},
        }
        doc = _make_doc_cov(doc_dict)
        result = await converter._process_content_in_order(doc)

        # Both groups created
        assert len(result.block_groups) == 2
        assert result.block_groups[0].type == GroupType.LIST
        assert result.block_groups[1].type == GroupType.INLINE
        # Children of the outer group should include the inner group index
        outer_group = result.block_groups[0]
        assert outer_group.children is not None


class TestGetRefTextEdgeCases:
    """Lines 209, 217: _get_ref_text returning empty string for edge cases."""

    @pytest.mark.asyncio
    async def test_image_caption_ref_with_invalid_ref_path(self):
        """When a caption ref doesn't start with #/, _get_ref_text returns ''."""
        converter = _make_converter()
        doc_dict = {
            "body": {
                "children": [{DOCLING_REF_NODE: "#/pictures/0"}]
            },
            "pictures": [
                {
                    "self_ref": "#/pictures/0",
                    "image": "data:image/png;base64,abc",
                    "prov": [],
                    "captions": [{DOCLING_REF_NODE: "invalid/ref"}],
                    "footnotes": [],
                }
            ],
            "texts": [],
            "pages": {},
        }
        doc = _make_doc_cov(doc_dict)
        result = await converter._process_content_in_order(doc)

        assert len(result.blocks) == 1
        # The invalid ref should return "" for caption text
        assert result.blocks[0].image_metadata.captions == [""]

    @pytest.mark.asyncio
    async def test_image_caption_ref_out_of_range(self):
        """When the ref text index is out of range, _get_ref_text returns ''."""
        converter = _make_converter()
        doc_dict = {
            "body": {
                "children": [{DOCLING_REF_NODE: "#/pictures/0"}]
            },
            "pictures": [
                {
                    "self_ref": "#/pictures/0",
                    "image": "data:image/png;base64,abc",
                    "prov": [],
                    "captions": [{DOCLING_REF_NODE: "#/texts/99"}],
                    "footnotes": [],
                }
            ],
            "texts": [],
            "pages": {},
        }
        doc = _make_doc_cov(doc_dict)
        result = await converter._process_content_in_order(doc)

        assert len(result.blocks) == 1
        # Out of range should return ""
        assert result.blocks[0].image_metadata.captions == [""]


class TestImageBlockWithChildren:
    """Line 254: image block with children should recursively process them."""

    @pytest.mark.asyncio
    async def test_image_block_children_processed(self):
        converter = _make_converter()
        doc_dict = {
            "body": {
                "children": [{DOCLING_REF_NODE: "#/pictures/0"}]
            },
            "pictures": [
                {
                    "self_ref": "#/pictures/0",
                    "image": "data:image/png;base64,xyz",
                    "prov": [],
                    "captions": [],
                    "footnotes": [],
                    "children": [
                        {DOCLING_REF_NODE: "#/texts/0"}
                    ],
                }
            ],
            "texts": [
                {"self_ref": "#/texts/0", "text": "Image child text", "prov": []},
            ],
            "pages": {},
        }
        doc = _make_doc_cov(doc_dict)
        result = await converter._process_content_in_order(doc)

        # Image block + child text block
        assert len(result.blocks) == 2
        assert result.blocks[0].type == BlockType.IMAGE
        assert result.blocks[1].type == BlockType.TEXT
        assert result.blocks[1].data == "Image child text"


class TestTableBlockWithChildren:
    """Line 339: table block with children should recursively process them."""

    @pytest.mark.asyncio
    async def test_table_block_children_processed(self):
        converter = _make_converter()
        table_data = {
            "table_cells": [
                {"text": "A1", "row": 0, "col": 0},
            ],
            "grid": [
                [{"text": "A1"}],
            ],
            "num_rows": 1,
            "num_cols": 1,
        }

        doc_dict = {
            "body": {
                "children": [{DOCLING_REF_NODE: "#/tables/0"}]
            },
            "tables": [
                {
                    "self_ref": "#/tables/0",
                    "name": "Table 1",
                    "data": table_data,
                    "prov": [],
                    "captions": [],
                    "footnotes": [],
                    "children": [
                        {DOCLING_REF_NODE: "#/texts/0"}
                    ],
                }
            ],
            "texts": [
                {"self_ref": "#/texts/0", "text": "Table child text", "prov": []},
            ],
            "pages": {},
        }
        doc = _make_doc_cov(doc_dict)

        mock_summary_response = MagicMock()
        mock_summary_response.summary = "Summary"
        mock_summary_response.headers = ["A"]

        mock_get_summary = AsyncMock(return_value=mock_summary_response)
        mock_get_rows = AsyncMock(return_value=(["Row text"], [{"row": 1}]))

        with patch(
            "app.utils.converters.docling_doc_to_blocks.get_table_summary_n_headers",
            mock_get_summary,
        ), patch(
            "app.utils.converters.docling_doc_to_blocks.get_rows_text",
            mock_get_rows,
        ):
            result = await converter._process_content_in_order(doc)

        # Table group + table row block + child text block
        assert len(result.block_groups) == 1
        assert any(b.data == "Table child text" for b in result.blocks)


class TestProcessItemRefPathEdgeCases:
    """Lines 361-362: IndexError/ValueError on ref path parts (e.g., #/texts or #/texts/abc)."""

    @pytest.mark.asyncio
    async def test_ref_path_missing_index(self):
        """Ref path with missing index like '#/texts' should be skipped."""
        converter = _make_converter()
        doc_dict = {
            "body": {
                "children": [{DOCLING_REF_NODE: "#/texts"}]
            },
            "texts": [
                {"self_ref": "#/texts/0", "text": "Should not appear", "prov": []},
            ],
            "pages": {},
        }
        doc = _make_doc_cov(doc_dict)
        result = await converter._process_content_in_order(doc)
        assert len(result.blocks) == 0

    @pytest.mark.asyncio
    async def test_ref_path_non_numeric_index(self):
        """Ref path with non-numeric index like '#/texts/abc' should be skipped."""
        converter = _make_converter()
        doc_dict = {
            "body": {
                "children": [{DOCLING_REF_NODE: "#/texts/abc"}]
            },
            "texts": [
                {"self_ref": "#/texts/0", "text": "Should not appear", "prov": []},
            ],
            "pages": {},
        }
        doc = _make_doc_cov(doc_dict)
        result = await converter._process_content_in_order(doc)
        assert len(result.blocks) == 0


class TestUnknownItemTypeElseBranch:
    """Lines 391-392: the final else branch when item_type is not any recognized type."""

    @pytest.mark.asyncio
    async def test_pages_type_triggers_error_log(self):
        """A ref to 'pages' type triggers the initial unknown type check (line 370-372),
        not the final else. Testing with a valid-looking item that passes the dict check
        but has an unrecognized type."""
        converter = _make_converter()
        # This is already covered by existing tests, but let's ensure the else branch
        # at line 391-392 which requires going through all item_type checks
        doc_dict = {
            "body": {
                "children": [
                    {DOCLING_REF_NODE: "#/pages/0"}
                ]
            },
            "pages": [
                {"self_ref": "#/pages/0", "page_no": 1, "size": {"width": 100, "height": 200}}
            ],
        }
        doc = _make_doc_cov(doc_dict)
        result = await converter._process_content_in_order(doc)
        assert len(result.blocks) == 0


class TestProvWithEmptyPageNo:
    """Branch 110->143 and 115->143: prov exists but page_no is None/falsy."""

    @pytest.mark.asyncio
    async def test_prov_with_none_page_no(self):
        """When prov has a page_no that is None/0, the citation_metadata from prov
        is not set, falling through to the default_page_number."""
        converter = _make_converter()
        doc_dict = {
            "body": {
                "children": [{DOCLING_REF_NODE: "#/texts/0"}]
            },
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "text": "Text with null page_no",
                    "prov": [
                        {"page_no": None, "bbox": {}}
                    ],
                }
            ],
            "pages": {},
        }
        doc = _make_doc_cov(doc_dict)
        result = await converter._process_content_in_order(doc, page_number=7)

        assert len(result.blocks) == 1
        block = result.blocks[0]
        assert block.citation_metadata is not None
        assert block.citation_metadata.page_number == 7

    @pytest.mark.asyncio
    async def test_prov_with_zero_page_no(self):
        """page_no=0 is falsy, so citation won't be set from prov."""
        converter = _make_converter()
        doc_dict = {
            "body": {
                "children": [{DOCLING_REF_NODE: "#/texts/0"}]
            },
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "text": "Text with zero page_no",
                    "prov": [
                        {"page_no": 0, "bbox": {}}
                    ],
                }
            ],
            "pages": {},
        }
        doc = _make_doc_cov(doc_dict)
        result = await converter._process_content_in_order(doc, page_number=10)

        block = result.blocks[0]
        assert block.citation_metadata.page_number == 10


class TestLegacyProvWithPageIndex:
    """Lines 134-140: legacy prov format with valid page_no."""

    @pytest.mark.asyncio
    async def test_legacy_prov_with_valid_page_no(self):
        converter = _make_converter()
        doc_dict = {
            "body": {
                "children": [{DOCLING_REF_NODE: "#/texts/0"}]
            },
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "text": "Legacy prov text",
                    "prov": {DOCLING_REF_NODE: "#/pages/0"},
                }
            ],
            "pages": [
                {"page_no": 5, "size": {"width": 100, "height": 200}},
            ],
        }
        doc = _make_doc_cov(doc_dict)
        result = await converter._process_content_in_order(doc)

        assert len(result.blocks) == 1
        block = result.blocks[0]
        assert block.citation_metadata is not None
        assert block.citation_metadata.page_number == 5

    @pytest.mark.asyncio
    async def test_legacy_prov_page_index_out_of_range(self):
        """Legacy prov with page index beyond pages list."""
        converter = _make_converter()
        doc_dict = {
            "body": {
                "children": [{DOCLING_REF_NODE: "#/texts/0"}]
            },
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "text": "Out of range page",
                    "prov": {DOCLING_REF_NODE: "#/pages/99"},
                }
            ],
            "pages": [
                {"page_no": 1, "size": {"width": 100, "height": 200}},
            ],
        }
        doc = _make_doc_cov(doc_dict)
        result = await converter._process_content_in_order(doc)

        assert len(result.blocks) == 1
        # No citation metadata since page index is out of range
        assert result.blocks[0].citation_metadata is None

    @pytest.mark.asyncio
    async def test_legacy_prov_page_no_is_none(self):
        """Legacy prov where the page has page_no=None."""
        converter = _make_converter()
        doc_dict = {
            "body": {
                "children": [{DOCLING_REF_NODE: "#/texts/0"}]
            },
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "text": "No page_no in legacy",
                    "prov": {DOCLING_REF_NODE: "#/pages/0"},
                }
            ],
            "pages": [
                {"page_no": None, "size": {"width": 100, "height": 200}},
            ],
        }
        doc = _make_doc_cov(doc_dict)
        result = await converter._process_content_in_order(doc)

        assert len(result.blocks) == 1
        # page_no is None so citation_metadata not set from prov
        assert result.blocks[0].citation_metadata is None


class TestBboxWithZeroDimensions:
    """Branch for page_width/page_height being 0."""

    @pytest.mark.asyncio
    async def test_zero_page_dimensions_skips_bbox(self):
        converter = _make_converter()
        doc_dict = {
            "body": {
                "children": [{DOCLING_REF_NODE: "#/texts/0"}]
            },
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "text": "Zero dimension page",
                    "prov": [
                        {
                            "page_no": 1,
                            "bbox": {"l": 0, "t": 10, "r": 100, "b": 50},
                        }
                    ],
                }
            ],
            "pages": {
                "1": {"size": {"width": 0, "height": 0}}
            },
        }
        doc = _make_doc_cov(doc_dict)
        result = await converter._process_content_in_order(doc)

        block = result.blocks[0]
        assert block.citation_metadata is not None
        assert block.citation_metadata.page_number == 1
        # bbox processing skipped due to zero dimensions
        assert block.citation_metadata.bounding_boxes is None
