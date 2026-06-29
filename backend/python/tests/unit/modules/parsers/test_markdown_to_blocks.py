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
from markdown_it.token import Token

from app.modules.parsers.markdown.markdown_to_blocks import (
    MarkdownToBlocksConverter,
    _TableCell,
    _TableState,
    _TokenWalker,
    _split_raw_markdown_into_segments,
)


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


def _is_empty_split_container(block: Block) -> bool:
    if block.parent_block_index is not None:
        return False
    data = block.data
    if isinstance(data, str) and not data.strip():
        return True
    if isinstance(data, dict):
        if not data:
            return True
        if block.type == BlockType.TABLE_ROW:
            return not data.get("row_natural_language_text") and not data.get("cells")
    return False


def _assert_all_blocks_have_data(container: BlocksContainer) -> None:
    for block in container.blocks:
        assert block.data is not None, (
            f"block index={block.index} type={block.type} "
            f"sub_type={block.sub_type} has data=None"
        )


def _assert_empty_text_split_container(block: Block) -> None:
    assert block.data == ""
    assert _is_empty_split_container(block)


def _fragment_blocks(container: BlocksContainer, container_index: int) -> list[Block]:
    return [
        block
        for block in container.blocks
        if block.parent_block_index == container_index
    ]


def _content_text_blocks(container: BlocksContainer) -> list[Block]:
    return [
        block
        for block in container.blocks
        if block.type == BlockType.TEXT and not _is_empty_split_container(block)
    ]


def _split_container_blocks(
    container: BlocksContainer,
    sub_type: BlockSubType,
) -> list[Block]:
    return [
        block
        for block in container.blocks
        if block.sub_type == sub_type and _is_empty_split_container(block)
    ]


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


