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
    BlockSubType,
    BlockType,
    DataFormat,
    GroupSubType,
    GroupType,
    ListMetadata,
    TableMetadata,
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


@pytest.mark.asyncio
class TestPanelExpandTableNesting:
    """Regression tests for parent_index cycle bug (panel/expand containing tables)."""

    async def test_panel_with_nested_table_no_self_reference(self):
        """Panel containing table should not create self-referencing parent_index."""
        parser = _parser()
        adf = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "panel",
                    "attrs": {"panelType": "info"},
                    "content": [
                        {
                            "type": "table",
                            "content": [
                                {
                                    "type": "tableRow",
                                    "content": [
                                        {
                                            "type": "tableCell",
                                            "content": [
                                                {
                                                    "type": "paragraph",
                                                    "content": [
                                                        {"type": "text", "text": "Cell"}
                                                    ],
                                                }
                                            ],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        }

        blocks, block_groups = await parser.parse_adf(
            adf, page_title="Test", page_id="123"
        )

        # Verify no self-references
        for i, group in enumerate(block_groups):
            assert group.parent_index != i, (
                f"block_group[{i}] has self-referencing parent_index={group.parent_index}"
            )

        # Verify panel group exists
        panel_groups = [g for g in block_groups if g.sub_type == GroupSubType.CALLOUT]
        assert len(panel_groups) == 1

        # Verify table group exists and has correct parent
        table_groups = [g for g in block_groups if g.type == GroupType.TABLE]
        assert len(table_groups) == 1
        assert table_groups[0].parent_index == panel_groups[0].index

    async def test_expand_with_nested_layout_no_self_reference(self):
        """Expand containing layoutSection should not create self-referencing parent_index."""
        parser = _parser()
        adf = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "expand",
                    "attrs": {"title": "Details"},
                    "content": [
                        {
                            "type": "layoutSection",
                            "content": [
                                {
                                    "type": "layoutColumn",
                                    "attrs": {"width": 50.0},
                                    "content": [
                                        {
                                            "type": "paragraph",
                                            "content": [
                                                {"type": "text", "text": "Column 1"}
                                            ],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        }

        blocks, block_groups = await parser.parse_adf(
            adf, page_title="Test", page_id="123"
        )

        # Verify no self-references
        for i, group in enumerate(block_groups):
            assert group.parent_index != i, (
                f"block_group[{i}] has self-referencing parent_index={group.parent_index}"
            )

        # Verify expand group exists
        expand_groups = [g for g in block_groups if g.sub_type == GroupSubType.TOGGLE]
        assert len(expand_groups) == 1

        # Verify column list exists and has correct parent
        column_list_groups = [g for g in block_groups if g.type == GroupType.COLUMN_LIST]
        assert len(column_list_groups) == 1
        assert column_list_groups[0].parent_index == expand_groups[0].index

    async def test_blockquote_with_nested_table_no_self_reference(self):
        """Blockquote containing table should not create self-referencing parent_index."""
        parser = _parser()
        adf = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "blockquote",
                    "content": [
                        {
                            "type": "table",
                            "content": [
                                {
                                    "type": "tableRow",
                                    "content": [
                                        {
                                            "type": "tableCell",
                                            "content": [
                                                {
                                                    "type": "paragraph",
                                                    "content": [
                                                        {"type": "text", "text": "Quote table"}
                                                    ],
                                                }
                                            ],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        }

        blocks, block_groups = await parser.parse_adf(
            adf, page_title="Test", page_id="123"
        )

        # Verify no self-references
        for i, group in enumerate(block_groups):
            assert group.parent_index != i, (
                f"block_group[{i}] has self-referencing parent_index={group.parent_index}"
            )

        # Verify quote group exists
        quote_groups = [g for g in block_groups if g.sub_type == GroupSubType.QUOTE]
        assert len(quote_groups) == 1

        # Verify table group exists and has correct parent
        table_groups = [g for g in block_groups if g.type == GroupType.TABLE]
        assert len(table_groups) == 1
        assert table_groups[0].parent_index == quote_groups[0].index

    async def test_nested_panel_in_expand_no_self_reference(self):
        """Complex nesting: expand containing panel containing table."""
        parser = _parser()
        adf = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "expand",
                    "attrs": {"title": "More Details"},
                    "content": [
                        {
                            "type": "panel",
                            "attrs": {"panelType": "warning"},
                            "content": [
                                {
                                    "type": "table",
                                    "content": [
                                        {
                                            "type": "tableRow",
                                            "content": [
                                                {
                                                    "type": "tableCell",
                                                    "content": [
                                                        {
                                                            "type": "paragraph",
                                                            "content": [
                                                                {"type": "text", "text": "Nested"}
                                                            ],
                                                        }
                                                    ],
                                                }
                                            ],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        }

        blocks, block_groups = await parser.parse_adf(
            adf, page_title="Test", page_id="123"
        )

        # Verify no self-references in any group
        for i, group in enumerate(block_groups):
            assert group.parent_index != i, (
                f"block_group[{i}] has self-referencing parent_index={group.parent_index}"
            )

        # Verify hierarchy: expand -> panel -> table
        expand_groups = [g for g in block_groups if g.sub_type == GroupSubType.TOGGLE]
        panel_groups = [g for g in block_groups if g.sub_type == GroupSubType.CALLOUT]
        table_groups = [g for g in block_groups if g.type == GroupType.TABLE]

        assert len(expand_groups) == 1
        assert len(panel_groups) == 1
        assert len(table_groups) == 1

        # Verify parent chain
        assert panel_groups[0].parent_index == expand_groups[0].index
        assert table_groups[0].parent_index == panel_groups[0].index

class TestNormalizeUrl:
    def test_none_returns_none(self):
        assert ConfluenceBlockParser._normalize_url(None) is None

    def test_empty_string_returns_none(self):
        assert ConfluenceBlockParser._normalize_url("") is None

    def test_whitespace_returns_none(self):
        assert ConfluenceBlockParser._normalize_url("   ") is None

    def test_valid_url_unchanged(self):
        url = "https://acme.atlassian.net/wiki/spaces/X/pages/1"
        assert ConfluenceBlockParser._normalize_url(url) == url


class TestConstructBlockUrl:
    def test_returns_parent_page_url(self):
        parser = _parser()
        parent = "https://acme.atlassian.net/wiki/spaces/X/pages/1"
        assert parser._construct_block_url(parent, "node-1") == parent

    def test_none_when_no_parent(self):
        parser = _parser()
        assert parser._construct_block_url(None, "node-1") is None

    def test_create_title_block_includes_weburl(self):
        parser = _parser()
        block = parser.create_title_block(
            "Page Title",
            page_id="p1",
            weburl="https://acme.atlassian.net/wiki/spaces/X/pages/1",
        )
        assert block.data == "# Page Title"
        assert str(block.weburl).startswith("https://")


class TestResolveCommentWeburlEdgeCases:
    def test_none_links(self):
        assert ConfluenceBlockParser._resolve_comment_weburl(None, None) is None

    def test_empty_webui(self):
        assert ConfluenceBlockParser._resolve_comment_weburl({"webui": ""}, None) is None

    def test_whitespace_webui(self):
        assert ConfluenceBlockParser._resolve_comment_weburl({"webui": "   "}, None) is None


@pytest.mark.asyncio
class TestParseAdfEdgeCases:
    async def test_none_adf_content(self):
        parser = _parser()
        blocks, groups = await parser.parse_adf(None, page_id="p1")
        assert blocks == []
        assert groups == []

    async def test_non_dict_adf_content(self):
        parser = _parser()
        blocks, groups = await parser.parse_adf("not a dict", page_id="p1")
        assert blocks == []
        assert groups == []

    async def test_empty_dict(self):
        parser = _parser()
        blocks, groups = await parser.parse_adf({}, page_id="p1")
        assert blocks == []
        assert groups == []

    async def test_title_skipped_when_page_id_missing(self):
        parser = _parser()
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "text", "text": "Body"}],
            }],
        }
        blocks, _ = await parser.parse_adf(adf, page_title="My Page", page_id=None)
        assert all(b.data != "# My Page" for b in blocks)

    async def test_title_skipped_when_whitespace_only(self):
        parser = _parser()
        adf = {"type": "doc", "content": []}
        blocks, _ = await parser.parse_adf(adf, page_title="   ", page_id="p1")
        assert blocks == []


@pytest.mark.asyncio
class TestProcessNodeRecursive:
    async def test_non_dict_node(self):
        parser = _parser()
        blocks: list[Block] = []
        groups: list[BlockGroup] = []
        result = await parser._process_node_recursive(
            node="invalid",  # type: ignore[arg-type]
            blocks=blocks,
            block_groups=groups,
            parent_group_index=None,
            media_fetcher=None,
            parent_page_url=None,
            page_id=None,
        )
        assert result == []

    async def test_empty_type(self):
        parser = _parser()
        blocks: list[Block] = []
        groups: list[BlockGroup] = []
        result = await parser._process_node_recursive(
            node={"type": ""},
            blocks=blocks,
            block_groups=groups,
            parent_group_index=None,
            media_fetcher=None,
            parent_page_url=None,
            page_id=None,
        )
        assert result == []

    async def test_parser_exception_returns_empty(self):
        parser = _parser()

        async def boom(**_kwargs):
            raise RuntimeError("parse failed")

        parser._parse_paragraph = boom  # type: ignore[method-assign]
        blocks: list[Block] = []
        groups: list[BlockGroup] = []
        result = await parser._process_node_recursive(
            node={"type": "paragraph", "content": [{"type": "text", "text": "x"}]},
            blocks=blocks,
            block_groups=groups,
            parent_group_index=None,
            media_fetcher=None,
            parent_page_url=None,
            page_id=None,
        )
        assert result == []
        parser.logger.warning.assert_called()


class TestExtractTextFromContent:
    def test_empty_content(self):
        parser = _parser()
        assert parser._extract_text_from_content([]) == ""

    def test_hard_break(self):
        parser = _parser()
        content = [{"type": "hardBreak"}]
        assert parser._extract_text_from_content(content) == "\n"

    def test_mention_with_text(self):
        parser = _parser()
        content = [{"type": "mention", "attrs": {"text": "Alice"}}]
        assert parser._extract_text_from_content(content) == "@Alice"

    def test_mention_fallback_to_id(self):
        parser = _parser()
        content = [{"type": "mention", "attrs": {"id": "user-42"}}]
        assert parser._extract_text_from_content(content) == "@user-42"

    def test_emoji_short_name(self):
        parser = _parser()
        content = [{"type": "emoji", "attrs": {"shortName": "smile"}}]
        assert parser._extract_text_from_content(content) == ":smile:"

    def test_emoji_text_fallback(self):
        parser = _parser()
        content = [{"type": "emoji", "attrs": {"text": "😀"}}]
        assert parser._extract_text_from_content(content) == "😀"

    def test_inline_card(self):
        parser = _parser()
        content = [{"type": "inlineCard", "attrs": {"url": "https://example.com"}}]
        assert "[https://example.com]" in parser._extract_text_from_content(content)

    def test_status(self):
        parser = _parser()
        content = [{"type": "status", "attrs": {"text": "In Progress"}}]
        assert parser._extract_text_from_content(content) == "[In Progress]"

    def test_date_valid_timestamp(self):
        parser = _parser()
        # 2020-01-01 UTC in ms
        content = [{"type": "date", "attrs": {"timestamp": "1577836800000"}}]
        assert "2020-01-01" in parser._extract_text_from_content(content)

    def test_date_invalid_timestamp(self):
        parser = _parser()
        content = [{"type": "date", "attrs": {"timestamp": "not-a-number"}}]
        assert parser._extract_text_from_content(content) == "not-a-number"

    def test_strip_marks(self):
        parser = _parser()
        content = [{
            "type": "text",
            "text": "bold",
            "marks": [{"type": "strong"}],
        }]
        assert parser._extract_text_from_content(content, strip_marks=True) == "bold"

    def test_nested_content_recursion(self):
        parser = _parser()
        content = [{
            "type": "unknownWrapper",
            "content": [{"type": "text", "text": "nested"}],
        }]
        assert parser._extract_text_from_content(content) == "nested"


class TestIsInlineOnlyContent:
    def test_empty_is_inline(self):
        parser = _parser()
        assert parser._is_inline_only_content([]) is True

    def test_non_dict_skipped(self):
        parser = _parser()
        assert parser._is_inline_only_content(["not a dict"]) is True  # type: ignore[list-item]

    def test_paragraph_with_block_child(self):
        parser = _parser()
        content = [{
            "type": "paragraph",
            "content": [{"type": "table", "content": []}],
        }]
        assert parser._is_inline_only_content(content) is False

    def test_inline_nodes_only(self):
        parser = _parser()
        content = [
            {"type": "text", "text": "hi"},
            {"type": "mention", "attrs": {"text": "Bob"}},
        ]
        assert parser._is_inline_only_content(content) is True


class TestExtractTextFromBlocks:
    def test_out_of_range_index_skipped(self):
        parser = _parser()
        from app.models.blocks import BlockContainerIndex

        blocks = [Block(index=0, type=BlockType.TEXT, format=DataFormat.MARKDOWN, data="ok")]
        indices = [BlockContainerIndex(block_index=99)]
        assert parser._extract_text_from_blocks(blocks, indices) == ""

    def test_dict_with_text_key(self):
        parser = _parser()
        from app.models.blocks import BlockContainerIndex

        blocks = [Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data={"text": "cell text"},
        )]
        indices = [BlockContainerIndex(block_index=0)]
        assert parser._extract_text_from_blocks(blocks, indices) == "cell text"

    def test_dict_with_row_natural_language_text(self):
        parser = _parser()
        from app.models.blocks import BlockContainerIndex

        blocks = [Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data={"row_natural_language_text": "row nl"},
        )]
        indices = [BlockContainerIndex(block_index=0)]
        assert parser._extract_text_from_blocks(blocks, indices) == "row nl"


class TestApplyMarks:
    def test_no_marks(self):
        parser = _parser()
        assert parser._apply_marks("plain", []) == "plain"

    def test_strong(self):
        parser = _parser()
        assert parser._apply_marks("x", [{"type": "strong"}]) == "**x**"

    def test_em(self):
        parser = _parser()
        assert parser._apply_marks("x", [{"type": "em"}]) == "*x*"

    def test_code(self):
        parser = _parser()
        assert parser._apply_marks("x", [{"type": "code"}]) == "`x`"

    def test_strike(self):
        parser = _parser()
        assert parser._apply_marks("x", [{"type": "strike"}]) == "~~x~~"

    def test_link_with_href(self):
        parser = _parser()
        marks = [{"type": "link", "attrs": {"href": "https://x.com"}}]
        assert parser._apply_marks("click", marks) == "[click](https://x.com)"

    def test_link_without_href(self):
        parser = _parser()
        marks = [{"type": "link", "attrs": {}}]
        assert parser._apply_marks("click", marks) == "click"

    def test_underline(self):
        parser = _parser()
        assert parser._apply_marks("x", [{"type": "underline"}]) == "<u>x</u>"

    def test_text_color(self):
        parser = _parser()
        marks = [{"type": "textColor", "attrs": {"color": "#ff0000"}}]
        result = parser._apply_marks("red", marks)
        assert 'color: #ff0000' in result
        assert "red" in result

    def test_multiple_marks(self):
        parser = _parser()
        marks = [{"type": "em"}, {"type": "strong"}]
        result = parser._apply_marks("x", marks)
        assert "**" in result and "*" in result


@pytest.mark.asyncio
class TestTextBlockParsers:
    async def test_empty_paragraph_skipped(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "   "}]}],
        }, page_id="p1")
        assert blocks == []

    async def test_empty_heading_skipped(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "heading",
                "attrs": {"level": 2},
                "content": [{"type": "text", "text": ""}],
            }],
        }, page_id="p1")
        assert blocks == []

    async def test_heading_level_two(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "heading",
                "attrs": {"level": 2},
                "content": [{"type": "text", "text": "Sub"}],
            }],
        }, page_id="p1")
        assert blocks[0].data == "## Sub"

    async def test_codeblock_with_language(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "codeBlock",
                "attrs": {"language": "python"},
                "content": [{"type": "text", "text": "print(1)"}],
            }],
        }, page_id="p1")
        assert blocks[0].sub_type == BlockSubType.CODE
        assert blocks[0].code_metadata.language == "python"

    async def test_empty_codeblock_skipped(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{"type": "codeBlock", "attrs": {}, "content": []}],
        }, page_id="p1")
        assert blocks == []

    async def test_standalone_text_node(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "text",
                "text": "root text",
                "marks": [{"type": "strong"}],
            }],
        }, page_id="p1")
        assert blocks[0].data == "**root text**"

    async def test_blockquote_empty_content_creates_group(self):
        parser = _parser()
        _, groups = await parser.parse_adf({
            "type": "doc",
            "content": [{"type": "blockquote", "content": []}],
        }, page_id="p1")
        quote_groups = [g for g in groups if g.sub_type == GroupSubType.QUOTE]
        assert len(quote_groups) == 1


