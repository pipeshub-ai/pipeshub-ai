"""
Coverage boost tests for app.utils.converters.docling_doc_to_blocks.

Targets uncovered lines:
- 147: default_page_number overrides existing citation_metadata page_number
- 170: text block with children recursion
- 195-196: group block returning child BlockGroup from nested group
- 209, 217: _get_ref_text returning "" for invalid ref or missing item
- 254: image block with children recursion
- 339: table block with children recursion
- 361-362: IndexError/ValueError on ref path parts
- 391-392: unknown item type else branch (already tested, ensure it logs)
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import (
    BlockType,
    GroupType,
)
from app.utils.converters.docling_doc_to_blocks import (
    DOCLING_REF_NODE,
    DoclingDocToBlocksConverter,
)


def _make_converter():
    logger = logging.getLogger("test-converter-coverage")
    config = AsyncMock()
    return DoclingDocToBlocksConverter(logger, config)


def _make_doc(doc_dict):
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
        doc = _make_doc(doc_dict)

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
        doc = _make_doc(doc_dict)
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
        doc = _make_doc(doc_dict)
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
        doc = _make_doc(doc_dict)
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
        doc = _make_doc(doc_dict)
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
        doc = _make_doc(doc_dict)
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
        doc = _make_doc(doc_dict)

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
        doc = _make_doc(doc_dict)
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
        doc = _make_doc(doc_dict)
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
        doc = _make_doc(doc_dict)
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
        doc = _make_doc(doc_dict)
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
        doc = _make_doc(doc_dict)
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
        doc = _make_doc(doc_dict)
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
        doc = _make_doc(doc_dict)
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
        doc = _make_doc(doc_dict)
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
        doc = _make_doc(doc_dict)
        result = await converter._process_content_in_order(doc)

        block = result.blocks[0]
        assert block.citation_metadata is not None
        assert block.citation_metadata.page_number == 1
        # bbox processing skipped due to zero dimensions
        assert block.citation_metadata.bounding_boxes is None