class TestHeadingMergesIntoParagraph:
    """Heading followed by a paragraph merges into one PARAGRAPH block."""

    def test_heading_merges_with_following_paragraph(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("## Section\n\nSome text.\n")
        assert len(container.blocks) == 1
        block = container.blocks[0]
        assert block.sub_type == BlockSubType.PARAGRAPH
        assert block.data == "## Section\nSome text."
        assert block.format == DataFormat.MARKDOWN

    def test_all_heading_levels_merge(self, converter: MarkdownToBlocksConverter):
        for level in range(1, 7):
            prefix = "#" * level
            md = f"{prefix} Heading\n\nBody.\n"
            container = converter.convert(md)
            assert len(container.blocks) == 1
            assert container.blocks[0].data == f"{prefix} Heading\nBody."
            assert container.blocks[0].format == DataFormat.MARKDOWN

    def test_heading_before_list_stays_standalone(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("### Items\n\n- one\n- two\n")
        heading_blocks = [b for b in container.blocks if b.sub_type == BlockSubType.HEADING]
        list_blocks = [b for b in container.blocks if b.sub_type == BlockSubType.LIST_ITEM]
        assert len(heading_blocks) == 1
        assert heading_blocks[0].data == "Items"
        assert len(list_blocks) == 2

    def test_heading_before_table_stays_standalone(self, converter: MarkdownToBlocksConverter):
        md = "## Data\n\n| A |\n| --- |\n| 1 |\n"
        container = converter.convert(md)
        heading_blocks = [b for b in container.blocks if b.sub_type == BlockSubType.HEADING]
        assert len(heading_blocks) == 1
        assert heading_blocks[0].data == "Data"

    def test_heading_before_blockquote_stays_standalone(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("# Title\n\n> quoted\n")
        heading_blocks = [b for b in container.blocks if b.sub_type == BlockSubType.HEADING]
        assert len(heading_blocks) == 1
        assert heading_blocks[0].data == "Title"

    def test_heading_before_code_stays_standalone(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("## Example\n\n```python\nx = 1\n```\n")
        heading_blocks = [b for b in container.blocks if b.sub_type == BlockSubType.HEADING]
        assert len(heading_blocks) == 1
        assert heading_blocks[0].data == "Example"

    def test_heading_before_hr_then_paragraph_stays_standalone(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("## Section\n\n---\n\nParagraph.\n")
        heading_blocks = [b for b in container.blocks if b.sub_type == BlockSubType.HEADING]
        para_blocks = [b for b in container.blocks if b.sub_type == BlockSubType.PARAGRAPH]
        assert len(heading_blocks) == 1
        assert heading_blocks[0].data == "Section"
        assert len(para_blocks) == 1
        assert para_blocks[0].data == "Paragraph."

    def test_heading_before_another_heading_stays_standalone(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("# H1\n\n## H2\n")
        heading_blocks = [b for b in container.blocks if b.sub_type == BlockSubType.HEADING]
        assert len(heading_blocks) == 2
        assert heading_blocks[0].data == "H1"
        assert heading_blocks[1].data == "H2"

    def test_heading_at_end_of_document_stays_standalone(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("# Trailing\n")
        assert len(container.blocks) == 1
        assert container.blocks[0].sub_type == BlockSubType.HEADING
        assert container.blocks[0].data == "Trailing"

    def test_consecutive_heading_para_heading_para(self, converter: MarkdownToBlocksConverter):
        md = "# First\n\nPara one.\n\n## Second\n\nPara two.\n"
        container = converter.convert(md)
        assert len(container.blocks) == 2
        assert container.blocks[0].data == "# First\nPara one."
        assert container.blocks[0].sub_type == BlockSubType.PARAGRAPH
        assert container.blocks[1].data == "## Second\nPara two."
        assert container.blocks[1].sub_type == BlockSubType.PARAGRAPH

    def test_heading_merges_preserves_inline_formatting(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("## Title\n\nHello **bold** world.\n")
        assert len(container.blocks) == 1
        block = container.blocks[0]
        assert block.data == "## Title\nHello **bold** world."
        assert block.format == DataFormat.MARKDOWN

    def test_heading_merge_with_multiple_paragraphs_only_merges_first(
        self, converter: MarkdownToBlocksConverter
    ):
        md = "## Section\n\nFirst para.\n\nSecond para.\n"
        container = converter.convert(md)
        assert len(container.blocks) == 2
        assert container.blocks[0].data == "## Section\nFirst para."
        assert container.blocks[0].sub_type == BlockSubType.PARAGRAPH
        assert container.blocks[1].data == "Second para."
        assert container.blocks[1].sub_type == BlockSubType.PARAGRAPH


class TestParagraphs:
    def test_paragraph_produces_text_block(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("Hello **bold** world\n")
        assert len(container.blocks) == 1
        block = container.blocks[0]
        assert block.type == BlockType.TEXT
        assert block.sub_type == BlockSubType.PARAGRAPH
        assert block.data == "Hello **bold** world"
        assert block.format == DataFormat.MARKDOWN

    def test_paragraph_preserves_inline_formatting_as_raw_markdown(
        self, converter: MarkdownToBlocksConverter
    ):
        markdown = "**bold** *italic* _italic_ __bold__ ~~strikethrough~~\n"
        container = converter.convert(markdown)
        block = container.blocks[0]
        assert block.format == DataFormat.MARKDOWN
        assert block.data == "**bold** *italic* _italic_ __bold__ ~~strikethrough~~"

    def test_plain_paragraph_stays_txt_format(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("Plain paragraph.\n")
        block = container.blocks[0]
        assert block.data == "Plain paragraph."
        assert block.format == DataFormat.MARKDOWN

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
        assert table_group.table_metadata.column_names is None
        assert table_group.data["column_headers"] == ["A", "B"]

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

    def test_nested_list_captures_nested_content_in_raw_markdown(
        self, converter: MarkdownToBlocksConverter
    ):
        container = converter.convert("- outer\n  - inner\n")
        assert len(container.block_groups) == 1

        list_group = container.block_groups[0]
        assert list_group.type == GroupType.LIST
        assert _child_group_indices(list_group) == []

        list_items = [
            block for block in container.blocks
            if block.sub_type == BlockSubType.LIST_ITEM
        ]
        assert len(list_items) == 1
        assert list_items[0].data == "outer\n  - inner"
        assert list_items[0].format == DataFormat.MARKDOWN
        assert list_items[0].parent_index == list_group.index
        assert _child_block_indices(list_group) == [list_items[0].index]


class TestImages:
    def test_image_produces_image_block_with_caption_map(self, converter: MarkdownToBlocksConverter):
        container = converter.convert(
            "![Image_1](https://example.com/a.png)\n",
            caption_map={"Image_1": "data:image/png;base64,abc"},
        )
        image_blocks = _blocks_by_type(container, BlockType.IMAGE)
        assert len(image_blocks) == 1
        assert image_blocks[0].image_metadata is not None
        assert image_blocks[0].image_metadata.captions == ["Image_1"]
        assert image_blocks[0].format == DataFormat.BASE64

    def test_image_uses_caption_map(self, converter: MarkdownToBlocksConverter):
        container = converter.convert(
            "![Image_1](https://example.com/a.png)\n",
            caption_map={"Image_1": "data:image/png;base64,abc"},
        )
        image_block = _blocks_by_type(container, BlockType.IMAGE)[0]
        assert image_block.data == {"uri": "data:image/png;base64,abc"}
        assert image_block.format == DataFormat.BASE64

    def test_image_without_caption_map_skips_image_block(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("![alt](https://example.com/a.png)\n")
        assert _blocks_by_type(container, BlockType.IMAGE) == []

    def test_paragraph_with_text_and_image_splits_blocks(self, converter: MarkdownToBlocksConverter):
        container = converter.convert(
            "See ![Image_1](https://example.com/a.png) here\n",
            caption_map={"Image_1": "data:image/png;base64,abc"},
        )
        assert len(container.blocks) == 3
        assert container.blocks[0].type == BlockType.TEXT
        assert container.blocks[0].data == "See "
        assert container.blocks[0].parent_block_index is None
        assert container.blocks[1].type == BlockType.IMAGE
        assert container.blocks[1].format == DataFormat.BASE64
        assert container.blocks[1].parent_block_index is None
        assert container.blocks[2].type == BlockType.TEXT
        assert container.blocks[2].data == " here"
        assert container.blocks[2].parent_block_index is None

    # ------------------------------------------------------------------
    # No alt text
    # ------------------------------------------------------------------

    def test_image_no_alt_text_skips_without_caption_map(self, converter: MarkdownToBlocksConverter):
        """![](url) without caption_map → no IMAGE block."""
        container = converter.convert("![](https://example.com/img.png)\n")
        assert _blocks_by_type(container, BlockType.IMAGE) == []

    def test_image_no_alt_no_src_skips_image_block(self, converter: MarkdownToBlocksConverter):
        """![]() — empty alt and empty src → no IMAGE block emitted."""
        container = converter.convert("![]() \n")
        assert _blocks_by_type(container, BlockType.IMAGE) == []

    # ------------------------------------------------------------------
    # caption_map lookup behaviour
    # ------------------------------------------------------------------

    def test_caption_map_miss_skips_image_block(self, converter: MarkdownToBlocksConverter):
        """A caption_map present but key doesn't match → no IMAGE block."""
        container = converter.convert(
            "![Image_1](https://example.com/img.png)\n",
            caption_map={"Image_2": "data:image/png;base64,xyz"},
        )
        assert _blocks_by_type(container, BlockType.IMAGE) == []

    def test_caption_map_lookup_is_case_sensitive(self, converter: MarkdownToBlocksConverter):
        """caption_map lookup uses the exact alt-text string (case-sensitive)."""
        container = converter.convert(
            "![Image_1](https://example.com/img.png)\n",
            caption_map={"image_1": "data:image/png;base64,xyz"},
        )
        assert _blocks_by_type(container, BlockType.IMAGE) == []

    # ------------------------------------------------------------------
    # Multiple images
    # ------------------------------------------------------------------

    def test_multiple_images_standalone_produce_multiple_blocks(
        self, converter: MarkdownToBlocksConverter
    ):
        """Two consecutive images each produce their own IMAGE block."""
        md = (
            "![Image_1](https://example.com/a.png)\n\n"
            "![Image_2](https://example.com/b.png)\n"
        )
        container = converter.convert(
            md,
            caption_map={
                "Image_1": "data:image/png;base64,AAA",
                "Image_2": "data:image/png;base64,BBB",
            },
        )
        image_blocks = _blocks_by_type(container, BlockType.IMAGE)
        assert len(image_blocks) == 2
        assert image_blocks[0].data == {"uri": "data:image/png;base64,AAA"}
        assert image_blocks[1].data == {"uri": "data:image/png;base64,BBB"}

    def test_two_images_inline_one_in_map_one_not(
        self, converter: MarkdownToBlocksConverter
    ):
        """Mixed caption_map: only the resolved image produces an IMAGE block."""
        md = "![Image_1](https://a.com/1.png) and ![Image_2](https://b.com/2.png)\n"
        container = converter.convert(
            md, caption_map={"Image_1": "data:image/png;base64,AAA"}
        )
        image_blocks = _blocks_by_type(container, BlockType.IMAGE)
        assert len(image_blocks) == 1
        assert image_blocks[0].data == {"uri": "data:image/png;base64,AAA"}
        assert image_blocks[0].format == DataFormat.BASE64

    def test_image_only_paragraph_produces_no_text_block(
        self, converter: MarkdownToBlocksConverter
    ):
        """A paragraph that is purely an image emits a single IMAGE block at root."""
        container = converter.convert(
            "![Image_1](https://example.com/img.png)\n",
            caption_map={"Image_1": "data:image/png;base64,abc"},
        )
        assert _content_text_blocks(container) == []
        assert len(_blocks_by_type(container, BlockType.IMAGE)) == 1
        assert container.blocks[0].type == BlockType.IMAGE
        assert container.blocks[0].parent_block_index is None

    # ------------------------------------------------------------------
    # parent_index inside groups
    # ------------------------------------------------------------------

    def test_image_inside_blockquote_is_preserved_in_raw_markdown(
        self, converter: MarkdownToBlocksConverter
    ):
        """Without caption_map, blockquote splits into empty container and text fragment."""
        container = converter.convert("> ![Image_1](https://example.com/img.png)\n")
        assert len(container.block_groups) == 1
        quote_group = container.block_groups[0]
        assert quote_group.type == GroupType.TEXT_SECTION
        assert quote_group.sub_type == GroupSubType.QUOTE
        assert _blocks_by_type(container, BlockType.IMAGE) == []

        quote_containers = _split_container_blocks(container, BlockSubType.QUOTE)
        assert len(quote_containers) == 1
        _assert_empty_text_split_container(quote_containers[0])
        fragments = _fragment_blocks(container, quote_containers[0].index)
        assert len(fragments) == 1
        assert fragments[0].data == "> "
        assert fragments[0].parent_block_index == quote_containers[0].index
        assert quote_containers[0].parent_index == quote_group.index

    def test_image_inside_blockquote_with_caption_map_emits_image_block(
        self, converter: MarkdownToBlocksConverter
    ):
        """With caption_map, blockquote splits into container, text fragment, and IMAGE."""
        caption_map = {"Image_1": "data:image/png;base64,QUOTEIMG"}
        container = converter.convert(
            "> ![Image_1](https://example.com/img.png)\n",
            caption_map=caption_map,
        )
        quote_group = container.block_groups[0]
        quote_containers = _split_container_blocks(container, BlockSubType.QUOTE)
        image_blocks = _blocks_by_type(container, BlockType.IMAGE)

        assert len(quote_containers) == 1
        _assert_empty_text_split_container(quote_containers[0])
        fragments = _fragment_blocks(container, quote_containers[0].index)
        assert fragments[0].data == "> "
        assert len(image_blocks) == 1
        assert image_blocks[0].data == {"uri": "data:image/png;base64,QUOTEIMG"}
        assert image_blocks[0].format == DataFormat.BASE64
        assert image_blocks[0].parent_index is None
        assert image_blocks[0].parent_block_index == quote_containers[0].index
        assert _child_block_indices(quote_group) == [quote_containers[0].index]

    def test_image_inside_blockquote_without_matching_uri_skips_image_block(
        self, converter: MarkdownToBlocksConverter
    ):
        """caption_map present but alt not resolved → container and text fragment only."""
        container = converter.convert(
            "> ![Image_1](https://example.com/img.png)\n",
            caption_map={"Image_2": "data:image/png;base64,OTHER"},
        )
        assert _blocks_by_type(container, BlockType.IMAGE) == []
        quote_container = container.blocks[0]
        _assert_empty_text_split_container(quote_container)
        assert _fragment_blocks(container, quote_container.index)[0].data == "> "

    def test_image_inside_list_is_preserved_in_raw_markdown(
        self, converter: MarkdownToBlocksConverter
    ):
        """Without caption_map, list item splits into empty container and text fragment."""
        container = converter.convert("- ![Image_1](https://example.com/img.png)\n")
        assert len(container.block_groups) == 1
        list_group = container.block_groups[0]
        assert list_group.type == GroupType.LIST
        assert _blocks_by_type(container, BlockType.IMAGE) == []

        list_container = container.blocks[0]
        assert list_container.sub_type == BlockSubType.LIST_ITEM
        _assert_empty_text_split_container(list_container)
        assert _fragment_blocks(container, list_container.index) == []
        assert list_container.parent_index == list_group.index

    def test_image_inside_list_with_caption_map_emits_image_block(
        self, converter: MarkdownToBlocksConverter
    ):
        """With caption_map, list item splits into container, text fragment, and IMAGE."""
        caption_map = {"Image_1": "data:image/png;base64,LISTIMG"}
        container = converter.convert(
            "- Available connectors: ![Image_1](https://example.com/img.png)\n",
            caption_map=caption_map,
        )
        list_group = container.block_groups[0]
        list_containers = _split_container_blocks(container, BlockSubType.LIST_ITEM)
        image_blocks = _blocks_by_type(container, BlockType.IMAGE)

        assert len(list_containers) == 1
        _assert_empty_text_split_container(list_containers[0])
        fragments = _fragment_blocks(container, list_containers[0].index)
        assert fragments[0].data == "Available connectors: "
        assert fragments[0].sub_type is None
        assert len(image_blocks) == 1
        assert image_blocks[0].data == {"uri": "data:image/png;base64,LISTIMG"}
        assert image_blocks[0].format == DataFormat.BASE64
        assert image_blocks[0].parent_index is None
        assert image_blocks[0].parent_block_index == list_containers[0].index
        assert image_blocks[0].sub_type is None
        assert _child_block_indices(list_group) == [list_containers[0].index]

    def test_image_inside_table_cell_with_caption_map_emits_image_block(
        self, converter: MarkdownToBlocksConverter
    ):
        """Table cells with inline images split into TABLE_ROW container, TEXT, and IMAGE."""
        markdown = (
            "| Name | Icon |\n"
            "| --- | --- |\n"
            "| Connectors | ![Image_1](https://example.com/icon.png) |\n"
        )
        caption_map = {"Image_1": "data:image/png;base64,TABLEIMG"}
        container = converter.convert(markdown, caption_map=caption_map)

        table_group = container.block_groups[0]
        row_containers = _blocks_by_type(container, BlockType.TABLE_ROW)
        image_blocks = _blocks_by_type(container, BlockType.IMAGE)

        assert len(row_containers) == 1
        assert row_containers[0].data is not None
        assert _is_empty_split_container(row_containers[0])
        assert len(image_blocks) == 1
        assert image_blocks[0].data == {"uri": "data:image/png;base64,TABLEIMG"}
        assert image_blocks[0].format == DataFormat.BASE64
        assert image_blocks[0].parent_index is None
        assert image_blocks[0].parent_block_index == row_containers[0].index
        fragments = _fragment_blocks(container, row_containers[0].index)
        assert fragments[0].type == BlockType.TEXT
        assert fragments[0].data == "Name: Connectors"
        assert _child_block_indices(table_group) == [row_containers[0].index]

    def test_table_header_image_is_skipped(
        self, converter: MarkdownToBlocksConverter
    ):
        markdown = (
            "| Label | ![Image_1](https://example.com/logo.png) |\n"
            "| --- | --- |\n"
            "| Value | cell |\n"
        )
        caption_map = {"Image_1": "data:image/png;base64,HEADERIMG"}
        container = converter.convert(markdown, caption_map=caption_map)

        table_group = container.block_groups[0]
        image_blocks = _blocks_by_type(container, BlockType.IMAGE)

        assert table_group.data["column_headers"] == ["Label", ""]
        assert len(image_blocks) == 0

    def test_table_mid_cell_image_splits_row_text_fragments(
        self, converter: MarkdownToBlocksConverter
    ):
        markdown = (
            "| col1 | col2 | col3 |\n"
            "| --- | --- | --- |\n"
            "| text1 | Text2 ![Image_1](https://example.com/img.png) Text3 | text4 |\n"
        )
        caption_map = {"Image_1": "data:image/png;base64,IMG"}
        container = converter.convert(markdown, caption_map=caption_map)

        row_container = _blocks_by_type(container, BlockType.TABLE_ROW)[0]
        fragments = _fragment_blocks(container, row_container.index)
        text_fragments = [b for b in fragments if b.type == BlockType.TEXT]
        image_blocks = [b for b in fragments if b.type == BlockType.IMAGE]

        assert row_container.data is not None
        assert _is_empty_split_container(row_container)
        assert len(text_fragments) == 2
        assert text_fragments[0].data == "col1: text1, col2: Text2"
        assert text_fragments[1].data == "col2: Text3, col3: text4"
        assert len(image_blocks) == 1
        assert image_blocks[0].parent_block_index == row_container.index

    def test_table_row_images_follow_their_row_in_block_order(
        self, converter: MarkdownToBlocksConverter
    ):
        markdown = (
            "| Name | Icon |\n"
            "| --- | --- |\n"
            "| Row1 | ![Image_1](https://example.com/1.png) |\n"
            "| Row2 | ![Image_2](https://example.com/2.png) |\n"
        )
        caption_map = {
            "Image_1": "data:image/png;base64,IMG1",
            "Image_2": "data:image/png;base64,IMG2",
        }
        container = converter.convert(markdown, caption_map=caption_map)

        assert [block.type for block in container.blocks] == [
            BlockType.TABLE_ROW,
            BlockType.TEXT,
            BlockType.IMAGE,
            BlockType.TABLE_ROW,
            BlockType.TEXT,
            BlockType.IMAGE,
        ]
        assert container.blocks[2].data == {"uri": "data:image/png;base64,IMG1"}
        assert container.blocks[5].data == {"uri": "data:image/png;base64,IMG2"}

    def test_image_inside_code_block_is_not_emitted(
        self, converter: MarkdownToBlocksConverter
    ):
        """Fenced code is literal text; image syntax inside it must not become IMAGE blocks."""
        markdown = "```markdown\n![Image_1](https://example.com/img.png)\n```\n"
        caption_map = {"Image_1": "data:image/png;base64,SHOULDNOTAPPEAR"}
        container = converter.convert(markdown, caption_map=caption_map)

        code_blocks = [b for b in container.blocks if b.sub_type == BlockSubType.CODE]
        image_blocks = _blocks_by_type(container, BlockType.IMAGE)

        assert len(code_blocks) == 1
        assert "![Image_1](https://example.com/img.png)" in code_blocks[0].data
        assert image_blocks == []

    def test_html_img_inside_blockquote_with_caption_map_emits_image_block(
        self, converter: MarkdownToBlocksConverter
    ):
        """HTML img tags embedded in blockquote raw markdown are also surfaced."""
        markdown = '> <img src="https://example.com/a.png" alt="Image_1"/>\n'
        caption_map = {"Image_1": "data:image/png;base64,HTMLIMG"}
        container = converter.convert(markdown, caption_map=caption_map)

        image_blocks = _blocks_by_type(container, BlockType.IMAGE)
        assert len(image_blocks) == 1
        assert image_blocks[0].data == {"uri": "data:image/png;base64,HTMLIMG"}
        assert image_blocks[0].format == DataFormat.BASE64

    # ------------------------------------------------------------------
    # Full pipeline: extract_and_replace_images → parse_to_blocks
    # ------------------------------------------------------------------

    def test_full_pipeline_extract_then_parse(self, converter: MarkdownToBlocksConverter):
        """
        Simulates the real indexing pipeline:
        1. extract_and_replace_images normalises alt text to Image_N.
        2. URL is resolved to base64 and stored in caption_map.
        3. parse_to_blocks produces an IMAGE block with BASE64 data.
        """
        pytest.importorskip("markdown")
        from app.modules.parsers.markdown.docling_markdown_parser import (
            _extract_and_replace_images,
        )

        original_md = "# Report\n\n![chart](https://cdn.example.com/chart.png)\n"
        modified_md, images = _extract_and_replace_images(original_md)

        assert len(images) == 1
        new_alt = images[0]["new_alt_text"]
        assert new_alt == "Image_1"

        fake_base64 = "data:image/png;base64,CHARTDATA"
        caption_map = {new_alt: fake_base64}

        container = converter.convert(modified_md, caption_map=caption_map)

        _assert_all_blocks_have_data(container)
        image_blocks = _blocks_by_type(container, BlockType.IMAGE)
        assert len(image_blocks) == 1
        assert image_blocks[0].data == {"uri": fake_base64}
        assert image_blocks[0].format == DataFormat.BASE64
        assert image_blocks[0].image_metadata.captions == ["Image_1"]

        text_blocks = [
            block for block in container.blocks
            if block.type == BlockType.TEXT and block.sub_type == BlockSubType.PARAGRAPH
        ]
        assert len(text_blocks) == 1
        assert text_blocks[0].data == "# Report"
        assert text_blocks[0].format == DataFormat.MARKDOWN
        assert text_blocks[0].parent_block_index is None

    def test_full_pipeline_multiple_images(self, converter: MarkdownToBlocksConverter):
        """extract_and_replace_images assigns sequential Image_N labels."""
        pytest.importorskip("markdown")
        from app.modules.parsers.markdown.docling_markdown_parser import (
            _extract_and_replace_images,
        )

        original_md = (
            "![logo](https://cdn.example.com/logo.png) "
            "and ![banner](https://cdn.example.com/banner.png)\n"
        )
        modified_md, images = _extract_and_replace_images(original_md)

        assert len(images) == 2
        assert images[0]["new_alt_text"] == "Image_1"
        assert images[1]["new_alt_text"] == "Image_2"

        caption_map = {
            "Image_1": "data:image/png;base64,LOGO",
            "Image_2": "data:image/png;base64,BANNER",
        }
        container = converter.convert(modified_md, caption_map=caption_map)
        _assert_all_blocks_have_data(container)
        image_blocks = _blocks_by_type(container, BlockType.IMAGE)
        assert len(image_blocks) == 2
        assert image_blocks[0].data == {"uri": "data:image/png;base64,LOGO"}
        assert image_blocks[1].data == {"uri": "data:image/png;base64,BANNER"}

    def test_full_pipeline_image_url_not_converted_to_base64(
        self, converter: MarkdownToBlocksConverter
    ):
        """When base64 conversion fails, no caption_map entry → no IMAGE block."""
        pytest.importorskip("markdown")
        from app.modules.parsers.markdown.docling_markdown_parser import (
            _extract_and_replace_images,
        )

        original_md = "![photo](https://cdn.example.com/photo.png)\n"
        modified_md, images = _extract_and_replace_images(original_md)

        caption_map: dict[str, str] = {}

        container = converter.convert(modified_md, caption_map=caption_map)
        _assert_all_blocks_have_data(container)
        assert _blocks_by_type(container, BlockType.IMAGE) == []


class TestBlockquotes:
    def test_blockquote_produces_text_section_group(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("> quoted text\n")
        assert len(container.block_groups) == 1
        quote_group = container.block_groups[0]
        assert quote_group.type == GroupType.TEXT_SECTION
        assert quote_group.sub_type == GroupSubType.QUOTE
        quote_block = container.blocks[0]
        assert quote_block.sub_type == BlockSubType.QUOTE
        assert quote_block.data == "> quoted text"
        assert quote_block.format == DataFormat.MARKDOWN
        assert quote_block.parent_index == quote_group.index
        assert _child_block_indices(quote_group) == [quote_block.index]

    def test_multiline_blockquote_is_single_block(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("> line one\n> line two\n")
        assert len(container.blocks) == 1
        quote_block = container.blocks[0]
        assert quote_block.sub_type == BlockSubType.QUOTE
        assert quote_block.data == "> line one\n> line two"
        assert quote_block.format == DataFormat.MARKDOWN
        assert quote_block.parent_index == container.block_groups[0].index

    def test_nested_blockquote_captures_nested_content_in_raw_markdown(
        self, converter: MarkdownToBlocksConverter
    ):
        container = converter.convert("> outer\n> > inner\n")
        assert len(container.block_groups) == 1

        quote_group = container.block_groups[0]
        assert quote_group.type == GroupType.TEXT_SECTION
        assert quote_group.sub_type == GroupSubType.QUOTE
        assert _child_group_indices(quote_group) == []

        quote_block = container.blocks[0]
        assert quote_block.sub_type == BlockSubType.QUOTE
        assert quote_block.data == "> outer\n> > inner"
        assert quote_block.format == DataFormat.MARKDOWN
        assert quote_block.parent_index == quote_group.index
        assert _child_block_indices(quote_group) == [quote_block.index]


class TestHtmlBlocks:
    def test_html_block_uses_html_format(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("<div>hello</div>\n")
        assert len(container.blocks) == 1
        assert len(container.block_groups) == 1
        block = container.blocks[0]
        html_group = container.block_groups[0]
        assert html_group.type == GroupType.TEXT_SECTION
        assert block.format == DataFormat.HTML
        assert block.data == "<div>hello</div>"
        assert block.sub_type == BlockSubType.PARAGRAPH
        assert block.parent_index == html_group.index
        assert _child_block_indices(html_group) == [block.index]


class TestDividers:
    def test_hr_does_not_emit_block(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("---\n")
        assert container.blocks == []


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
        assert block_types[0] == (BlockSubType.PARAGRAPH, BlockType.TEXT)
        assert container.blocks[0].data == "# Title\nParagraph one."
        assert container.blocks[0].format == DataFormat.MARKDOWN
        assert block_types[1] == (BlockSubType.LIST_ITEM, BlockType.TEXT)
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

        _assert_all_blocks_have_data(container)
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
        pytest.importorskip("markdown")
        from app.modules.parsers.markdown.markdown_it_parser import MarkdownItParser

        parser = MarkdownItParser()
        expected = MagicMock()
        parser._converter.convert = MagicMock(return_value=expected)

        result = parser.parse_to_blocks("# Hello", caption_map={"Image_1": "uri"})

        assert result is expected
        parser._converter.convert.assert_called_once_with(
            "# Hello",
            caption_map={"Image_1": "uri"},
            page_number=None,
        )


class TestSuppressedStructuralEmission:
    """Content inside list items and blockquotes must not emit inner blocks."""

    def test_heading_inside_list_is_not_emitted(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("- # Not a heading\n")
        headings = [b for b in container.blocks if b.sub_type == BlockSubType.HEADING]
        assert headings == []
        list_items = [b for b in container.blocks if b.sub_type == BlockSubType.LIST_ITEM]
        assert len(list_items) == 1
        assert "# Not a heading" in list_items[0].data

    def test_paragraph_inside_blockquote_is_not_emitted(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("> inner paragraph\n")
        paragraphs = [b for b in container.blocks if b.sub_type == BlockSubType.PARAGRAPH]
        assert paragraphs == []
        assert container.blocks[0].sub_type == BlockSubType.QUOTE

    def test_code_inside_list_is_not_emitted(self, converter: MarkdownToBlocksConverter):
        md = "- item\n\n  ```python\n  x = 1\n  ```\n"
        container = converter.convert(md)
        code_blocks = [b for b in container.blocks if b.sub_type == BlockSubType.CODE]
        assert code_blocks == []

    def test_hr_inside_list_is_not_emitted(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("- item\n\n  ---\n")
        dividers = [b for b in container.blocks if b.sub_type == BlockSubType.DIVIDER]
        assert dividers == []

    def test_html_inside_list_is_not_emitted(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("- item\n\n  <div>html</div>\n")
        html_blocks = [b for b in container.blocks if b.format == DataFormat.HTML]
        assert html_blocks == []

    def test_table_inside_blockquote_is_not_emitted(self, converter: MarkdownToBlocksConverter):
        md = "> | A | B |\n> | --- | --- |\n> | 1 | 2 |\n"
        container = converter.convert(md)
        assert _groups_by_type(container, GroupType.TABLE) == []
        assert container.blocks[0].sub_type == BlockSubType.QUOTE

    def test_blockquote_inside_list_is_ignored(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("- outer\n  > inner quote\n")
        quote_groups = _groups_by_type(container, GroupType.TEXT_SECTION)
        assert quote_groups == []
        list_items = [b for b in container.blocks if b.sub_type == BlockSubType.LIST_ITEM]
        assert len(list_items) == 1
        assert "> inner quote" in list_items[0].data


class TestNestedGroups:
    def test_open_group_records_child_on_parent_stack(self):
        """Nested groups append child_group_indices on the parent _OpenGroup."""
        walker = _TokenWalker("")
        walker._open_group(GroupType.TEXT_SECTION, GroupSubType.QUOTE)
        walker._open_group(GroupType.LIST)
        parent_open = walker.group_stack[0]
        assert parent_open.child_group_indices == [1]
        list_group = walker.block_groups[1]
        assert list_group.parent_index == 0

    def test_start_table_records_child_when_parent_group_open(self):
        walker = _TokenWalker("")
        walker._open_group(GroupType.LIST)
        walker._start_table()
        parent_open = walker.group_stack[0]
        assert parent_open.child_group_indices == [1]
        table_group = walker.block_groups[1]
        assert table_group.parent_index == 0
        assert table_group.type == GroupType.TABLE


class TestInlineRendering:
    def test_link_in_paragraph_preserves_markdown(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("Visit [example](https://example.com) today.\n")
        block = container.blocks[0]
        assert block.format == DataFormat.MARKDOWN
        assert block.data == "Visit [example](https://example.com) today."

    def test_inline_code_in_paragraph(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("Use `print()` here.\n")
        block = container.blocks[0]
        assert block.format == DataFormat.MARKDOWN
        assert "`print()`" in block.data

    def test_formatted_text_with_image_uses_markdown(self, converter: MarkdownToBlocksConverter):
        container = converter.convert(
            "**Bold** ![Image_1](https://example.com/a.png)\n",
            caption_map={"Image_1": "data:image/png;base64,abc"},
        )
        assert len(container.blocks) == 2
        text_block = container.blocks[0]
        assert text_block.type == BlockType.TEXT
        assert text_block.format == DataFormat.MARKDOWN
        assert text_block.data.strip() == "**Bold**"
        assert text_block.parent_block_index is None
        image_block = container.blocks[1]
        assert image_block.type == BlockType.IMAGE
        assert image_block.format == DataFormat.BASE64
        assert image_block.parent_block_index is None

    def test_hard_line_break_renders_as_newline(self, converter: MarkdownToBlocksConverter):
        container = converter.convert("Line one  \nLine two\n")
        block = container.blocks[0]
        assert block.format == DataFormat.MARKDOWN
        assert "Line one\nLine two" in block.data


class TestTableEdgeCases:
    def test_table_with_empty_cell(self, converter: MarkdownToBlocksConverter):
        md = "| A | B |\n| --- | --- |\n| | val |\n"
        container = converter.convert(md)
        row_block = _blocks_by_type(container, BlockType.TABLE_ROW)[0]
        assert row_block.data["cells"] == ["", "val"]

    def test_finish_table_flushes_trailing_current_row_as_header(self):
        walker = _TokenWalker("")
        walker.block_groups.append(
            BlockGroup(index=0, type=GroupType.TABLE, parent_index=None)
        )
        walker.table_state = _TableState(group_index=0)
        walker.table_state.current_row = [
            _TableCell(plain="h1", markdown="h1"),
            _TableCell(plain="h2", markdown="h2"),
        ]
        walker._finish_table()
        table_group = walker.block_groups[0]
        assert table_group.table_metadata is not None
        assert table_group.table_metadata.has_header is True
        assert table_group.data["column_headers"] == ["h1", "h2"]
        assert walker.blocks == []

    def test_finish_table_flushes_trailing_current_row_as_data_row(self):
        walker = _TokenWalker("")
        walker.block_groups.append(
            BlockGroup(index=0, type=GroupType.TABLE, parent_index=None)
        )
        walker.table_state = _TableState(
            group_index=0,
            headers=[_TableCell(plain="A", markdown="A")],
        )
        walker.table_state.current_row = [_TableCell(plain="1", markdown="1")]
        walker._finish_table()
        assert len(walker.blocks) == 1
        assert walker.blocks[0].data["cells"] == ["1"]


class TestSplitRawMarkdownIntoSegments:
    def test_markdown_image_splits_text(self):
        segments = _split_raw_markdown_into_segments("before ![alt](url) after")
        assert [(s.kind, s.text or s.alt_text) for s in segments] == [
            ("text", "before "),
            ("image", "alt"),
            ("text", " after"),
        ]

    def test_html_img_tag_splits_text(self):
        segments = _split_raw_markdown_into_segments(
            'text <img alt="Image_1" src="x"> more'
        )
        assert [(s.kind, s.text or s.alt_text) for s in segments] == [
            ("text", "text "),
            ("image", "Image_1"),
            ("text", " more"),
        ]

    def test_empty_string_returns_no_segments(self):
        assert _split_raw_markdown_into_segments("") == []
        assert _split_raw_markdown_into_segments("   ") == []


class TestBlockDataNeverNone:
    """Emitted blocks must always carry data; empty text containers use ""."""

    @pytest.mark.parametrize(
        ("markdown", "caption_map"),
        [
            (
                "- item ![Image_1](https://example.com/a.png) tail\n",
                {"Image_1": "data:image/png;base64,IMG"},
            ),
            (
                "> quote ![Image_1](https://example.com/a.png)\n",
                {"Image_1": "data:image/png;base64,IMG"},
            ),
            (
                "| A | B |\n| --- | --- |\n| x | ![Image_1](https://example.com/a.png) |\n",
                {"Image_1": "data:image/png;base64,IMG"},
            ),
            ("- ![Image_1](https://example.com/a.png)\n", {}),
        ],
    )
    def test_image_split_outputs_never_have_none_data(
        self,
        converter: MarkdownToBlocksConverter,
        markdown: str,
        caption_map: dict[str, str],
    ) -> None:
        container = converter.convert(markdown, caption_map=caption_map)
        _assert_all_blocks_have_data(container)
        for block in container.blocks:
            if _is_empty_split_container(block) and block.type == BlockType.TEXT:
                assert block.data == ""

    def test_unsplit_document_never_has_none_data(
        self, converter: MarkdownToBlocksConverter
    ) -> None:
        markdown = (
            "# Report\n\n"
            "Plain paragraph.\n\n"
            "| Metric | Value |\n| --- | --- |\n| Users | 10 |\n\n"
            "- action item\n"
        )
        container = converter.convert(markdown)
        _assert_all_blocks_have_data(container)


class TestImageSplitContainers:
    def test_image_only_list_item_emits_image_block_with_list_item_sub_type(
        self, converter: MarkdownToBlocksConverter
    ):
        container = converter.convert(
            "- ![Image_1](https://example.com/img.png)\n",
            caption_map={"Image_1": "data:image/png;base64,LISTIMG"},
        )
        list_containers = _split_container_blocks(container, BlockSubType.LIST_ITEM)
        image_blocks = _blocks_by_type(container, BlockType.IMAGE)

        assert list_containers == []
        assert len(image_blocks) == 1
        assert image_blocks[0].sub_type == BlockSubType.LIST_ITEM
        assert image_blocks[0].parent_block_index is None
        assert image_blocks[0].data == {"uri": "data:image/png;base64,LISTIMG"}

    def test_image_split_table_row_container_carries_only_row_number(
        self, converter: MarkdownToBlocksConverter
    ):
        markdown = (
            "| Name | Icon |\n"
            "| --- | --- |\n"
            "| Connectors | ![Image_1](https://example.com/icon.png) |\n"
        )
        container = converter.convert(
            markdown,
            caption_map={"Image_1": "data:image/png;base64,TABLEIMG"},
        )
        row_container = _blocks_by_type(container, BlockType.TABLE_ROW)[0]
        assert row_container.data == {"row_number": 1}
        assert row_container.data is not None
        assert _is_empty_split_container(row_container)


class TestTokenWalkerUtilities:
    def test_slice_token_map_returns_empty_when_map_missing(self):
        walker = _TokenWalker("line1\nline2")
        token = Token("list_item_open", "li", 1)
        token.map = None
        assert walker._slice_token_map(token) == ""

    def test_slice_token_map_returns_empty_when_map_out_of_bounds(self):
        walker = _TokenWalker("line1\nline2")
        token = Token("list_item_open", "li", 1)
        token.map = [10, 11]
        assert walker._slice_token_map(token) == ""

    def test_close_group_on_empty_stack_is_noop(self):
        walker = _TokenWalker("")
        walker._close_group()
        assert walker.group_stack == []

    def test_finish_table_without_state_is_noop(self):
        walker = _TokenWalker("")
        walker._finish_table()
        assert walker.table_state is None

    def test_format_table_row_without_headers(self):
        result = _TokenWalker._format_table_row([], ["a", "b", "c"])
        assert result == "a, b, c"

    def test_format_table_row_column_fallback(self):
        result = _TokenWalker._format_table_row(["H1"], ["c1", "c2"])
        assert result == "H1: c1, Column 2: c2"

    def test_render_inline_softbreak_and_hardbreak(self):
        walker = _TokenWalker("")
        soft = Token("softbreak", "br", 0)
        hard = Token("hardbreak", "br", 0)
        assert walker._render_inline(soft) == "\n"
        assert walker._render_inline(hard) == "\n"

    def test_render_inline_nested_children(self):
        walker = _TokenWalker("")
        parent = Token("strong_open", "strong", 1)
        child = Token("text", "", 0)
        child.content = "bold"
        parent.children = [child]
        assert walker._render_inline(parent) == "bold"

    def test_render_inline_markdown_code_and_html(self):
        walker = _TokenWalker("")
        code = Token("code_inline", "code", 0)
        code.content = "x"
        html = Token("html_inline", "", 0)
        html.content = "<b>hi</b>"
        assert walker._render_inline_markdown(code) == "`x`"
        assert walker._render_inline_markdown(html) == "<b>hi</b>"

    def test_render_inline_markdown_from_children_link(self):
        walker = _TokenWalker("")
        link_open = Token("link_open", "a", 1)
        link_open.attrs = {"href": "https://example.com"}
        text = Token("text", "", 0)
        text.content = "click"
        link_close = Token("link_close", "a", -1)
        result = walker._render_inline_markdown_from_children(
            [link_open, text, link_close]
        )
        assert result == "[click](https://example.com)"

    def test_render_inline_markdown_from_children_bold_markup(self):
        walker = _TokenWalker("")
        bold_open = Token("strong_open", "strong", 1)
        bold_open.markup = "**"
        text = Token("text", "", 0)
        text.content = "bold"
        bold_close = Token("strong_close", "strong", -1)
        result = walker._render_inline_markdown_from_children(
            [bold_open, text, bold_close]
        )
        assert result == "**bold**"

    def test_render_inline_markdown_from_children_skips_orphan_close(self):
        walker = _TokenWalker("")
        orphan_close = Token("strong_close", "strong", -1)
        text = Token("text", "", 0)
        text.content = "plain"
        result = walker._render_inline_markdown_from_children(
            [orphan_close, text]
        )
        assert result == "plain"

    def test_split_inline_content_no_children(self):
        walker = _TokenWalker("")
        inline = Token("inline", "", 0)
        inline.content = "  plain  "
        inline.children = None
        text, fmt = walker._split_inline_content(inline)
        assert text == "plain"
        assert fmt == DataFormat.MARKDOWN

    def test_split_inline_into_segments_with_markdown_image(self):
        walker = _TokenWalker("")
        inline = Token("inline", "", 0)
        inline.children = [
            Token("text", "", 0),
            Token("image", "img", 0),
            Token("text", "", 0),
        ]
        inline.children[0].content = "See "
        inline.children[1].content = "Image_1"
        inline.children[2].content = " here"
        segments = walker._split_inline_into_segments(inline)
        assert [(s.kind, s.text or s.alt_text) for s in segments] == [
            ("text", "See "),
            ("image", "Image_1"),
            ("text", " here"),
        ]

    def test_split_inline_into_segments_with_html_img_inline(self):
        walker = _TokenWalker("")
        inline = Token("inline", "", 0)
        html = Token("html_inline", "", 0)
        html.content = '<img alt="Image_1" src="x">'
        inline.children = [Token("text", "", 0), html]
        inline.children[0].content = "Before "
        segments = walker._split_inline_into_segments(inline)
        assert [(s.kind, s.text or s.alt_text) for s in segments] == [
            ("text", "Before "),
            ("image", "Image_1"),
        ]

    def test_render_inline_markdown_softbreak_and_hardbreak(self):
        walker = _TokenWalker("")
        soft = Token("softbreak", "br", 0)
        hard = Token("hardbreak", "br", 0)
        assert walker._render_inline_markdown(soft) == "\n"
        assert walker._render_inline_markdown(hard) == "\n"

    def test_render_inline_markdown_from_children_open_without_markup(self):
        walker = _TokenWalker("")
        em_open = Token("em_open", "em", 1)
        em_open.markup = ""
        text = Token("text", "", 0)
        text.content = "italic"
        em_close = Token("em_close", "em", -1)
        result = walker._render_inline_markdown_from_children(
            [em_open, text, em_close]
        )
        assert result == "italic"

    def test_render_inline_markdown_from_children_consumes_close_token(self):
        walker = _TokenWalker("")
        bold_open = Token("strong_open", "strong", 1)
        bold_open.markup = "**"
        text = Token("text", "", 0)
        text.content = "x"
        bold_close = Token("strong_close", "strong", -1)
        trailing = Token("text", "", 0)
        trailing.content = "!"
        result = walker._render_inline_markdown_from_children(
            [bold_open, text, bold_close, trailing]
        )
        assert result == "**x**!"

    def test_process_heading_open_without_inline(self):
        walker = _TokenWalker("")
        tokens = [Token("heading_open", "h1", 1), Token("heading_close", "h1", -1)]
        index = walker._process_token(tokens, 0)
        assert index == 1
        assert walker.blocks == []

    def test_process_paragraph_open_without_inline(self):
        walker = _TokenWalker("")
        tokens = [Token("paragraph_open", "p", 1), Token("paragraph_close", "p", -1)]
        index = walker._process_token(tokens, 0)
        assert index == 1
        assert walker.blocks == []

    def test_ordered_list_open_inside_blockquote_is_suppressed(self):
        walker = _TokenWalker("> 1. item\n")
        walker.blockquote_depth = 1
        tokens = [Token("ordered_list_open", "ol", 1)]
        index = walker._process_token(tokens, 0)
        assert index == 0
        assert walker.block_groups == []

    def test_list_item_open_with_empty_slice_skips_block(self):
        walker = _TokenWalker("")
        token = Token("list_item_open", "li", 1)
        token.map = [0, 0]
        index = walker._process_token([token], 0)
        assert index == 0
        assert walker.blocks == []
        assert walker.list_item_depth == 1

    def test_blockquote_open_adds_raw_markdown_block(self):
        walker = _TokenWalker("> quoted\n")
        token = Token("blockquote_open", "blockquote", 1)
        token.map = [0, 1]
        walker._process_token([token], 0)
        assert len(walker.blocks) == 1
        assert walker.blocks[0].sub_type == BlockSubType.QUOTE
        assert walker.blocks[0].data == "> quoted"

    def test_skip_inline_block_without_following_inline(self):
        walker = _TokenWalker("")
        tokens = [Token("heading_open", "h1", 1)]
        assert walker._skip_inline_block(tokens, 0) == 1

    def test_blockquote_open_with_empty_slice_opens_group_only(self):
        walker = _TokenWalker("")
        token = Token("blockquote_open", "blockquote", 1)
        token.map = [0, 0]
        walker._process_token([token], 0)
        assert len(walker.block_groups) == 1
        assert walker.block_groups[0].sub_type == GroupSubType.QUOTE
        assert walker.blocks == []

    def test_render_inline_markdown_unknown_token_returns_empty(self):
        walker = _TokenWalker("")
        unknown = Token("image", "img", 0)
        assert walker._render_inline_markdown(unknown) == ""

    def test_render_inline_markdown_from_children_missing_close(self):
        walker = _TokenWalker("")
        em_open = Token("em_open", "em", 1)
        em_open.markup = "*"
        text = Token("text", "", 0)
        text.content = "x"
        result = walker._render_inline_markdown_from_children([em_open, text])
        assert result == "*x*"

    def test_process_token_table_cell_without_inline(self):
        walker = _TokenWalker("")
        walker.table_state = _TableState(group_index=0)
        tokens = [Token("td_open", "td", 1), Token("td_close", "td", -1)]
        index = walker._process_token(tokens, 0)
        assert index == 1
        assert walker.table_state.current_row == [_TableCell(plain="", markdown="")]


class TestLongTextSplitting:
    def test_oversized_paragraph_splits_into_multiple_blocks(
        self, converter: MarkdownToBlocksConverter
    ):
        long_sentence = "A" * 600_000 + "."
        markdown = long_sentence + " " + ("B" * 600_000 + ".")
        container = converter.convert(markdown)

        text_blocks = _blocks_by_type(container, BlockType.TEXT)
        assert len(text_blocks) >= 2
        assert all(
            isinstance(block.data, str) and len(block.data) <= 500_000
            for block in text_blocks
        )

    def test_oversized_blockquote_uses_container_and_fragments(
        self, converter: MarkdownToBlocksConverter
    ):
        long_text = ("Quote sentence. " * 80_000).strip()
        container = converter.convert(f"> {long_text}")

        text_blocks = _blocks_by_type(container, BlockType.TEXT)
        containers = [b for b in text_blocks if b.data == ""]
        fragments = [b for b in text_blocks if b.parent_block_index is not None]
        assert containers
        assert len(fragments) >= 2
        assert all(len(block.data) <= 500_000 for block in fragments)