@pytest.mark.asyncio
class TestListAndTaskParsers:
    async def test_ordered_list_items(self):
        parser = _parser()
        blocks, groups = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "orderedList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [{
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "one"}],
                        }],
                    },
                    {
                        "type": "listItem",
                        "content": [{
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "two"}],
                        }],
                    },
                ],
            }],
        }, page_id="p1")
        parser.post_process_blocks(blocks, groups)
        ordered = [g for g in groups if g.type == GroupType.ORDERED_LIST]
        assert len(ordered) == 1
        assert len([b for b in blocks if b.list_metadata]) == 2

    async def test_nested_bullet_in_list_item(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "bulletList",
                "content": [{
                    "type": "listItem",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "parent"}],
                        },
                        {
                            "type": "bulletList",
                            "content": [{
                                "type": "listItem",
                                "content": [{
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "child"}],
                                }],
                            }],
                        },
                    ],
                }],
            }],
        }, page_id="p1")
        items = [b for b in blocks if b.list_metadata]
        assert len(items) >= 2

    async def test_task_item_todo(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "taskList",
                "content": [{
                    "type": "taskItem",
                    "attrs": {"state": "TODO"},
                    "content": [{
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "Do thing"}],
                    }],
                }],
            }],
        }, page_id="p1")
        assert "[ ]" in blocks[0].data

    async def test_task_item_done(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "taskList",
                "content": [{
                    "type": "taskItem",
                    "attrs": {"state": "DONE"},
                    "content": [{
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "Done thing"}],
                    }],
                }],
            }],
        }, page_id="p1")
        assert "[x]" in blocks[0].data

    async def test_decision_item_decided(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "decisionList",
                "content": [{
                    "type": "decisionItem",
                    "attrs": {"state": "DECIDED"},
                    "content": [{
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "Approved"}],
                    }],
                }],
            }],
        }, page_id="p1")
        assert blocks[0].data.startswith("✓")

    async def test_decision_item_undecided(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "decisionList",
                "content": [{
                    "type": "decisionItem",
                    "attrs": {"state": "UNDECIDED"},
                    "content": [{
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "Pending"}],
                    }],
                }],
            }],
        }, page_id="p1")
        assert blocks[0].data.startswith("◇")


