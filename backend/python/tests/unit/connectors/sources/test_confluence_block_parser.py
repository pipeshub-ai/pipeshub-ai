"""Tests for Confluence Cloud block parser URL resolution."""

from unittest.mock import MagicMock

import pytest

from app.connectors.sources.atlassian.confluence_cloud.block_parser import (
    ConfluenceBlockParser,
)
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlockType,
    DataFormat,
    GroupSubType,
    GroupType,
    ListMetadata,
    TableRowMetadata,
)


def _parser() -> ConfluenceBlockParser:
    return ConfluenceBlockParser(logger=MagicMock())


class TestResolveCommentWeburl:
    def test_relative_webui_with_parent_page_url(self):
        links = {
            "webui": "/spaces/~71202001983f111/pages/123?focusedCommentId=338591761",
        }
        parent = "https://acme.atlassian.net/wiki/spaces/TEST/pages/123"
        url = ConfluenceBlockParser._resolve_comment_weburl(links, parent)
        assert url == (
            "https://acme.atlassian.net/wiki"
            "/spaces/~71202001983f111/pages/123?focusedCommentId=338591761"
        )

    def test_links_base(self):
        links = {
            "webui": "/spaces/ENG/pages/456",
            "base": "https://acme.atlassian.net/wiki",
        }
        url = ConfluenceBlockParser._resolve_comment_weburl(links, None)
        assert url == "https://acme.atlassian.net/wiki/spaces/ENG/pages/456"

    def test_self_link_fallback(self):
        links = {
            "webui": "/spaces/ENG/pages/456",
            "self": "https://acme.atlassian.net/wiki/rest/api/content/456",
        }
        url = ConfluenceBlockParser._resolve_comment_weburl(links, None)
        assert url == "https://acme.atlassian.net/wiki/spaces/ENG/pages/456"

    def test_absolute_webui_unchanged(self):
        links = {"webui": "https://acme.atlassian.net/wiki/spaces/ENG/pages/1"}
        url = ConfluenceBlockParser._resolve_comment_weburl(links, None)
        assert url == "https://acme.atlassian.net/wiki/spaces/ENG/pages/1"

    def test_no_resolution_returns_none(self):
        links = {"webui": "/spaces/ENG/pages/456"}
        assert ConfluenceBlockParser._resolve_comment_weburl(links, None) is None


@pytest.mark.asyncio
class TestParseConfluenceCommentToBlockComment:
    async def test_relative_webui_builds_valid_block_comment(self):
        parser = _parser()
        comment = {
            "id": "338591761",
            "createdAt": "2026-01-15T10:00:00.000Z",
            "version": {"authorId": "user-1"},
            "resolutionStatus": "open",
            "body": {
                "atlas_doc_format": {
                    "value": '{"type":"doc","version":1,"content":[{"type":"paragraph","content":[{"type":"text","text":"Hello"}]}]}',
                },
            },
            "_links": {
                "webui": "/spaces/TEST/pages/1?focusedCommentId=338591761",
            },
        }
        parent = "https://acme.atlassian.net/wiki/spaces/TEST/pages/1"

        block_comment = await parser._parse_confluence_comment_to_block_comment(
            comment,
            parent_page_url=parent,
        )

        assert block_comment is not None
        assert "Hello" in block_comment.text
        assert block_comment.format == DataFormat.MARKDOWN
        assert str(block_comment.weburl).startswith("https://acme.atlassian.net/wiki/")


