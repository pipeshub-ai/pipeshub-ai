"""Tests for Confluence Cloud block parser URL resolution."""

import json
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

    def test_nested_table_children_ranges_shift_after_content_insert(self):
        """Nested table group indices in table.children stay valid after content insert."""
        parser = _parser()

        outer_row = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            parent_index=0,
            data={"cells": ["a"], "row_number": 0},
            table_row_metadata=TableRowMetadata(row_number=0, is_header=True),
        )
        nested_row = Block(
            index=1,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            parent_index=1,
            data={"cells": ["nested"], "row_number": 0},
            table_row_metadata=TableRowMetadata(row_number=0, is_header=False),
        )
        nested_table = BlockGroup(
            index=1,
            type=GroupType.TABLE,
            parent_index=0,
            children=BlockGroupChildren.from_indices(block_indices=[1]),
        )
        outer_table = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            children=BlockGroupChildren.from_indices(
                block_indices=[0],
                block_group_indices=[1],
            ),
        )
        content_group = BlockGroup(
            index=0,
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
            source_group_id="page_content",
        )
        blocks = [outer_row, nested_row]
        block_groups = [outer_table, nested_table]

        block_groups.insert(0, content_group)
        for i, bg in enumerate(block_groups):
            bg.index = i

        ConfluenceBlockParser.shift_parent_indices_after_group_insert(
            blocks, block_groups, insert_at=0
        )
        ConfluenceBlockParser.sync_table_row_links(blocks, block_groups)

        assert outer_table.index == 1
        assert nested_table.index == 2
        assert nested_table.parent_index == 1
        nested_ranges = outer_table.children.block_group_ranges
        assert any(r.start <= 2 <= r.end for r in nested_ranges)
        assert not any(r.start <= 1 <= r.end for r in nested_ranges)


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
        assert row_data["cells"] == ["A", ""]
        cell_details = row_data["cell_details"]
        
        # First cell: text only
        assert cell_details[0]["type"] == "text"
        assert cell_details[0]["text"] == "A"
        
        # Second cell: nested table
        assert cell_details[1]["type"] == "nested"
        assert len(cell_details[1]["block_group_indices"]) == 1
        assert cell_details[1]["block_group_indices"][0] == nested_table.index

    async def test_migration_nested_table_extension(self):
        """Migration nested-table extension stores inner table ADF as a JSON string."""
        parser = _parser()
        nested_table_adf = {
            "type": "table",
            "content": [{
                "type": "tableRow",
                "content": [{
                    "type": "tableCell",
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "inner"}]}]
                }]
            }]
        }
        adf = {
            "type": "doc",
            "content": [{
                "type": "table",
                "content": [{
                    "type": "tableRow",
                    "content": [
                        {
                            "type": "tableCell",
                            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "outer"}]}]
                        },
                        {
                            "type": "tableCell",
                            "content": [{
                                "type": "extension",
                                "attrs": {
                                    "extensionType": "com.atlassian.confluence.migration",
                                    "extensionKey": "nested-table",
                                    "parameters": {
                                        "adf": json.dumps(nested_table_adf),
                                    },
                                },
                            }]
                        },
                    ]
                }]
            }]
        }

        blocks, block_groups = await parser.parse_adf(adf, page_id="p1", page_title=None)

        table_groups = [g for g in block_groups if g.type == GroupType.TABLE]
        assert len(table_groups) == 2

        outer_table, nested_table = table_groups
        assert nested_table.parent_index == outer_table.index
        assert outer_table.children is not None
        assert any(
            r.start <= nested_table.index <= r.end
            for r in outer_table.children.block_group_ranges
        )

        outer_rows = [b for b in blocks if b.type == BlockType.TABLE_ROW and b.parent_index == outer_table.index]
        assert len(outer_rows) == 1
        cell_details = outer_rows[0].data["cell_details"]
        assert cell_details[1]["type"] == "nested"
        assert cell_details[1]["block_group_indices"] == [nested_table.index]

        nested_rows = [b for b in blocks if b.type == BlockType.TABLE_ROW and b.parent_index == nested_table.index]
        assert len(nested_rows) == 1
        assert nested_rows[0].data["cells"] == ["inner"]

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
        
        cell_details = table_rows[0].data["cell_details"]
        assert cell_details[0]["type"] == "nested"
        assert len(cell_details[0]["block_indices"]) > 0

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
        
        row_data = table_rows[0].data
        assert row_data["cells"] == ["Simple", "- item", "Also simple"]
        cell_details = row_data["cell_details"]
        assert len(cell_details) == 3
        
        # First cell: text only
        assert cell_details[0]["type"] == "text"
        assert cell_details[0]["text"] == "Simple"
        
        # Second cell: nested list
        assert cell_details[1]["type"] == "nested"
        assert len(cell_details[1]["block_indices"]) > 0
        
        # Third cell: text only
        assert cell_details[2]["type"] == "text"
        assert cell_details[2]["text"] == "Also simple"

    async def test_list_in_table_not_wrapped_in_list_group_after_post_process(self):
        """Table-cell list items stay under the TABLE group (no duplicate LIST group)."""
        parser = _parser()
        adf = {
            "type": "doc",
            "content": [{
                "type": "table",
                "content": [{
                    "type": "tableRow",
                    "content": [{
                        "type": "tableCell",
                        "content": [{
                            "type": "bulletList",
                            "content": [
                                {
                                    "type": "listItem",
                                    "content": [{
                                        "type": "paragraph",
                                        "content": [{"type": "text", "text": "item1"}],
                                    }],
                                },
                                {
                                    "type": "listItem",
                                    "content": [{
                                        "type": "paragraph",
                                        "content": [{"type": "text", "text": "item2"}],
                                    }],
                                },
                            ],
                        }],
                    }],
                }],
            }],
        }

        blocks, block_groups = await parser.parse_adf(adf, page_id="p1", page_title=None)
        parser.post_process_blocks(blocks, block_groups)

        table_group = next(g for g in block_groups if g.type == GroupType.TABLE)
        list_groups = [
            g for g in block_groups
            if g.type in (GroupType.LIST, GroupType.ORDERED_LIST)
        ]
        assert list_groups == []

        list_items = [b for b in blocks if b.list_metadata]
        assert len(list_items) == 2
        assert all(item.parent_index == table_group.index for item in list_items)

    async def test_page_level_list_still_grouped_after_post_process(self):
        """List items outside tables are still wrapped in LIST groups."""
        parser = _parser()
        adf = {
            "type": "doc",
            "content": [{
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [{
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "a"}],
                        }],
                    },
                    {
                        "type": "listItem",
                        "content": [{
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "b"}],
                        }],
                    },
                ],
            }],
        }

        blocks, block_groups = await parser.parse_adf(adf, page_id="p1", page_title=None)
        parser.post_process_blocks(blocks, block_groups)

        list_groups = [g for g in block_groups if g.type == GroupType.LIST]
        assert len(list_groups) == 1
        list_items = [b for b in blocks if b.list_metadata]
        assert len(list_items) == 2
        assert all(item.parent_index == list_groups[0].index for item in list_items)

    async def test_list_item_with_image_creates_image_block(self):
        parser = _parser()

        async def media_fetcher(_media_id: str, _filename: str) -> str:
            return "data:image/png;base64,YWJj"

        adf = {
            "type": "doc",
            "content": [{
                "type": "bulletList",
                "content": [{
                    "type": "listItem",
                    "content": [{
                        "type": "mediaSingle",
                        "content": [{
                            "type": "media",
                            "attrs": {
                                "id": "att-1",
                                "type": "file",
                                "alt": "diagram",
                                "width": 120,
                                "height": 80,
                            },
                        }],
                    }],
                }],
            }],
        }

        blocks, _ = await parser.parse_adf(
            adf,
            page_id="p1",
            page_title=None,
            media_fetcher=media_fetcher,
        )

        image_blocks = [b for b in blocks if b.type == BlockType.IMAGE]
        assert len(image_blocks) == 1

    async def test_table_cell_preserves_link_markdown(self):
        parser = _parser()
        adf = {
            "type": "doc",
            "content": [{
                "type": "table",
                "content": [{
                    "type": "tableRow",
                    "content": [{
                        "type": "tableCell",
                        "content": [{
                            "type": "paragraph",
                            "content": [{
                                "type": "text",
                                "text": "Example link",
                                "marks": [{
                                    "type": "link",
                                    "attrs": {"href": "https://example.com"},
                                }],
                            }],
                        }],
                    }],
                }],
            }],
        }

        blocks, _ = await parser.parse_adf(adf, page_id="p1", page_title=None)
        row = next(b for b in blocks if b.type == BlockType.TABLE_ROW)
        cell_text = row.data["cell_details"][0]["text"]
        assert "[Example link](https://example.com)" in cell_text


class TestInlineCommentBlockMatching:
    def test_find_block_by_text_matches_table_row(self):
        parser = _parser()
        row = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data={
                "cells": ["hello world"],
                "row_natural_language_text": "hello world",
                "cell_details": [{"type": "text", "text": "hello world", "is_header": False}],
            },
        )

        assert parser._find_block_by_text([row], "hello world") == row

    def test_find_block_by_text_matches_list_item(self):
        parser = _parser()
        item = Block(
            index=1,
            type=BlockType.TEXT,
            format=DataFormat.MARKDOWN,
            data="- bullet item text",
            list_metadata=ListMetadata(list_style="bullet", indent_level=1),
        )

        assert parser._find_block_by_text([item], "bullet item") == item