@pytest.mark.asyncio
class TestMediaParsers:
    async def test_media_no_id_or_filename(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "mediaSingle",
                "content": [{"type": "media", "attrs": {}}],
            }],
        }, page_id="p1")
        assert not any(b.type == BlockType.IMAGE for b in blocks)

    async def test_media_no_fetcher(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "mediaSingle",
                "content": [{
                    "type": "media",
                    "attrs": {"id": "att-1", "alt": "pic"},
                }],
            }],
        }, page_id="p1")
        assert not any(b.type == BlockType.IMAGE for b in blocks)

    async def test_media_fetcher_raises(self):
        parser = _parser()

        async def fail_fetcher(_mid: str, _fn: str) -> str:
            raise OSError("network error")

        blocks, _ = await parser.parse_adf(
            {
                "type": "doc",
                "content": [{
                    "type": "mediaSingle",
                    "content": [{
                        "type": "media",
                        "attrs": {"id": "att-1", "alt": "pic"},
                    }],
                }],
            },
            page_id="p1",
            media_fetcher=fail_fetcher,
        )
        assert not any(b.type == BlockType.IMAGE for b in blocks)
        parser.logger.warning.assert_called()

    async def test_media_fetcher_returns_none(self):
        parser = _parser()

        async def none_fetcher(_mid: str, _fn: str) -> None:
            return None

        blocks, _ = await parser.parse_adf(
            {
                "type": "doc",
                "content": [{
                    "type": "mediaSingle",
                    "content": [{
                        "type": "media",
                        "attrs": {"id": "att-1", "alt": "pic"},
                    }],
                }],
            },
            page_id="p1",
            media_fetcher=none_fetcher,
        )
        assert not any(b.type == BlockType.IMAGE for b in blocks)

    async def test_media_success_with_dimensions(self):
        parser = _parser()

        async def ok_fetcher(_mid: str, _fn: str) -> str:
            return "data:image/png;base64,abc"

        blocks, _ = await parser.parse_adf(
            {
                "type": "doc",
                "content": [{
                    "type": "mediaGroup",
                    "content": [{
                        "type": "media",
                        "attrs": {
                            "id": "att-1",
                            "alt": "diagram",
                            "width": 100,
                            "height": 50,
                        },
                    }],
                }],
            },
            page_id="p1",
            media_fetcher=ok_fetcher,
        )
        images = [b for b in blocks if b.type == BlockType.IMAGE]
        assert len(images) == 1
        assert images[0].image_metadata.image_size == {"width": 100, "height": 50}


