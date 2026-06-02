"""Unit tests for MarkdownToBlocksConverter."""

from unittest.mock import MagicMock, patch

import pytest

from app.models.blocks import (
    Block,
    BlockGroup,
    BlocksContainer,
    BlockSubType,
    BlockType,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.modules.parsers.markdown.markdown_to_blocks import MarkdownToBlocksConverter


@pytest.fixture
def converter() -> MarkdownToBlocksConverter:
    return MarkdownToBlocksConverter()


def _groups_by_type(container: BlocksContainer, group_type: GroupType) -> list[BlockGroup]:
    return [group for group in container.block_groups if group.type == group_type]


def _blocks_by_type(container: BlocksContainer, block_type: BlockType) -> list[Block]:
    return [block for block in container.blocks if block.type == block_type]


def _child_block_indices(group: BlockGroup) -> list[int]:
    assert group.children is not None
    indices: list[int] = []
    for block_range in group.children.block_ranges:
        indices.extend(range(block_range.start, block_range.end + 1))
    return indices


def _child_group_indices(group: BlockGroup) -> list[int]:
    assert group.children is not None
    indices: list[int] = []
    for group_range in group.children.block_group_ranges:
        indices.extend(range(group_range.start, group_range.end + 1))
    return indices


class TestHeadings:
    def test_heading_produces_text_block_with_heading_subtype(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("# Title\n\n## Subtitle\n")
        heading_blocks = [
            block for block in container.blocks
            if block.sub_type == BlockSubType.HEADING
        ]
        assert len(heading_blocks) == 2
        assert heading_blocks[0].type == BlockType.TEXT
        assert heading_blocks[0].data == "Title"
        assert heading_blocks[1].data == "Subtitle"
        assert all(block.parent_index is None for block in heading_blocks)

    @pytest.mark.parametrize(
        ("markdown", "expected"),
        [
            ("# h1\n", "h1"),
            ("## h2\n", "h2"),
            ("### h3\n", "h3"),
            ("#### h4\n", "h4"),
            ("##### h5\n", "h5"),
            ("###### h6\n", "h6"),
        ],
    )
    def test_all_heading_levels(
        self,
        converter: MarkdownToBlocksConverter,
        markdown: str,
        expected: str,
    ):
        container = converter.convert(markdown)
        assert len(container.blocks) == 1
        assert container.blocks[0].sub_type == BlockSubType.HEADING
        assert container.blocks[0].data == expected


class TestParagraphs:
    def test_paragraph_produces_text_block(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("Hello **bold** world\n")
        assert len(container.blocks) == 1
        block = container.blocks[0]
        assert block.type == BlockType.TEXT
        assert block.sub_type == BlockSubType.PARAGRAPH
        assert block.data == "Hello bold world"
        assert block.format == DataFormat.TXT

    def test_multiple_paragraphs_are_separate_blocks(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("First paragraph.\n\nSecond paragraph.\n")
        paragraphs = [
            block for block in container.blocks
            if block.sub_type == BlockSubType.PARAGRAPH
        ]
        assert len(paragraphs) == 2
        assert paragraphs[0].data == "First paragraph."
        assert paragraphs[1].data == "Second paragraph."


class TestCodeBlocks:
    def test_fence_produces_code_block_with_language(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("```python\nprint('hi')\n```\n")
        assert len(container.blocks) == 1
        block = container.blocks[0]
        assert block.sub_type == BlockSubType.CODE
        assert block.data == "print('hi')"
        assert block.format == DataFormat.CODE
        assert block.code_metadata is not None
        assert block.code_metadata.language == "python"

    def test_indented_code_block_has_no_language(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("    indented code\n")
        block = container.blocks[0]
        assert block.sub_type == BlockSubType.CODE
        assert block.data == "indented code"
        assert block.code_metadata is not None
        assert block.code_metadata.language is None

    def test_empty_fence_is_skipped(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("```\n```\n")
        assert container.blocks == []
        assert container.block_groups == []


class TestTables:
    def test_table_produces_table_group_and_rows(self, converter: MarkdownToBlocksConverter):
        markdown = "| Name | Age |\n| --- | --- |\n| Alice | 30 |\n| Bob | 25 |\n"
        container = converter.convert(markdown)

        assert len(container.block_groups) == 1
        table_group = container.block_groups[0]
        assert table_group.type == GroupType.TABLE
        assert table_group.data["column_headers"] == ["Name", "Age"]
        assert table_group.data["table_summary"] == ""
        assert table_group.format == DataFormat.JSON

        row_blocks = _blocks_by_type(container, BlockType.TABLE_ROW)
        assert len(row_blocks) == 2
        assert row_blocks[0].parent_index == table_group.index
        assert "Name: Alice" in row_blocks[0].data["row_natural_language_text"]
        assert row_blocks[0].data["cells"] == ["Alice", "30"]
        assert row_blocks[1].data["row_number"] == 2

    def test_table_group_children_link_row_blocks(self, converter: MarkdownToBlocksConverter):
        markdown = "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n"
        container = converter.convert(markdown)
        table_group = container.block_groups[0]

        child_indices = _child_block_indices(table_group)
        row_blocks = _blocks_by_type(container, BlockType.TABLE_ROW)
        assert child_indices == [block.index for block in row_blocks]
        assert table_group.table_metadata is not None
        assert table_group.table_metadata.num_of_rows == 2
        assert table_group.table_metadata.num_of_cols == 2
        assert table_group.table_metadata.num_of_cells == 4
        assert table_group.table_metadata.has_header is True
        assert table_group.table_metadata.column_names == ["A", "B"]

    def test_multiple_tables_create_separate_groups(self, converter: MarkdownToBlocksConverter):
        markdown = (
            "| A |\n| --- |\n| 1 |\n\n"
            "| B |\n| --- |\n| 2 |\n"
        )
        container = converter.convert(markdown)
        table_groups = _groups_by_type(container, GroupType.TABLE)
        assert len(table_groups) == 2
        assert table_groups[0].data["column_headers"] == ["A"]
        assert table_groups[1].data["column_headers"] == ["B"]
        assert container.blocks[0].parent_index == table_groups[0].index
        assert container.blocks[1].parent_index == table_groups[1].index


class TestLists:
    def test_bullet_list_produces_list_group(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("- first\n- second\n")
        assert len(container.block_groups) == 1
        list_group = container.block_groups[0]
        assert list_group.type == GroupType.LIST
        assert list_group.parent_index is None

        list_items = [
            block for block in container.blocks
            if block.sub_type == BlockSubType.LIST_ITEM
        ]
        assert len(list_items) == 2
        assert all(item.parent_index == list_group.index for item in list_items)
        assert _child_block_indices(list_group) == [0, 1]

    def test_ordered_list_produces_ordered_list_group(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("1. alpha\n2. beta\n")
        list_group = container.block_groups[0]
        assert list_group.type == GroupType.ORDERED_LIST
        assert _child_block_indices(list_group) == [0, 1]

    def test_nested_list_creates_nested_block_groups(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("- outer\n  - inner\n")
        assert len(container.block_groups) == 2

        outer_group = container.block_groups[0]
        inner_group = container.block_groups[1]
        assert outer_group.type == GroupType.LIST
        assert inner_group.type == GroupType.LIST
        assert inner_group.parent_index == outer_group.index
        assert _child_group_indices(outer_group) == [inner_group.index]

        outer_item = container.blocks[0]
        inner_item = container.blocks[1]
        assert outer_item.data == "outer"
        assert outer_item.parent_index == outer_group.index
        assert inner_item.data == "inner"
        assert inner_item.parent_index == inner_group.index


class TestImages:
    def test_image_produces_image_block(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("![Image_1](https://example.com/a.png)\n")
        image_blocks = _blocks_by_type(container, BlockType.IMAGE)
        assert len(image_blocks) == 1
        assert image_blocks[0].image_metadata is not None
        assert image_blocks[0].image_metadata.captions == ["Image_1"]

    def test_image_uses_caption_map(self, converter: MarkdownToBlocksConverter):
        container = converter.convert(
            "![Image_1](https://example.com/a.png)\n",
            caption_map={"Image_1": "data:image/png;base64,abc"},
        )
        image_block = _blocks_by_type(container, BlockType.IMAGE)[0]
        assert image_block.data == {"uri": "data:image/png;base64,abc"}
        assert image_block.format == DataFormat.BASE64

    def test_image_without_caption_map_uses_url(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("![alt](https://example.com/a.png)\n")
        image_block = _blocks_by_type(container, BlockType.IMAGE)[0]
        assert image_block.data == {"url": "https://example.com/a.png"}
        assert image_block.format == DataFormat.TXT

    def test_paragraph_with_text_and_image_splits_blocks(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("See ![img](https://example.com/a.png) here\n")
        assert len(container.blocks) == 2
        assert container.blocks[0].sub_type == BlockSubType.PARAGRAPH
        assert container.blocks[0].data == "See  here"
        assert container.blocks[1].type == BlockType.IMAGE


class TestBlockquotes:
    def test_blockquote_produces_text_section_group(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("> quoted text\n")
        assert len(container.block_groups) == 1
        quote_group = container.block_groups[0]
        assert quote_group.type == GroupType.TEXT_SECTION
        assert quote_group.sub_type == GroupSubType.QUOTE
        assert container.blocks[0].data == "quoted text"
        assert _child_block_indices(quote_group) == [0]

    def test_multiline_blockquote_is_single_block(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("> line one\n> line two\n")
        assert len(container.blocks) == 1
        assert container.blocks[0].data == "line one\nline two"
        assert container.blocks[0].parent_index == container.block_groups[0].index

    def test_nested_blockquote_creates_nested_groups(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("> outer\n> > inner\n")
        assert len(container.block_groups) == 2

        outer_group = container.block_groups[0]
        inner_group = container.block_groups[1]
        assert outer_group.sub_type == GroupSubType.QUOTE
        assert inner_group.sub_type == GroupSubType.QUOTE
        assert inner_group.parent_index == outer_group.index
        assert _child_group_indices(outer_group) == [inner_group.index]
        assert container.blocks[0].parent_index == outer_group.index
        assert container.blocks[1].parent_index == inner_group.index


class TestHtmlBlocks:
    def test_html_block_uses_html_format(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("<div>hello</div>\n")
        assert len(container.blocks) == 1
        block = container.blocks[0]
        assert block.format == DataFormat.HTML
        assert block.data == "<div>hello</div>"
        assert block.sub_type == BlockSubType.PARAGRAPH


class TestDividers:
    def test_hr_produces_divider_block(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("---\n")
        assert len(container.blocks) == 1
        block = container.blocks[0]
        assert block.sub_type == BlockSubType.DIVIDER
        assert block.data == "---"
        assert block.parent_index is None


class TestEmptyContent:
    def test_empty_string_returns_empty_container(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("")
        assert container.blocks == []
        assert container.block_groups == []

    def test_whitespace_only_returns_empty_container(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("   \n\n  ")
        assert container.blocks == []
        assert container.block_groups == []


class TestBlockAndGroupIndices:
    def test_block_and_group_indices_are_sequential(self, converter: MarkdownToBlocksConverter):
        markdown = (
            "# Title\n\n"
            "| Col |\n| --- |\n| val |\n\n"
            "- one\n"
        )
        container = converter.convert(markdown)

        assert [block.index for block in container.blocks] == list(range(len(container.blocks)))
        assert [group.index for group in container.block_groups] == list(
            range(len(container.block_groups))
        )


class TestMixedContentOrder:
    def test_preserves_reading_order(self, converter: MarkdownToBlocksConverter):
        markdown = "# Title\n\nParagraph one.\n\n---\n\n- item\n"
        container = converter.convert(markdown)
        block_types = [
            (block.sub_type, block.type)
            for block in container.blocks
        ]
        assert block_types[0] == (BlockSubType.HEADING, BlockType.TEXT)
        assert block_types[1] == (BlockSubType.PARAGRAPH, BlockType.TEXT)
        assert block_types[2] == (BlockSubType.DIVIDER, BlockType.TEXT)
        assert block_types[3] == (BlockSubType.LIST_ITEM, BlockType.TEXT)
        assert container.blocks[0].index == 0
        assert container.blocks[-1].index == len(container.blocks) - 1

    def test_complex_document_block_group_structure(self, converter: MarkdownToBlocksConverter):
        markdown = (
            "# Report\n\n"
            "> Summary line\n\n"
            "| Metric | Value |\n| --- | --- |\n| Users | 10 |\n\n"
            "- action item\n"
        )
        container = converter.convert(markdown)

        assert len(_groups_by_type(container, GroupType.TEXT_SECTION)) == 1
        assert len(_groups_by_type(container, GroupType.TABLE)) == 1
        assert len(_groups_by_type(container, GroupType.LIST)) == 1

        quote_group = _groups_by_type(container, GroupType.TEXT_SECTION)[0]
        table_group = _groups_by_type(container, GroupType.TABLE)[0]
        list_group = _groups_by_type(container, GroupType.LIST)[0]

        assert quote_group.children is not None
        assert table_group.children is not None
        assert list_group.children is not None
        assert len(_child_block_indices(table_group)) == 1
        assert len(_child_block_indices(list_group)) == 1


class TestMarkdownItParserIntegration:
    def test_parse_to_blocks_delegates_to_converter(self):
        from app.modules.parsers.markdown.markdown_it_parser import MarkdownItParser

        parser = MarkdownItParser()
        expected = MagicMock()
        parser._converter.convert = MagicMock(return_value=expected)

        result = parser.parse_to_blocks("# Hello", caption_map={"Image_1": "uri"})

        assert result is expected
        parser._converter.convert.assert_called_once_with(
            "# Hello",
            caption_map={"Image_1": "uri"},
        )