@pytest.mark.asyncio
class TestParseAdfPageTitleAndTableChildren:
    async def test_page_title_before_body_keeps_table_children_aligned(self):
        """Title as block 0 during parse so table block_ranges match table_row indices."""
        parser = _parser()
        adf = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 3},
                    "content": [{"type": "text", "text": "Sync Filters"}],
                },
                {
                    "type": "table",
                    "attrs": {"localId": "table-1"},
                    "content": [
                        {
                            "type": "tableRow",
                            "content": [
                                {
                                    "type": "tableHeader",
                                    "content": [
                                        {
                                            "type": "paragraph",
                                            "content": [{"type": "text", "text": "A"}],
                                        }
                                    ],
                                },
                                {
                                    "type": "tableHeader",
                                    "content": [
                                        {
                                            "type": "paragraph",
                                            "content": [{"type": "text", "text": "B"}],
                                        }
                                    ],
                                },
                            ],
                        },
                        {
                            "type": "tableRow",
                            "content": [
                                {
                                    "type": "tableCell",
                                    "content": [
                                        {
                                            "type": "paragraph",
                                            "content": [{"type": "text", "text": "1"}],
                                        }
                                    ],
                                },
                                {
                                    "type": "tableCell",
                                    "content": [
                                        {
                                            "type": "paragraph",
                                            "content": [{"type": "text", "text": "2"}],
                                        }
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ],
        }

        blocks, block_groups = await parser.parse_adf(
            adf_content=adf,
            page_id="page-1",
            page_title="Linear",
            parent_page_url="https://acme.atlassian.net/wiki/spaces/SD/pages/1/Linear",
        )
        parser.post_process_blocks(blocks, block_groups)

        assert blocks[0].type == BlockType.TEXT
        assert blocks[0].data == "# Linear"

        table_groups = [g for g in block_groups if g.type == GroupType.TABLE]
        assert len(table_groups) == 1
        table_group = table_groups[0]

        row_indices = [
            b.index
            for b in blocks
            if b.type == BlockType.TABLE_ROW and b.parent_index == table_group.index
        ]
        assert row_indices

        assert table_group.children is not None
        range_starts = [r.start for r in table_group.children.block_ranges]
        range_ends = [r.end for r in table_group.children.block_ranges]
        assert min(range_starts) == min(row_indices)
        assert max(range_ends) == max(row_indices)

        first_range_start = table_group.children.block_ranges[0].start
        first_row_block = blocks[first_range_start]
        assert first_row_block.type == BlockType.TABLE_ROW
        assert isinstance(first_row_block.data, dict)
        assert table_group.table_metadata.has_header is True
        assert first_row_block.table_row_metadata.is_header is True
        assert first_row_block.data["row_number"] == 1


class TestTableRowParentIndexAfterContentInsert:
    def test_shift_and_sync_after_content_wrapper_insert(self):
        """Table rows keep parent_index on the TABLE group after content insert at 0."""
        parser = _parser()

        row_a = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            parent_index=0,
            data={"cells": ["h1", "h2"], "row_number": 0},
            table_row_metadata=TableRowMetadata(row_number=0, is_header=True),
        )
        row_b = Block(
            index=1,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            parent_index=0,
            data={"cells": ["1", "2"], "row_number": 0},
            table_row_metadata=TableRowMetadata(row_number=0, is_header=False),
        )
        table_group = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            children=BlockGroupChildren.from_indices(block_indices=[0, 1]),
        )
        content_group = BlockGroup(
            index=0,
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
            source_group_id="page_content",
        )
        blocks = [row_a, row_b]
        block_groups = [table_group]

        block_groups.insert(0, content_group)
        for i, bg in enumerate(block_groups):
            bg.index = i

        ConfluenceBlockParser.shift_parent_indices_after_group_insert(
            blocks, block_groups, insert_at=0
        )
        ConfluenceBlockParser.sync_table_row_links(blocks, block_groups)

        assert table_group.index == 1
        assert row_a.parent_index == 1
        assert row_b.parent_index == 1
        assert row_a.data["row_number"] == 1
        assert row_b.data["row_number"] == 2
        assert row_a.table_row_metadata.row_number == 1
        assert row_b.table_row_metadata.row_number == 2

    def test_comment_thread_parent_stays_on_content_after_insert(self):
        thread = BlockGroup(
            index=2,
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.COMMENT_THREAD,
            parent_index=0,
        )
        comment = BlockGroup(
            index=3,
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.COMMENT,
            parent_index=2,
        )
        content_group = BlockGroup(
            index=0,
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
        )
        block_groups = [thread, comment]
        block_groups.insert(0, content_group)
        for i, bg in enumerate(block_groups):
            bg.index = i

        ConfluenceBlockParser.shift_parent_indices_after_group_insert(
            [], block_groups, insert_at=0
        )

        assert thread.parent_index == 0
        assert comment.parent_index == 3


class TestFixNumberedListNumbering:
    def test_nested_list_restarts_after_returning_to_shallower_level(self):
        parser = _parser()
        blocks = [
            Block(
                index=0,
                type=BlockType.TEXT,
                format=DataFormat.MARKDOWN,
                data="1. Top A",
                list_metadata=ListMetadata(list_style="numbered", indent_level=0),
            ),
            Block(
                index=1,
                type=BlockType.TEXT,
                format=DataFormat.MARKDOWN,
                data="1. Nested A",
                list_metadata=ListMetadata(list_style="numbered", indent_level=1),
            ),
            Block(
                index=2,
                type=BlockType.TEXT,
                format=DataFormat.MARKDOWN,
                data="1. Nested B",
                list_metadata=ListMetadata(list_style="numbered", indent_level=1),
            ),
            Block(
                index=3,
                type=BlockType.TEXT,
                format=DataFormat.MARKDOWN,
                data="1. Top B",
                list_metadata=ListMetadata(list_style="numbered", indent_level=0),
            ),
            Block(
                index=4,
                type=BlockType.TEXT,
                format=DataFormat.MARKDOWN,
                data="1. Nested C",
                list_metadata=ListMetadata(list_style="numbered", indent_level=1),
            ),
        ]

        parser._fix_numbered_list_numbering(blocks)

        assert blocks[0].data == "1. Top A"
        assert blocks[1].data == "1. Nested A"
        assert blocks[2].data == "2. Nested B"
        assert blocks[3].data == "2. Top B"
        assert blocks[4].data == "1. Nested C"


@pytest.mark.asyncio
class TestNestedTableInCell:
    async def test_nested_table_creates_block_groups(self):
        """Nested table in cell is parsed as BlockGroup, not flattened to text."""
        parser = _parser()
        adf = {
            "type": "doc",
            "content": [{
                "type": "table",
                "content": [{
                    "type": "tableRow",
                    "content": [
                        {
                            "type": "tableCell",
                            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "A"}]}]
                        },
                        {
                            "type": "tableCell",
                            "content": [{
                                "type": "table",
                                "content": [{
                                    "type": "tableRow",
                                    "content": [{
                                        "type": "tableCell",
                                        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "nested"}]}]
                                    }]
                                }]
                            }]
                        }
                    ]
                }]
            }]
        }
        
        blocks, block_groups = await parser.parse_adf(adf, page_id="p1", page_title=None)
        
        # Should have 2 TABLE groups (outer + nested)
        table_groups = [g for g in block_groups if g.type == GroupType.TABLE]
        assert len(table_groups) == 2
        
        outer_table = table_groups[0]
        nested_table = table_groups[1]
        
        # Nested table parent_index should point to outer table
        assert nested_table.parent_index == outer_table.index
        
        # Outer table row should have nested cell data
        outer_rows = [b for b in blocks if b.type == BlockType.TABLE_ROW and b.parent_index == outer_table.index]
        assert len(outer_rows) == 1
        
        row_data = outer_rows[0].data
        assert isinstance(row_data, dict)
        cells = row_data["cells"]
        
        # First cell: text only
        assert cells[0]["type"] == "text"
        assert cells[0]["text"] == "A"
        
        # Second cell: nested table
        assert cells[1]["type"] == "nested"
        assert len(cells[1]["block_group_indices"]) == 1
        assert cells[1]["block_group_indices"][0] == nested_table.index

    async def test_nested_list_in_cell(self):
        """List in cell is parsed as blocks, not flattened to text."""
        parser = _parser()
        adf = {
            "type": "doc",
            "content": [{
                "type": "table",
                "content": [{
                    "type": "tableRow",
                    "content": [
                        {
                            "type": "tableCell",
                            "content": [{
                                "type": "bulletList",
                                "content": [
                                    {
                                        "type": "listItem",
                                        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "item1"}]}]
                                    },
                                    {
                                        "type": "listItem",
                                        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "item2"}]}]
                                    }
                                ]
                            }]
                        }
                    ]
                }]
            }]
        }
        
        blocks, block_groups = await parser.parse_adf(adf, page_id="p1", page_title=None)
        
        # Check that cell has nested content with text blocks
        table_rows = [b for b in blocks if b.type == BlockType.TABLE_ROW]
        assert len(table_rows) == 1
        
        cells = table_rows[0].data["cells"]
        assert cells[0]["type"] == "nested"
        assert len(cells[0]["block_indices"]) > 0

    async def test_mixed_text_and_nested_cells(self):
        """Table row with both simple text and nested content cells."""
        parser = _parser()
        adf = {
            "type": "doc",
            "content": [{
                "type": "table",
                "content": [{
                    "type": "tableRow",
                    "content": [
                        {
                            "type": "tableCell",
                            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Simple"}]}]
                        },
                        {
                            "type": "tableCell",
                            "content": [{
                                "type": "bulletList",
                                "content": [{
                                    "type": "listItem",
                                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "item"}]}]
                                }]
                            }]
                        },
                        {
                            "type": "tableCell",
                            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Also simple"}]}]
                        }
                    ]
                }]
            }]
        }
        
        blocks, block_groups = await parser.parse_adf(adf, page_id="p1", page_title=None)
        
        table_rows = [b for b in blocks if b.type == BlockType.TABLE_ROW]
        assert len(table_rows) == 1
        
        cells = table_rows[0].data["cells"]
        assert len(cells) == 3
        
        # First cell: text only
        assert cells[0]["type"] == "text"
        assert cells[0]["text"] == "Simple"
        
        # Second cell: nested list
        assert cells[1]["type"] == "nested"
        assert len(cells[1]["block_indices"]) > 0
        
        # Third cell: text only
        assert cells[2]["type"] == "text"
        assert cells[2]["text"] == "Also simple"