@pytest.mark.asyncio
class TestExtensionAndPlaceholderParsers:
    async def test_extension_invalid_json_fallback_placeholder(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "extension",
                "attrs": {
                    "extensionType": "com.example",
                    "extensionKey": "my-widget",
                    "parameters": {"adf": "not-valid-json{"},
                },
            }],
        }, page_id="p1")
        assert any("[Extension: my-widget]" in str(b.data) for b in blocks)

    async def test_extension_no_adf_no_content_placeholder(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "extension",
                "attrs": {
                    "extensionType": "com.example",
                    "extensionKey": "empty-ext",
                },
            }],
        }, page_id="p1")
        assert any("empty-ext" in str(b.data) for b in blocks)

    async def test_extension_content_fallback(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "extension",
                "attrs": {"extensionKey": "wrap"},
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "inside"}],
                }],
            }],
        }, page_id="p1")
        assert any(b.data == "inside" for b in blocks)

    async def test_placeholder_with_text(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "placeholder",
                "attrs": {"text": "Type something..."},
            }],
        }, page_id="p1")
        assert blocks[0].data == "Type something..."

    async def test_placeholder_empty_skipped(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{"type": "placeholder", "attrs": {}}],
        }, page_id="p1")
        assert blocks == []

    async def test_inline_card_block(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "inlineCard",
                "attrs": {"url": "https://example.com/page"},
            }],
        }, page_id="p1")
        assert blocks[0].sub_type == BlockSubType.LINK

    async def test_standalone_mention(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "mention",
                "attrs": {"text": "Carol"},
            }],
        }, page_id="p1")
        assert blocks[0].data == "@Carol"


@pytest.mark.asyncio
class TestUnknownNodeParser:
    async def test_unknown_with_content_recurses(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "customUnknown",
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "from unknown"}],
                }],
            }],
        }, page_id="p1")
        assert any(b.data == "from unknown" for b in blocks)

    async def test_unknown_without_content(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{"type": "totallyUnknown"}],
        }, page_id="p1")
        assert blocks == []


class TestCommentParsingHelpers:
    def test_normalize_text_strips_markdown(self):
        text = "**[Link](https://x.com)**"
        normalized = ConfluenceBlockParser._normalize_text_for_comment_match(text)
        assert "link" in normalized
        assert "https" not in normalized

    def test_searchable_text_table_row(self):
        parser = _parser()
        row = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data={
                "row_natural_language_text": "alpha",
                "cell_details": [{"type": "text", "text": "beta"}],
                "cells": ["gamma"],
            },
        )
        text = parser._searchable_text_for_comment_match(row)
        assert "alpha" in text
        assert "beta" in text
        assert "gamma" in text

    def test_searchable_text_string_data_block(self):
        parser = _parser()
        block = Block(
            index=0,
            type=BlockType.TEXT,
            format=DataFormat.TXT,
            data="  plain text  ",
        )
        assert parser._searchable_text_for_comment_match(block) == "  plain text  "

    def test_find_block_empty_quoted_text(self):
        parser = _parser()
        assert parser._find_block_by_text([], "") is None
        assert parser._find_block_by_text([], "   ") is None

    def test_find_block_no_match(self):
        parser = _parser()
        block = Block(index=0, type=BlockType.TEXT, format=DataFormat.MARKDOWN, data="hello")
        assert parser._find_block_by_text([block], "goodbye") is None

    def test_find_block_markdown_link_normalized(self):
        parser = _parser()
        block = Block(
            index=0,
            type=BlockType.TEXT,
            format=DataFormat.MARKDOWN,
            data="See [Example](https://example.com) here",
        )
        assert parser._find_block_by_text([block], "example") == block

    def test_searchable_text_from_string_data_block(self):
        parser = _parser()
        block = Block(
            index=0,
            type=BlockType.IMAGE,
            format=DataFormat.TXT,
            data="  image caption  ",
        )
        assert parser._searchable_text_for_comment_match(block) == "  image caption  "

    def test_find_block_skips_empty_block_text(self):
        parser = _parser()
        empty = Block(index=0, type=BlockType.TEXT, format=DataFormat.MARKDOWN, data="")
        match = Block(index=1, type=BlockType.TEXT, format=DataFormat.MARKDOWN, data="findme")
        assert parser._find_block_by_text([empty, match], "findme") == match


@pytest.mark.asyncio
class TestAttachInlineComments:
    async def test_empty_comments_no_op(self):
        parser = _parser()
        blocks = [Block(index=0, type=BlockType.TEXT, format=DataFormat.MARKDOWN, data="x")]
        await parser.attach_inline_comments_to_blocks(blocks, [])
        assert blocks[0].comments == []

    async def test_attaches_thread_to_matching_block(self):
        parser = _parser()
        blocks = [Block(
            index=0,
            type=BlockType.TEXT,
            format=DataFormat.MARKDOWN,
            data="Target paragraph text",
        )]
        comments = [{
            "id": "c1",
            "createdAt": "2026-01-01T00:00:00.000Z",
            "version": {"authorId": "u1"},
            "resolutionStatus": "open",
            "properties": {"inlineOriginalSelection": "Target paragraph"},
            "body": {
                "atlas_doc_format": {
                    "value": '{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"Nice"}]}]}',
                },
            },
            "_links": {"webui": "/spaces/X/pages/1?focusedCommentId=c1"},
        }]
        await parser.attach_inline_comments_to_blocks(
            blocks,
            comments,
            parent_page_url="https://acme.atlassian.net/wiki/spaces/X/pages/1",
        )
        assert len(blocks[0].comments) == 1
        assert "Nice" in blocks[0].comments[0][0].text

    async def test_thread_with_parent_comment_id(self):
        parser = _parser()
        blocks = [Block(
            index=0,
            type=BlockType.TEXT,
            format=DataFormat.MARKDOWN,
            data="Quoted snippet here",
        )]
        comments = [
            {
                "id": "parent-1",
                "createdAt": "2026-01-01T00:00:00.000Z",
                "version": {"authorId": "u1"},
                "resolutionStatus": "open",
                "properties": {"inlineOriginalSelection": "Quoted snippet"},
                "body": {
                    "atlas_doc_format": {
                        "value": '{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"First"}]}]}',
                    },
                },
            },
            {
                "id": "reply-1",
                "parentCommentId": "parent-1",
                "createdAt": "2026-01-02T00:00:00.000Z",
                "version": {"authorId": "u2"},
                "resolutionStatus": "open",
                "body": {
                    "atlas_doc_format": {
                        "value": '{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"Reply"}]}]}',
                    },
                },
            },
        ]
        await parser.attach_inline_comments_to_blocks(blocks, comments)
        assert len(blocks[0].comments[0]) == 2

    async def test_target_block_not_found_leaves_blocks_unchanged(self):
        parser = _parser()
        blocks = [Block(
            index=0,
            type=BlockType.TEXT,
            format=DataFormat.MARKDOWN,
            data="unrelated content",
        )]
        comments = [{
            "id": "c-miss",
            "createdAt": "2026-01-01T00:00:00.000Z",
            "version": {"authorId": "u1"},
            "properties": {"inlineOriginalSelection": "does not exist"},
            "body": {
                "atlas_doc_format": {
                    "value": '{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"Orphan"}]}]}',
                },
            },
        }]
        await parser.attach_inline_comments_to_blocks(blocks, comments)
        assert blocks[0].comments == []
        parser.logger.debug.assert_called()


@pytest.mark.asyncio
@pytest.mark.asyncio
class TestBlockCommentParsing:
    async def test_missing_id_returns_none(self):
        parser = _parser()
        result = await parser._parse_confluence_comment_to_block_comment({})
        assert result is None

    async def test_missing_body_returns_none(self):
        parser = _parser()
        result = await parser._parse_confluence_comment_to_block_comment({"id": "1"})
        assert result is None

    async def test_adf_as_dict_not_string(self):
        parser = _parser()
        comment = {
            "id": "2",
            "createdAt": "2026-01-01T00:00:00.000Z",
            "version": {"authorId": "u1"},
            "body": {
                "atlas_doc_format": {
                    "value": {
                        "type": "doc",
                        "content": [{
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "Dict body"}],
                        }],
                    },
                },
            },
        }
        result = await parser._parse_confluence_comment_to_block_comment(comment)
        assert result is not None
        assert "Dict body" in result.text

    async def test_user_display_names(self):
        parser = _parser()
        comment = {
            "id": "3",
            "createdAt": "2026-01-01T00:00:00.000Z",
            "version": {"authorId": "u99"},
            "body": {
                "atlas_doc_format": {
                    "value": '{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"Hi"}]}]}',
                },
            },
        }
        result = await parser._parse_confluence_comment_to_block_comment(
            comment,
            user_display_names={"u99": "Jane Doe"},
        )
        assert result.author_name == "Jane Doe"

    async def test_invalid_json_returns_none(self):
        parser = _parser()
        comment = {
            "id": "4",
            "body": {"atlas_doc_format": {"value": "{bad json"}},
        }
        result = await parser._parse_confluence_comment_to_block_comment(comment)
        assert result is None
        parser.logger.warning.assert_called()

    async def test_unknown_author_uses_fallback_name(self):
        parser = _parser()
        comment = {
            "id": "5",
            "createdAt": "2026-01-01T00:00:00.000Z",
            "version": {"authorId": "missing-user"},
            "body": {
                "atlas_doc_format": {
                    "value": '{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"Hi"}]}]}',
                },
            },
        }
        result = await parser._parse_confluence_comment_to_block_comment(
            comment,
            user_display_names={"other": "Someone"},
        )
        assert result.author_name == "Unknown"


class TestAdfToMarkdownSimple:
    def test_none_input(self):
        parser = _parser()
        assert parser._adf_to_markdown_simple(None) == ""

    def test_heading_and_lists_and_codeblock(self):
        parser = _parser()
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 2},
                    "content": [{"type": "text", "text": "Title"}],
                },
                {
                    "type": "bulletList",
                    "content": [{
                        "type": "listItem",
                        "content": [{
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "item"}],
                        }],
                    }],
                },
                {
                    "type": "orderedList",
                    "content": [{
                        "type": "listItem",
                        "content": [{
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "num"}],
                        }],
                    }],
                },
                {
                    "type": "codeBlock",
                    "attrs": {"language": "js"},
                    "content": [{"type": "text", "text": "const x=1"}],
                },
                {"type": "rule"},
            ],
        }
        md = parser._adf_to_markdown_simple(adf)
        assert "## Title" in md
        assert "- item" in md
        assert "1. num" in md
        assert "```js" in md
        assert "---" in md

    def test_non_dict_input(self):
        parser = _parser()
        assert parser._adf_to_markdown_simple("not dict") == ""

    def test_hard_break_inside_paragraph(self):
        parser = _parser()
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "line1"},
                    {"type": "hardBreak"},
                    {"type": "text", "text": "line2"},
                ],
            }],
        }
        md = parser._adf_to_markdown_simple(adf)
        assert "line1" in md
        assert "line2" in md


class TestParseConfluenceTimestamp:
    def test_valid_iso(self):
        parser = _parser()
        dt = parser._parse_confluence_timestamp("2026-01-15T10:00:00.000Z")
        assert dt is not None
        assert dt.year == 2026

    def test_none(self):
        parser = _parser()
        assert parser._parse_confluence_timestamp(None) is None

    def test_invalid(self):
        parser = _parser()
        assert parser._parse_confluence_timestamp("not-a-date") is None


class TestIndexShiftingHelpers:
    def test_shift_group_index_refs_below_unchanged(self):
        assert ConfluenceBlockParser._shift_group_index_refs(0, insert_at=5) == 0

    def test_shift_group_index_refs_at_insert_bumped(self):
        assert ConfluenceBlockParser._shift_group_index_refs(5, insert_at=5) == 6

    def test_shift_block_group_ranges(self):
        from app.models.blocks import IndexRange

        ranges = [IndexRange(start=3, end=4), IndexRange(start=1, end=1)]
        ConfluenceBlockParser._shift_block_group_ranges(ranges, insert_at=2)
        assert ranges[0].start == 4
        assert ranges[0].end == 5
        assert ranges[1].start == 1

    def test_shift_parent_indices_cell_details_block_group_indices(self):
        row = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            parent_index=1,
            data={
                "cell_details": [{
                    "type": "nested",
                    "block_group_indices": [1, 2],
                }],
            },
        )
        table_group = BlockGroup(index=1, type=GroupType.TABLE)
        content_group = BlockGroup(
            index=0,
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
        )
        block_groups = [content_group, table_group]

        ConfluenceBlockParser.shift_parent_indices_after_group_insert(
            [row], block_groups, insert_at=0
        )
        assert row.data["cell_details"][0]["block_group_indices"] == [2, 3]


class TestSyncTableRowLinksEdgeCases:
    def test_skips_non_table_groups(self):
        non_table = BlockGroup(index=0, type=GroupType.LIST)
        ConfluenceBlockParser.sync_table_row_links([], [non_table])

    def test_out_of_range_block_index(self):
        from app.models.blocks import IndexRange

        group = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            children=BlockGroupChildren.from_indices(block_indices=[99]),
        )
        ConfluenceBlockParser.sync_table_row_links([], [group])

    def test_non_table_row_block_in_range(self):
        from app.models.blocks import IndexRange

        text_block = Block(index=0, type=BlockType.TEXT, format=DataFormat.MARKDOWN, data="x")
        group = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            children=BlockGroupChildren(block_ranges=[IndexRange(start=0, end=0)]),
        )
        ConfluenceBlockParser.sync_table_row_links([text_block], [group])
        assert text_block.parent_index is None

    def test_updates_parent_index_and_row_numbers(self):
        row = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            parent_index=99,
            data={"cells": ["a"], "row_number": 0},
            table_row_metadata=TableRowMetadata(row_number=0, is_header=True),
        )
        group = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            children=BlockGroupChildren.from_indices(block_indices=[0]),
        )
        ConfluenceBlockParser.sync_table_row_links([row], [group])
        assert row.parent_index == 0
        assert row.data["row_number"] == 1
        assert row.table_row_metadata.row_number == 1


class TestSyncNestedTableGroupLinks:
    def test_sets_nested_parent_index(self):
        from app.models.blocks import IndexRange

        outer = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            children=BlockGroupChildren(block_group_ranges=[IndexRange(start=1, end=1)]),
        )
        nested = BlockGroup(index=1, type=GroupType.TABLE, parent_index=None)
        ConfluenceBlockParser.sync_nested_table_group_links([outer, nested])
        assert nested.parent_index == 0

    def test_out_of_range_group_index(self):
        from app.models.blocks import IndexRange

        outer = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            children=BlockGroupChildren(block_group_ranges=[IndexRange(start=99, end=99)]),
        )
        ConfluenceBlockParser.sync_nested_table_group_links([outer])


class TestPostProcessingHelpers:
    def test_list_item_lives_in_table_true(self):
        parser = _parser()
        table_group = BlockGroup(index=0, type=GroupType.TABLE)
        block = Block(
            index=0,
            type=BlockType.TEXT,
            format=DataFormat.MARKDOWN,
            data="- item",
            parent_index=0,
            list_metadata=ListMetadata(list_style="bullet", indent_level=1),
        )
        assert parser._list_item_lives_in_table(block, [table_group]) is True

    def test_list_item_lives_in_table_false(self):
        parser = _parser()
        list_group = BlockGroup(index=0, type=GroupType.LIST)
        block = Block(
            index=0,
            type=BlockType.TEXT,
            format=DataFormat.MARKDOWN,
            data="- item",
            parent_index=0,
            list_metadata=ListMetadata(list_style="bullet", indent_level=0),
        )
        assert parser._list_item_lives_in_table(block, [list_group]) is False

    def test_group_list_items_empty_blocks(self):
        parser = _parser()
        parser._group_list_items([], [])

    def test_create_list_group_empty_indices(self):
        parser = _parser()
        parser._create_list_group([], [], [], "bullet")

    def test_create_comment_group_with_block_indices(self):
        from app.models.blocks import BlockComment

        parser = _parser()
        comment = BlockComment(text="Hi", format=DataFormat.MARKDOWN, author_id="u1")
        group = parser.create_comment_group(
            comment,
            group_index=1,
            parent_group_index=0,
            source_id="c1",
            block_indices=(2, 3),
        )
        assert group.children is not None
        assert group.children.block_ranges[0].start == 2

    def test_create_comment_thread_group(self):
        parser = _parser()
        thread = parser.create_comment_thread_group(
            thread_id="t1",
            group_index=2,
            comment_type="footer",
            page_title="My Page",
            weburl="https://acme.atlassian.net/wiki/spaces/X/pages/1",
        )
        assert thread.sub_type == GroupSubType.COMMENT_THREAD
        assert "Footer" in thread.name

    def test_create_comment_group_with_children_records(self):
        from app.models.blocks import BlockComment, ChildRecord, ChildType

        parser = _parser()
        comment = BlockComment(text="Note", format=DataFormat.MARKDOWN, author_id="u1")
        records = [ChildRecord(
            child_type=ChildType.RECORD,
            child_id="att-1",
            child_name="file.pdf",
        )]
        group = parser.create_comment_group(
            comment,
            group_index=1,
            parent_group_index=0,
            source_id="c1",
            children_records=records,
        )
        assert group.children_records == records

    def test_finalize_marks_first_table_row_as_header(self):
        parser = _parser()
        row = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            parent_index=0,
            data={"cells": ["H"], "row_number": 0},
            table_row_metadata=TableRowMetadata(row_number=0, is_header=False),
        )
        table_group = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            table_metadata=TableMetadata(num_of_rows=1, num_of_cols=1, has_header=True),
            children=BlockGroupChildren.from_indices(block_indices=[0]),
        )
        parser._finalize_indices_and_metadata([row], [table_group])
        assert row.table_row_metadata.is_header is True


@pytest.mark.asyncio
class TestTableEdgeCases:
    async def test_table_escapes_pipe_in_cells(self):
        parser = _parser()
        _, groups = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "table",
                "content": [
                    {
                        "type": "tableRow",
                        "content": [{
                            "type": "tableHeader",
                            "content": [{
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "A|B"}],
                            }],
                        }],
                    },
                    {
                        "type": "tableRow",
                        "content": [{
                            "type": "tableCell",
                            "content": [{
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "val"}],
                            }],
                        }],
                    },
                ],
            }],
        }, page_id="p1")
        table = next(g for g in groups if g.type == GroupType.TABLE)
        assert "\\|" in table.data["table_markdown"]

    async def test_panel_empty_content(self):
        parser = _parser()
        _, groups = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "panel",
                "attrs": {"panelType": "warning"},
                "content": [],
            }],
        }, page_id="p1")
        panels = [g for g in groups if g.sub_type == GroupSubType.CALLOUT]
        assert len(panels) == 1
        assert panels[0].name == "WARNING"

    async def test_nested_expand_delegates(self):
        parser = _parser()
        blocks, groups = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "nestedExpand",
                "attrs": {"title": "Nested"},
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "inside"}],
                }],
            }],
        }, page_id="p1")
        toggles = [g for g in groups if g.sub_type == GroupSubType.TOGGLE]
        assert len(toggles) == 1
        assert any(b.data == "inside" for b in blocks)


@pytest.mark.asyncio
class TestNestedListTaskAndExtensionParsing:
    async def test_list_item_with_media_only(self):
        parser = _parser()

        async def media_fetcher(_mid: str, _fn: str) -> str:
            return "data:image/png;base64,abc"

        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "bulletList",
                "content": [{
                    "type": "listItem",
                    "content": [{
                        "type": "mediaSingle",
                        "content": [{
                            "type": "media",
                            "attrs": {"id": "m1", "alt": "img"},
                        }],
                    }],
                }],
            }],
        }, page_id="p1", media_fetcher=media_fetcher)
        assert any(b.type == BlockType.IMAGE for b in blocks)

    async def test_task_item_nested_list_no_text(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "taskList",
                "content": [{
                    "type": "taskItem",
                    "attrs": {"state": "TODO"},
                    "content": [{
                        "type": "bulletList",
                        "content": [{
                            "type": "listItem",
                            "content": [{
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "nested only"}],
                            }],
                        }],
                    }],
                }],
            }],
        }, page_id="p1")
        assert any("nested only" in str(b.data) for b in blocks)

    async def test_task_item_direct_inline_node(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "taskList",
                "content": [{
                    "type": "taskItem",
                    "attrs": {"state": "TODO"},
                    "content": [{"type": "text", "text": "inline task"}],
                }],
            }],
        }, page_id="p1")
        assert any("inline task" in str(b.data) for b in blocks)

    async def test_decision_item_with_nested_list(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "decisionList",
                "content": [{
                    "type": "decisionItem",
                    "attrs": {"state": "DECIDED"},
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "Parent"}],
                        },
                        {
                            "type": "decisionList",
                            "content": [{
                                "type": "decisionItem",
                                "attrs": {"state": "UNDECIDED"},
                                "content": [{
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Child"}],
                                }],
                            }],
                        },
                    ],
                }],
            }],
        }, page_id="p1")
        assert len([b for b in blocks if b.list_metadata]) >= 2

    async def test_extension_adf_doc_root(self):
        parser = _parser()
        inner = json.dumps({
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "text", "text": "from doc"}],
            }],
        })
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "extension",
                "attrs": {
                    "extensionKey": "embed",
                    "parameters": {"adf": inner},
                },
            }],
        }, page_id="p1")
        assert any(b.data == "from doc" for b in blocks)

    async def test_extension_adf_non_doc_root(self):
        parser = _parser()
        inner = json.dumps({
            "type": "paragraph",
            "content": [{"type": "text", "text": "root para"}],
        })
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "extension",
                "attrs": {
                    "extensionKey": "embed",
                    "parameters": {"adf": inner},
                },
            }],
        }, page_id="p1")
        assert any(b.data == "root para" for b in blocks)

    async def test_extension_adf_not_dict_raises_and_fallback(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "extension",
                "attrs": {
                    "extensionKey": "bad-adf",
                    "parameters": {"adf": json.dumps(["not", "a", "dict"])},
                },
            }],
        }, page_id="p1")
        assert any("bad-adf" in str(b.data) for b in blocks)

    async def test_table_row_handlers_return_empty(self):
        parser = _parser()
        blocks: list[Block] = []
        groups: list[BlockGroup] = []
        for method_name in ("_parse_tableRow", "_parse_tableCell", "_parse_tableHeader"):
            method = getattr(parser, method_name)
            result = await method(
                node={"type": "x"},
                blocks=blocks,
                block_groups=groups,
                parent_group_index=None,
                media_fetcher=None,
                parent_page_url=None,
                page_id=None,
            )
            assert result == []

    async def test_standalone_inline_parsers_return_empty(self):
        parser = _parser()
        blocks: list[Block] = []
        groups: list[BlockGroup] = []
        for method_name in ("_parse_emoji", "_parse_status", "_parse_date", "_parse_hardBreak"):
            method = getattr(parser, method_name)
            result = await method(
                node={"type": "x"},
                blocks=blocks,
                block_groups=groups,
                parent_group_index=None,
                media_fetcher=None,
                parent_page_url=None,
                page_id=None,
            )
            assert result == []

    async def test_empty_table_row_returns_none(self):
        parser = _parser()
        blocks: list[Block] = []
        groups: list[BlockGroup] = []
        row_index, nested = await parser._parse_table_row_node(
            node={"type": "tableRow", "content": []},
            blocks=blocks,
            block_groups=groups,
            parent_group_index=0,
            media_fetcher=None,
            parent_page_url=None,
            page_id=None,
        )
        assert row_index is None
        assert nested == []

    async def test_layout_section_no_columns(self):
        parser = _parser()
        _, groups = await parser.parse_adf({
            "type": "doc",
            "content": [{"type": "layoutSection", "content": []}],
        }, page_id="p1")
        column_lists = [g for g in groups if g.type == GroupType.COLUMN_LIST]
        assert len(column_lists) == 1
        assert column_lists[0].children is None

    async def test_expand_default_title(self):
        parser = _parser()
        _, groups = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "expand",
                "attrs": {},
                "content": [],
            }],
        }, page_id="p1")
        toggles = [g for g in groups if g.sub_type == GroupSubType.TOGGLE]
        assert toggles[0].name == "Details"


@pytest.mark.asyncio
class TestInlineCodeTableAndLayoutParsing:
    async def test_inline_code_standalone(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{"type": "inlineCode", "text": "var x"}],
        }, page_id="p1")
        assert blocks[0].sub_type == BlockSubType.CODE
        assert blocks[0].data == "var x"

    async def test_inline_code_empty_skipped(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{"type": "inlineCode", "text": ""}],
        }, page_id="p1")
        assert blocks == []

    async def test_task_item_with_media(self):
        parser = _parser()

        async def media_fetcher(_mid: str, _fn: str) -> str:
            return "data:image/png;base64,abc"

        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "taskList",
                "content": [{
                    "type": "taskItem",
                    "attrs": {"state": "TODO"},
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "see image"}],
                        },
                        {
                            "type": "mediaSingle",
                            "content": [{
                                "type": "media",
                                "attrs": {"id": "m1", "alt": "pic"},
                            }],
                        },
                    ],
                }],
            }],
        }, page_id="p1", media_fetcher=media_fetcher)
        assert any(b.type == BlockType.IMAGE for b in blocks)
        assert any("see image" in str(b.data) for b in blocks)

    async def test_decision_item_with_media(self):
        parser = _parser()

        async def media_fetcher(_mid: str, _fn: str) -> str:
            return "data:image/png;base64,abc"

        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "decisionList",
                "content": [{
                    "type": "decisionItem",
                    "attrs": {"state": "DECIDED"},
                    "content": [{
                        "type": "mediaSingle",
                        "content": [{
                            "type": "media",
                            "attrs": {"id": "m1", "alt": "pic"},
                        }],
                    }],
                }],
            }],
        }, page_id="p1", media_fetcher=media_fetcher)
        assert any(b.type == BlockType.IMAGE for b in blocks)

    async def test_decision_item_direct_inline_node(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "decisionList",
                "content": [{
                    "type": "decisionItem",
                    "attrs": {"state": "DECIDED"},
                    "content": [{"type": "text", "text": "inline decision"}],
                }],
            }],
        }, page_id="p1")
        assert any("inline decision" in str(b.data) for b in blocks)

    async def test_inline_card_empty_url_skipped(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{"type": "inlineCard", "attrs": {}}],
        }, page_id="p1")
        assert blocks == []

    async def test_empty_standalone_text_skipped(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{"type": "text", "text": "   "}],
        }, page_id="p1")
        assert blocks == []

    async def test_table_markdown_includes_legacy_dict_cell_text(self):
        """Legacy table rows may store cell values as dicts with a text field."""
        parser = _parser()
        blocks: list[Block] = []
        groups: list[BlockGroup] = []

        row_block = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data={
                "cells": [{"text": "legacy cell"}],
                "cell_details": [],
            },
        )
        blocks.append(row_block)

        original_parse_row = parser._parse_table_row_node

        async def mock_row(*_args, **_kwargs):
            return 0, []

        parser._parse_table_row_node = mock_row  # type: ignore[method-assign]
        try:
            await parser._parse_table(
                node={
                    "type": "table",
                    "content": [{"type": "tableRow", "content": []}],
                },
                blocks=blocks,
                block_groups=groups,
                parent_group_index=None,
                media_fetcher=None,
                parent_page_url=None,
                page_id=None,
            )
        finally:
            parser._parse_table_row_node = original_parse_row  # type: ignore[method-assign]

        table_group = next(g for g in groups if g.type == GroupType.TABLE)
        assert "legacy cell" in table_group.data["table_markdown"]

    async def test_panel_child_block_parent_index_updated(self):
        parser = _parser()
        blocks, groups = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "panel",
                "attrs": {"panelType": "info"},
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "In panel"}],
                }],
            }],
        }, page_id="p1")
        panel = next(g for g in groups if g.sub_type == GroupSubType.CALLOUT)
        assert all(b.parent_index == panel.index for b in blocks if b.data == "In panel")

    async def test_layout_column_with_child_blocks(self):
        parser = _parser()
        blocks, groups = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "layoutSection",
                "content": [{
                    "type": "layoutColumn",
                    "content": [{
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "Col A"}],
                    }],
                }],
            }],
        }, page_id="p1")
        columns = [g for g in groups if g.type == GroupType.COLUMN]
        assert len(columns) == 1
        assert columns[0].children is not None

    async def test_group_list_items_flushes_before_table_cell_list(self):
        """Page-level list group closes before table-cell list items."""
        parser = _parser()
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "bulletList",
                    "content": [{
                        "type": "listItem",
                        "content": [{
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "page item"}],
                        }],
                    }],
                },
                {
                    "type": "table",
                    "content": [{
                        "type": "tableRow",
                        "content": [{
                            "type": "tableCell",
                            "content": [{
                                "type": "bulletList",
                                "content": [{
                                    "type": "listItem",
                                    "content": [{
                                        "type": "paragraph",
                                        "content": [{"type": "text", "text": "cell item"}],
                                    }],
                                }],
                            }],
                        }],
                    }],
                },
            ],
        }
        blocks, groups = await parser.parse_adf(adf, page_id="p1")
        parser.post_process_blocks(blocks, groups)
        page_list_groups = [
            g for g in groups
            if g.type == GroupType.LIST and g.sub_type != GroupSubType.COMMENT
        ]
        assert len(page_list_groups) == 1

    async def test_extension_empty_adf_type_falls_through(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "extension",
                "attrs": {
                    "extensionKey": "empty-root",
                    "parameters": {"adf": json.dumps({})},
                },
            }],
        }, page_id="p1")
        assert any("empty-root" in str(b.data) for b in blocks)

    async def test_list_item_text_from_nested_content_branch(self):
        parser = _parser()
        blocks, _ = await parser.parse_adf({
            "type": "doc",
            "content": [{
                "type": "bulletList",
                "content": [{
                    "type": "listItem",
                    "content": [{
                        "type": "heading",
                        "attrs": {"level": 4},
                        "content": [{"type": "text", "text": "heading in item"}],
                    }],
                }],
            }],
        }, page_id="p1")
        assert any("heading in item" in str(b.data) for b in blocks)
