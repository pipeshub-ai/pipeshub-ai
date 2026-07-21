"""Unit tests for Selectolax HTML parsing (converter + parser wrapper)."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, NonCallableMock, patch

import pytest


def _selectolax_really_installed() -> bool:
    """Return True only when selectolax is a genuine installed package.

    The test conftest may insert a MagicMock stub into sys.modules to allow
    collection.  importlib.util.find_spec hits that stub and returns non-None,
    so we need to distinguish the real module from the mock explicitly.
    """
    mod = sys.modules.get("selectolax")
    return mod is not None and not isinstance(mod, NonCallableMock)


pytestmark = pytest.mark.skipif(
    not _selectolax_really_installed(),
    reason="selectolax is not installed",
)

from app.models.blocks import (
    Block,
    BlockSubType,
    BlockType,
    BlocksContainer,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.modules.parsers.html_parser.html_to_blocks import (
    HtmlToBlocksConverter,
    _MAX_DOM_PROBE_DEPTH,
    _merge_heading_content_segments,
)
from app.modules.parsers.markdown.markdown_to_blocks import _Segment
from app.modules.parsers.html_parser.selectolax_html_parser import SelectolaxHtmlParser
from app.modules.transformers.block_container_validator import BlockContainerValidator

_COMPLEX_HTML = Path(__file__).resolve().parents[6] / "complex-html.html"


@pytest.fixture
def converter() -> HtmlToBlocksConverter:
    return HtmlToBlocksConverter()


@pytest.fixture
def parser() -> SelectolaxHtmlParser:
    return SelectolaxHtmlParser()


def _child_block_indices(group) -> list[int]:
    assert group.children is not None
    indices: list[int] = []
    for block_range in group.children.block_ranges:
        indices.extend(range(block_range.start, block_range.end + 1))
    return indices


def _child_group_indices(group) -> list[int]:
    assert group.children is not None
    indices: list[int] = []
    for group_range in group.children.block_group_ranges:
        indices.extend(range(group_range.start, group_range.end + 1))
    return indices


def _blocks_by_type(container, block_type: BlockType) -> list[Block]:
    return [block for block in container.blocks if block.type == block_type]


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


def _fragment_blocks(container, container_index: int) -> list[Block]:
    return [
        block
        for block in container.blocks
        if block.parent_block_index == container_index
    ]


def _split_container_blocks(container, sub_type: BlockSubType) -> list[Block]:
    return [
        block
        for block in container.blocks
        if block.sub_type == sub_type and _is_empty_split_container(block)
    ]


class TestHeadingMergesIntoParagraph:
    """Heading followed by paragraph-like content merges into one PARAGRAPH block."""

    def test_heading_merges_with_following_paragraph(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        container = converter.convert("<h2>Section</h2><p>Some text.</p>")
        assert len(container.blocks) == 1
        block = container.blocks[0]
        assert block.sub_type == BlockSubType.PARAGRAPH
        assert block.data == "## Section\nSome text."
        assert block.format == DataFormat.MARKDOWN

    def test_all_heading_levels_merge(self, converter: HtmlToBlocksConverter) -> None:
        for level in range(1, 7):
            container = converter.convert(
                f"<h{level}>Heading</h{level}><p>Body.</p>"
            )
            assert len(container.blocks) == 1
            assert container.blocks[0].data == f"{'#' * level} Heading\nBody."
            assert container.blocks[0].sub_type == BlockSubType.PARAGRAPH

    def test_heading_before_list_stays_standalone(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        container = converter.convert("<h3>Items</h3><ul><li>one</li><li>two</li></ul>")
        headings = [b for b in container.blocks if b.sub_type == BlockSubType.HEADING]
        list_items = [b for b in container.blocks if b.sub_type == BlockSubType.LIST_ITEM]
        assert len(headings) == 1
        assert headings[0].data == "Items"
        assert len(list_items) == 2

    def test_heading_before_table_stays_standalone(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        container = converter.convert(
            "<h2>Data</h2><table><tr><td>1</td></tr></table>"
        )
        headings = [b for b in container.blocks if b.sub_type == BlockSubType.HEADING]
        assert len(headings) == 1
        assert headings[0].data == "Data"

    def test_heading_before_blockquote_stays_standalone(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        container = converter.convert("<h1>Title</h1><blockquote><p>quoted</p></blockquote>")
        headings = [b for b in container.blocks if b.sub_type == BlockSubType.HEADING]
        assert len(headings) == 1
        assert headings[0].data == "Title"

    def test_heading_before_code_stays_standalone(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        container = converter.convert(
            "<h2>Example</h2><pre><code class=\"language-python\">x = 1</code></pre>"
        )
        headings = [b for b in container.blocks if b.sub_type == BlockSubType.HEADING]
        assert len(headings) == 1
        assert headings[0].data == "Example"

    def test_heading_before_another_heading_stays_standalone(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        container = converter.convert("<h1>H1</h1><h2>H2</h2>")
        headings = [b for b in container.blocks if b.sub_type == BlockSubType.HEADING]
        assert len(headings) == 2
        assert headings[0].data == "H1"
        assert headings[1].data == "H2"

    def test_heading_before_shallow_div_merges(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        container = converter.convert("<h2>Section</h2><div>Shallow div text.</div>")
        assert len(container.blocks) == 1
        assert container.blocks[0].data == "## Section\nShallow div text."

    def test_heading_merges_preserves_inline_formatting(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        container = converter.convert(
            "<h2>Title</h2><p>Hello <strong>bold</strong> world.</p>"
        )
        assert len(container.blocks) == 1
        block = container.blocks[0]
        assert block.data == "## Title\nHello **bold** world."
        assert block.format == DataFormat.MARKDOWN

    def test_merge_heading_content_segments_joins_text(self) -> None:
        merged = _merge_heading_content_segments(
            2,
            [_Segment(kind="text", text="Title")],
            [_Segment(kind="text", text="Body.")],
        )
        assert [(s.kind, s.text) for s in merged] == [
            ("text", "## Title\nBody."),
        ]

    def test_merge_heading_content_segments_preserves_image_first_heading(
        self,
    ) -> None:
        merged = _merge_heading_content_segments(
            2,
            [
                _Segment(kind="image", alt_text="logo"),
                _Segment(kind="text", text=" Release notes"),
            ],
            [_Segment(kind="text", text="We shipped v2.")],
        )
        assert [(s.kind, s.text if s.kind == "text" else s.alt_text) for s in merged] == [
            ("text", "## "),
            ("image", "logo"),
            ("text", " Release notes\nWe shipped v2."),
        ]


class TestHeadings:
    def test_heading_produces_text_block(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<h1>Title</h1><h2>Subtitle</h2>")
        headings = [
            block for block in container.blocks
            if block.sub_type == BlockSubType.HEADING
        ]
        assert len(headings) == 2
        assert headings[0].data == "Title"
        assert headings[0].format == DataFormat.MARKDOWN
        assert headings[1].data == "Subtitle"

    def test_heading_with_inline_formatting_uses_markdown(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<h2>Title with <em>emphasis</em></h2>")
        block = container.blocks[0]
        assert block.format == DataFormat.MARKDOWN
        assert block.data == "Title with *emphasis*"


class TestParagraphs:
    def test_bare_anchor_tag_emits_markdown_paragraph(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert('<a href="https://example.com">Visit Example</a>')
        assert len(container.blocks) == 1
        block = container.blocks[0]
        assert block.sub_type == BlockSubType.PARAGRAPH
        assert block.format == DataFormat.MARKDOWN
        assert block.data == "[Visit Example](https://example.com)"

    def test_paragraph_preserves_inline_markup_as_markdown(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<p>Hello <strong>bold</strong> world</p>")
        assert len(container.blocks) == 1
        assert container.blocks[0].data == "Hello **bold** world"
        assert container.blocks[0].format == DataFormat.MARKDOWN
        assert container.blocks[0].sub_type == BlockSubType.PARAGRAPH

    def test_plain_paragraph_stays_txt_format(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<p>Plain paragraph.</p>")
        block = container.blocks[0]
        assert block.data == "Plain paragraph."
        assert block.format == DataFormat.MARKDOWN

    def test_relative_link_resolved_with_base_url(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert(
            '<p>See <a href="/docs/guide">the guide</a>.</p>',
            base_url="https://example.com",
        )
        block = container.blocks[0]
        assert block.format == DataFormat.MARKDOWN
        assert block.data == "See [the guide](https://example.com/docs/guide)."

    def test_relative_link_left_unchanged_without_base_url(
        self, converter: HtmlToBlocksConverter,
    ) -> None:
        container = converter.convert('<p><a href="/docs">docs</a></p>')
        block = container.blocks[0]
        assert block.data == "[docs](/docs)"


class TestCode:
    def test_pre_code_block_with_language(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert(
            '<pre><code class="language-python">print("hi")</code></pre>'
        )
        assert len(container.block_groups) == 1
        assert container.block_groups[0].type == GroupType.CODE
        assert len(container.blocks) == 1
        block = container.blocks[0]
        assert block.sub_type == BlockSubType.CODE
        assert block.data == 'print("hi")'
        assert block.code_metadata is not None
        assert block.code_metadata.language == "python"

    def test_pre_emits_one_block_per_code_tag(self, converter: HtmlToBlocksConverter) -> None:
        html = (
            "<pre>"
            "  <code>A</code>"
            "  <code>B</code>"
            "  <h1>Hello</h1>"
            "</pre>"
        )
        container = converter.convert(html)
        code_blocks = [
            block for block in container.blocks
            if block.sub_type == BlockSubType.CODE
        ]
        heading_blocks = [
            block for block in container.blocks
            if block.sub_type == BlockSubType.HEADING
        ]
        code_groups = [
            group for group in container.block_groups
            if group.type == GroupType.CODE
        ]
        assert len(code_groups) == 2
        assert len(code_blocks) == 2
        assert code_blocks[0].data == "A"
        assert code_blocks[1].data == "B"
        assert len(heading_blocks) == 1
        assert heading_blocks[0].data == "Hello"


class TestLists:
    def test_bullet_list_group(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<ul><li>One</li><li>Two</li></ul>")
        assert len(container.block_groups) == 1
        assert container.block_groups[0].type == GroupType.LIST
        list_items = [
            block for block in container.blocks
            if block.sub_type == BlockSubType.LIST_ITEM
        ]
        assert len(list_items) == 2
        assert list_items[0].data == "One"
        assert list_items[0].format == DataFormat.MARKDOWN
        assert _child_block_indices(container.block_groups[0]) == [0, 1]

    def test_nested_list_stays_in_parent_list_item_markdown(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        html = (
            "<ul>"
            "<li>outer<ul><li>inner</li></ul></li>"
            "</ul>"
        )
        container = converter.convert(html)
        assert len(container.block_groups) == 1
        assert container.block_groups[0].type == GroupType.LIST
        assert _child_group_indices(container.block_groups[0]) == []

        list_items = [
            block for block in container.blocks
            if block.sub_type == BlockSubType.LIST_ITEM
        ]
        assert len(list_items) == 1
        assert list_items[0].format == DataFormat.MARKDOWN
        assert "outer" in list_items[0].data
        assert "inner" in list_items[0].data
        assert "* inner" in list_items[0].data

    def test_list_item_with_nested_list_keeps_full_markdown(
        self,
        converter: HtmlToBlocksConverter,
    ) -> None:
        html = (
            "<ol>"
            "<li>Alpha</li>"
            "<li>Beta — second item<ol><li>Beta-a</li>"
            "<li>Beta-b nested item<ol><li>deep</li></ol></li></ol></li>"
            "</ol>"
        )
        container = converter.convert(html)
        list_items = [
            block for block in container.blocks
            if block.sub_type == BlockSubType.LIST_ITEM
        ]
        assert len(container.block_groups) == 1
        assert len(list_items) == 2
        assert list_items[0].data == "Alpha"
        assert "Beta — second item" in list_items[1].data
        assert "Beta-a" in list_items[1].data
        assert "Beta-b nested item" in list_items[1].data
        assert "deep" in list_items[1].data

    def test_list_item_preserves_nested_block_markup_as_markdown(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        html = """
        <ul>
          <li>
            <p>list 2</p>
            <ol><li>list nested</li><li>2nd item</li></ol>
            <p>sdfklsjflskf</p>
            <blockquote><p>quote</p></blockquote>
            <pre><code class="language-python">yessss</code></pre>
          </li>
        </ul>
        """
        container = converter.convert(html)
        list_items = [
            block for block in container.blocks
            if block.sub_type == BlockSubType.LIST_ITEM
        ]
        assert len(list_items) == 1
        data = list_items[0].data
        assert "list 2" in data
        assert "list nested" in data and "2nd item" in data
        assert "sdfklsjflskf" in data
        assert "quote" in data
        assert "yessss" in data
        assert list_items[0].format == DataFormat.MARKDOWN

    def test_ordered_list_group(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<ol><li>First</li></ol>")
        assert container.block_groups[0].type == GroupType.ORDERED_LIST

    def test_orphan_text_node_emitted_as_paragraph(self, converter: HtmlToBlocksConverter) -> None:
        html = "<ul><li>Item 1</li> hello world <li>Item 2</li></ul>"
        container = converter.convert(html)
        list_items = [b for b in container.blocks if b.sub_type == BlockSubType.LIST_ITEM]
        paragraphs = [b for b in container.blocks if b.sub_type == BlockSubType.PARAGRAPH]
        assert len(list_items) == 2
        assert list_items[0].data == "Item 1"
        assert list_items[1].data == "Item 2"
        assert len(paragraphs) == 1
        assert paragraphs[0].data == "hello world"
        assert paragraphs[0].format == DataFormat.MARKDOWN

    def test_orphan_text_in_wrapper_div_emitted_as_paragraph(self, converter: HtmlToBlocksConverter) -> None:
        html = "<ul><div>orphan in div</div><li>Real</li></ul>"
        container = converter.convert(html)
        list_items = [b for b in container.blocks if b.sub_type == BlockSubType.LIST_ITEM]
        paragraphs = [b for b in container.blocks if b.sub_type == BlockSubType.PARAGRAPH]
        assert len(list_items) == 1
        assert list_items[0].data == "Real"
        assert len(paragraphs) == 1
        assert paragraphs[0].data == "orphan in div"

    def test_orphan_element_emitted_as_paragraph(self, converter: HtmlToBlocksConverter) -> None:
        html = '<ol><li>First</li><a href="https://x.com">link</a><li>Second</li></ol>'
        container = converter.convert(html)
        list_items = [b for b in container.blocks if b.sub_type == BlockSubType.LIST_ITEM]
        paragraphs = [b for b in container.blocks if b.sub_type == BlockSubType.PARAGRAPH]
        assert len(list_items) == 2
        assert list_items[0].data == "First"
        assert list_items[1].data == "Second"
        assert len(paragraphs) == 1
        assert "[link](https://x.com)" in paragraphs[0].data

    def test_whitespace_only_text_nodes_skipped(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<ul>  <li>A</li>  <li>B</li>  </ul>")
        assert len(container.blocks) == 2
        assert all(b.sub_type == BlockSubType.LIST_ITEM for b in container.blocks)

    def test_orphan_in_deeply_nested_container(self, converter: HtmlToBlocksConverter) -> None:
        html = (
            "<ul>"
            "<div><li>Item 1</li></div>"
            " hello world "
            "<section><div><li>Item 2</li></div></section>"
            "</ul>"
        )
        container = converter.convert(html)
        list_items = [b for b in container.blocks if b.sub_type == BlockSubType.LIST_ITEM]
        paragraphs = [b for b in container.blocks if b.sub_type == BlockSubType.PARAGRAPH]
        assert len(list_items) == 2
        assert list_items[0].data == "Item 1"
        assert list_items[1].data == "Item 2"
        assert len(paragraphs) == 1
        assert paragraphs[0].data == "hello world"

    def test_bare_text_sibling_of_nested_blocks_emitted_as_paragraph(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        """Gmail-style mixed content: text + nested div/img must keep the text."""
        html = (
            '<div dir="ltr">'
            "An airplane is a fixed-wing aircraft."
            "<div><br></div>"
            '<div><img src="cid:ii_mrtb2uoe0" alt="image.png"><br></div>'
            "</div>"
        )
        container = converter.convert(html)
        paragraphs = [b for b in container.blocks if b.sub_type == BlockSubType.PARAGRAPH]
        assert len(paragraphs) == 1
        assert paragraphs[0].data == "An airplane is a fixed-wing aircraft."
        assert paragraphs[0].format == DataFormat.TXT


class TestTables:
    def test_table_produces_rows(self, converter: HtmlToBlocksConverter) -> None:
        html = """
        <table>
          <tr><th>Name</th><th>Age</th></tr>
          <tr><td>Ada</td><td>36</td></tr>
        </table>
        """
        container = converter.convert(html)
        assert len(container.block_groups) == 1
        assert container.block_groups[0].type == GroupType.TABLE
        row_blocks = [
            block for block in container.blocks
            if block.type == BlockType.TABLE_ROW
        ]
        assert len(row_blocks) == 1
        assert row_blocks[0].data["cells"] == ["Ada", "36"]

    def test_table_cell_relative_link_resolved_with_base_url(
        self, converter: HtmlToBlocksConverter,
    ) -> None:
        container = converter.convert(
            '<table><tr><td><a href="/docs">guide</a></td></tr></table>',
            base_url="https://example.com",
        )
        row_blocks = [
            block for block in container.blocks
            if block.type == BlockType.TABLE_ROW
        ]
        assert len(row_blocks) == 1
        assert row_blocks[0].data["cells"] == ["[guide](https://example.com/docs)"]

    def test_table_colspan_rowspan_headers_and_rows(
        self,
        converter: HtmlToBlocksConverter,
    ) -> None:
        html = """
        <table>
          <thead>
            <tr>
              <th rowspan="2">Region</th>
              <th colspan="2">Q1</th>
              <th colspan="2">Q2</th>
              <th rowspan="2">YTD Total</th>
            </tr>
            <tr><th>Revenue</th><th>Units</th><th>Revenue</th><th>Units</th></tr>
          </thead>
          <tbody>
            <tr><td>North</td><td>$1.2M</td><td>4,200</td><td>$1.5M</td><td>5,100</td><td>$2.7M</td></tr>
          </tbody>
        </table>
        """
        container = converter.convert(html)
        table_group = container.block_groups[0]
        assert table_group.table_metadata is not None
        assert table_group.table_metadata.column_names == [
            "Region",
            "Q1\nRevenue | Units",
            "Q2\nRevenue | Units",
            "YTD Total",
        ]
        assert table_group.data == {
            "table_summary": "",
            "column_headers": table_group.table_metadata.column_names,
        }
        row_blocks = [
            block for block in container.blocks
            if block.type == BlockType.TABLE_ROW
        ]
        assert row_blocks[0].data["cells"] == [
            "North",
            "$1.2M | 4,200",
            "$1.5M | 5,100",
            "$2.7M",
        ]
        assert row_blocks[0].data["row_natural_language_text"] == (
            "Region: North, Q1\nRevenue | Units: $1.2M | 4,200, "
            "Q2\nRevenue | Units: $1.5M | 5,100, YTD Total: $2.7M"
        )

    def test_table_colspan_footer_row(self, converter: HtmlToBlocksConverter) -> None:
        html = """
        <table>
          <thead><tr><th>A</th><th>B</th><th>C</th></tr></thead>
          <tfoot><tr><td colspan="2">Summary</td><td>99</td></tr></tfoot>
        </table>
        """
        container = converter.convert(html)
        row_blocks = [
            block for block in container.blocks
            if block.type == BlockType.TABLE_ROW
        ]
        assert row_blocks[0].data["cells"] == ["Summary", "", "99"]

    def test_table_caption_prepended_to_markdown(self, converter: HtmlToBlocksConverter) -> None:
        html = """
        <table>
          <caption>Quarterly Revenue Report</caption>
          <tr><th>Quarter</th><th>Revenue</th></tr>
          <tr><td>Q1</td><td>$10,000</td></tr>
        </table>
        """
        container = converter.convert(html)
        table_group = container.block_groups[0]
        assert table_group.table_metadata is not None
        assert table_group.table_metadata.captions == ["Quarterly Revenue Report"]
        assert table_group.data == {
            "table_summary": "Quarterly Revenue Report",
            "column_headers": ["Quarter", "Revenue"],
        }
        row_blocks = [
            block for block in container.blocks
            if block.type == BlockType.TABLE_ROW
        ]
        assert row_blocks[0].data["cells"] == ["Q1", "$10,000"]


class TestDetails:
    def test_details_summary_becomes_heading(self, converter: HtmlToBlocksConverter) -> None:
        html = """
        <details>
          <summary>Installation Instructions</summary>
          <p>Run the following command:</p>
          <pre><code>pip install my-package</code></pre>
        </details>
        """
        container = converter.convert(html)
        headings = [
            block for block in container.blocks
            if block.sub_type == BlockSubType.HEADING
        ]
        assert len(headings) == 1
        assert headings[0].data == "Installation Instructions"
        paragraphs = [
            block for block in container.blocks
            if block.sub_type == BlockSubType.PARAGRAPH
        ]
        assert any("Run the following command" in block.data for block in paragraphs)


class TestImages:
    def test_image_block_without_caption_map_skipped(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        container = converter.convert(
            '<p>Text <img src="/pic.png" alt="diagram"></p>',
            base_url="https://example.com",
        )
        images = [block for block in container.blocks if block.type == BlockType.IMAGE]
        assert len(images) == 0
        text_blocks = [
            block for block in container.blocks
            if block.type == BlockType.TEXT and block.sub_type == BlockSubType.PARAGRAPH
        ]
        assert len(text_blocks) == 1
        assert text_blocks[0].data == "Text "
        assert text_blocks[0].parent_block_index is None

    def test_paragraph_with_text_and_image_splits_blocks(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        container = converter.convert(
            '<p>See <img alt="Image_1" src="https://example.com/a.png"> here</p>',
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

    def test_list_item_with_image_splits_blocks(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        container = converter.convert(
            '<ul><li>Item with <img alt="Image_1" src="x"></li></ul>',
            caption_map={"Image_1": "data:image/png;base64,LISTIMG"},
        )
        list_group = container.block_groups[0]
        list_containers = _split_container_blocks(container, BlockSubType.LIST_ITEM)
        image_blocks = _blocks_by_type(container, BlockType.IMAGE)

        assert len(list_containers) == 1
        _assert_empty_text_split_container(list_containers[0])
        fragments = _fragment_blocks(container, list_containers[0].index)
        assert fragments[0].data == "Item with "
        assert fragments[0].sub_type is None
        assert len(image_blocks) == 1
        assert image_blocks[0].parent_block_index == list_containers[0].index
        assert image_blocks[0].sub_type is None
        assert image_blocks[0].parent_index is None
        assert _child_block_indices(list_group) == [list_containers[0].index]

    def test_list_item_with_nested_table_image_splits_blocks(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        html = """
        <ul>
          <li>
            Connector Details
            <table>
              <tr><th>Connector</th><th>Status</th><th>Screenshot</th></tr>
              <tr>
                <td>Slack</td><td>Active</td>
                <td><img alt="Image_1" src="https://example.com/screenshot.png"></td>
              </tr>
              <tr><td>Gmail</td><td>Active</td><td>N/A</td></tr>
            </table>
          </li>
        </ul>
        """
        container = converter.convert(
            html,
            caption_map={"Image_1": "data:image/png;base64,LISTTABLEIMG"},
        )
        list_containers = _split_container_blocks(container, BlockSubType.LIST_ITEM)
        image_blocks = _blocks_by_type(container, BlockType.IMAGE)
        fragments = _fragment_blocks(container, list_containers[0].index)

        assert len(list_containers) == 1
        _assert_empty_text_split_container(list_containers[0])
        assert len(image_blocks) == 1
        assert image_blocks[0].format == DataFormat.BASE64
        assert image_blocks[0].parent_block_index == list_containers[0].index
        assert fragments[0].data is not None
        assert fragments[0].sub_type is None
        assert "Slack | Active |" in fragments[0].data
        assert image_blocks[0].sub_type is None
        assert image_blocks[0].image_metadata.captions == ["Image_1"]

    def test_blockquote_with_image_splits_blocks(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        container = converter.convert(
            '<blockquote><p>Quote <img alt="Image_1" src="x"></p></blockquote>',
            caption_map={"Image_1": "data:image/png;base64,QUOTEIMG"},
        )
        quote_group = container.block_groups[0]
        quote_containers = _split_container_blocks(container, BlockSubType.QUOTE)
        image_blocks = _blocks_by_type(container, BlockType.IMAGE)

        assert len(quote_containers) == 1
        _assert_empty_text_split_container(quote_containers[0])
        fragments = _fragment_blocks(container, quote_containers[0].index)
        assert fragments[0].data == "Quote "
        assert len(image_blocks) == 1
        assert image_blocks[0].parent_block_index == quote_containers[0].index
        assert _child_block_indices(quote_group) == [quote_containers[0].index]

    def test_table_row_with_image_splits_blocks(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        html = """
        <table>
          <tr><th>Name</th><th>Icon</th></tr>
          <tr><td>Connectors</td><td><img alt="Image_1" src="x"></td></tr>
        </table>
        """
        container = converter.convert(
            html,
            caption_map={"Image_1": "data:image/png;base64,TABLEIMG"},
        )
        table_group = container.block_groups[0]
        row_containers = _blocks_by_type(container, BlockType.TABLE_ROW)
        image_blocks = _blocks_by_type(container, BlockType.IMAGE)

        assert len(row_containers) == 1
        assert row_containers[0].data is not None
        assert _is_empty_split_container(row_containers[0])
        assert len(image_blocks) == 1
        assert image_blocks[0].parent_index is None
        assert image_blocks[0].parent_block_index == row_containers[0].index
        fragments = _fragment_blocks(container, row_containers[0].index)
        assert fragments[0].type == BlockType.TEXT
        assert fragments[0].data == "Name: Connectors"
        assert _child_block_indices(table_group) == [row_containers[0].index]

    def test_table_header_image_is_skipped(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        html = """
        <table>
          <tr><th>Label</th><th><img alt="Image_1" src="x"></th></tr>
          <tr><td>Value</td><td>cell</td></tr>
        </table>
        """
        container = converter.convert(
            html,
            caption_map={"Image_1": "data:image/png;base64,HEADERIMG"},
        )
        table_group = container.block_groups[0]
        image_blocks = _blocks_by_type(container, BlockType.IMAGE)

        assert table_group.data["column_headers"] == ["Label", ""]
        assert len(image_blocks) == 0

    def test_table_mid_cell_image_splits_row_text_fragments(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        html = """
        <table>
          <tr><th>col1</th><th>col2</th><th>col3</th></tr>
          <tr>
            <td>text1</td>
            <td>Text2 <img alt="Image_1" src="x"> Text3</td>
            <td>text4</td>
          </tr>
        </table>
        """
        container = converter.convert(
            html,
            caption_map={"Image_1": "data:image/png;base64,IMG"},
        )
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

    def test_nested_table_in_cell_renders_as_markdown(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        html = """
        <table>
          <tr><th>Data</th></tr>
          <tr>
            <td>
              <table>
                <tr><th>Inner</th></tr>
                <tr><td>value</td></tr>
              </table>
            </td>
          </tr>
        </table>
        """
        container = converter.convert(html)
        row = _blocks_by_type(container, BlockType.TABLE_ROW)[0]
        assert row.data is not None
        cells = row.data["cells"]
        assert "| Inner |" in cells[0]
        assert "| value |" in cells[0]
        assert not cells[0].strip().startswith("[")

    def test_nested_table_and_image_in_cell_splits_row(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        html = """
        <table>
          <tr><th>Content</th></tr>
          <tr>
            <td>
              Before
              <table><tr><td>nested</td></tr></table>
              <img alt="fig" src="data:image/png;base64,NESTED">
              After
            </td>
          </tr>
        </table>
        """
        container = converter.convert(html)
        row_container = _blocks_by_type(container, BlockType.TABLE_ROW)[0]
        fragments = _fragment_blocks(container, row_container.index)
        text_fragments = [b for b in fragments if b.type == BlockType.TEXT]
        image_blocks = [b for b in fragments if b.type == BlockType.IMAGE]

        assert row_container.data is not None
        assert _is_empty_split_container(row_container)
        assert len(text_fragments) == 2
        assert "Before" in text_fragments[0].data
        assert "| nested |" in text_fragments[0].data
        assert text_fragments[1].data == "Content: After"
        assert len(image_blocks) == 1
        assert image_blocks[0].data == {"uri": "data:image/png;base64,NESTED"}

    def test_image_block_inline_data_uri(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert(
            '<img src="data:image/png;base64,abc" alt="diagram">',
        )
        images = [block for block in container.blocks if block.type == BlockType.IMAGE]
        assert len(images) == 1
        assert images[0].data == {"uri": "data:image/png;base64,abc"}
        assert images[0].format == DataFormat.BASE64

    def test_caption_map(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert(
            '<img alt="fig1" src="x">',
            caption_map={"fig1": "data:image/png;base64,abc"},
        )
        block = container.blocks[0]
        assert block.data == {"uri": "data:image/png;base64,abc"}
        assert block.format == DataFormat.BASE64

    def test_duplicate_empty_alt_images_resolve_distinct_uris_on_markdown_path(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        html = (
            "<p><strong>See</strong> "
            '<img src="data:image/png;base64,AAAA" alt=""> '
            '<img src="data:image/png;base64,BBBB" alt=""></p>'
        )
        container = converter.convert(html)
        images = [block for block in container.blocks if block.type == BlockType.IMAGE]
        assert len(images) == 2
        assert images[0].data == {"uri": "data:image/png;base64,AAAA"}
        assert images[1].data == {"uri": "data:image/png;base64,BBBB"}

    def test_malformed_srcset_does_not_crash(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert('<img srcset="   " alt="broken">')
        images = [block for block in container.blocks if block.type == BlockType.IMAGE]
        assert len(images) == 0

    def test_standalone_image_between_paragraphs(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        html = (
            "<p>First paragraph.</p>"
            '<img src="data:image/png;base64,abc" alt="fig">'
            "<p>Second paragraph.</p>"
        )
        container = converter.convert(html)
        assert [block.type for block in container.blocks] == [
            BlockType.TEXT,
            BlockType.IMAGE,
            BlockType.TEXT,
        ]
        assert container.blocks[0].data == "First paragraph."
        assert container.blocks[1].data == {"uri": "data:image/png;base64,abc"}
        assert container.blocks[2].data == "Second paragraph."

    def test_image_split_table_row_container_carries_only_row_number(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        html = """
        <table>
          <tr><th>Name</th><th>Icon</th></tr>
          <tr><td>Connectors</td><td><img alt="Image_1" src="x"></td></tr>
        </table>
        """
        container = converter.convert(
            html,
            caption_map={"Image_1": "data:image/png;base64,TABLEIMG"},
        )
        row_container = _blocks_by_type(container, BlockType.TABLE_ROW)[0]
        assert row_container.data == {"row_number": 1}
        assert row_container.data is not None
        assert _is_empty_split_container(row_container)

    def test_deeply_nested_table_with_image_emits_image_block(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        html = """
        <table>
          <tr><th>Level 1</th><th>Content</th></tr>
          <tr>
            <td>Main</td>
            <td>
              <table>
                <tr><th>Level 2</th><th>Content</th></tr>
                <tr>
                  <td>Screenshot</td>
                  <td><img alt="Image_1" src="x"></td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
        """
        container = converter.convert(
            html,
            caption_map={"Image_1": "data:image/png;base64,NESTED"},
        )
        image_blocks = _blocks_by_type(container, BlockType.IMAGE)
        row_container = _blocks_by_type(container, BlockType.TABLE_ROW)[0]

        assert len(image_blocks) == 1
        assert image_blocks[0].data == {"uri": "data:image/png;base64,NESTED"}
        assert image_blocks[0].parent_block_index == row_container.index
        assert row_container.data == {"row_number": 1}


class TestBlockquote:
    def test_blockquote_group(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<blockquote><p>Quoted</p></blockquote>")
        assert container.block_groups[0].type == GroupType.TEXT_SECTION
        assert container.block_groups[0].sub_type == GroupSubType.QUOTE
        assert container.blocks[0].format == DataFormat.MARKDOWN
        assert container.blocks[0].data == "Quoted"
        assert _child_block_indices(container.block_groups[0]) == [0]


class TestDivider:
    def test_hr_produces_no_block(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<hr>")
        assert container.blocks == []
        assert container.block_groups == []


class TestComplexHtmlFixture:
    @pytest.mark.skipif(
        not _COMPLEX_HTML.is_file(),
        reason="complex-html.html fixture not present at repo root",
    )
    def test_caption_table_and_details(
        self,
        converter: HtmlToBlocksConverter,
    ) -> None:
        container = converter.convert(_COMPLEX_HTML.read_text(encoding="utf-8"))

        _assert_all_blocks_have_data(container)
        table_group = next(
            group for group in container.block_groups
            if group.type == GroupType.TABLE
        )
        assert table_group.table_metadata is not None
        assert table_group.table_metadata.captions == ["Quarterly Revenue Report"]
        assert table_group.data == {
            "table_summary": "Quarterly Revenue Report",
            "column_headers": ["Quarter", "Revenue"],
        }
        row_blocks = [
            block for block in container.blocks
            if block.type == BlockType.TABLE_ROW
        ]
        assert row_blocks[0].data["cells"] == ["Q1", "$10,000"]

        headings = [
            block for block in container.blocks
            if block.sub_type == BlockSubType.HEADING
        ]
        assert any(block.data == "Installation Instructions" for block in headings)

        code_blocks = [
            block for block in container.blocks
            if block.sub_type == BlockSubType.CODE
        ]
        assert any("pip install my-package" in block.data for block in code_blocks)


class TestBlockDataNeverNone:
    """Emitted blocks must always carry data; empty text containers use ""."""

    @pytest.mark.parametrize(
        ("html", "caption_map"),
        [
            (
                '<ul><li>item <img alt="Image_1" src="x"> tail</li></ul>',
                {"Image_1": "data:image/png;base64,IMG"},
            ),
            (
                '<blockquote><p>quote <img alt="Image_1" src="x"></p></blockquote>',
                {"Image_1": "data:image/png;base64,IMG"},
            ),
            (
                """
                <table>
                  <tr><th>A</th><th>B</th></tr>
                  <tr><td>x</td><td><img alt="Image_1" src="x"></td></tr>
                </table>
                """,
                {"Image_1": "data:image/png;base64,IMG"},
            ),
            (
                '<ul><li><img alt="Image_1" src="https://example.com/a.png"></li></ul>',
                {},
            ),
        ],
    )
    def test_image_split_outputs_never_have_none_data(
        self,
        converter: HtmlToBlocksConverter,
        html: str,
        caption_map: dict[str, str],
    ) -> None:
        container = converter.convert(html, caption_map=caption_map)
        _assert_all_blocks_have_data(container)
        for block in container.blocks:
            if _is_empty_split_container(block) and block.type == BlockType.TEXT:
                assert block.data == ""

    def test_unsplit_document_never_has_none_data(
        self, converter: HtmlToBlocksConverter
    ) -> None:
        html = (
            "<h1>Report</h1>"
            "<p>Plain paragraph.</p>"
            "<table><tr><th>Metric</th><th>Value</th></tr>"
            "<tr><td>Users</td><td>10</td></tr></table>"
            "<ul><li>action item</li></ul>"
        )
        container = converter.convert(html)
        _assert_all_blocks_have_data(container)


class TestBlockContainerValidation:
    """HTML table output must pass BlockContainerValidator checks."""

    @pytest.mark.parametrize(
        "html",
        [
            "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>",
            """
            <table>
              <caption>Revenue</caption>
              <tr><th>Quarter</th><th>Amount</th></tr>
              <tr><td>Q1</td><td>$10</td></tr>
            </table>
            """,
            """
            <table>
              <tr><th>Label</th><th><img alt="Image_1" src="x"></th></tr>
              <tr><td>Value</td><td>cell</td></tr>
            </table>
            """,
            """
            <table>
              <tr><th>col1</th><th>col2</th></tr>
              <tr><td>text</td><td>Text2 <img alt="Image_1" src="x"> Text3</td></tr>
            </table>
            """,
        ],
    )
    def test_table_html_passes_block_container_validation(
        self,
        converter: HtmlToBlocksConverter,
        html: str,
    ) -> None:
        container = converter.convert(
            html,
            caption_map={"Image_1": "data:image/png;base64,IMG"},
        )
        table_groups = [
            group for group in container.block_groups
            if group.type == GroupType.TABLE
        ]
        for group in table_groups:
            assert group.data is not None
            assert isinstance(group.data.get("column_headers"), list)
            assert group.table_metadata is not None
            assert group.table_metadata.num_of_cells is not None

        BlockContainerValidator().validate(container)


class TestSelectolaxHtmlParser:
    @pytest.mark.asyncio
    async def test_parse_to_blocks_delegates_to_converter(
        self,
        parser: SelectolaxHtmlParser,
    ) -> None:
        container = await parser.parse_to_blocks("<h1>Title</h1>")
        _assert_all_blocks_have_data(container)
        assert container.blocks[0].sub_type == BlockSubType.HEADING
        assert container.blocks[0].data == "Title"

    @pytest.mark.asyncio
    async def test_parse_to_blocks_passes_base_url_and_caption_map(
        self,
        parser: SelectolaxHtmlParser,
    ) -> None:
        container = await parser.parse_to_blocks(
            '<img alt="fig1" src="pic.png">',
            base_url="https://example.com",
            caption_map={"fig1": "data:image/png;base64,abc"},
        )
        _assert_all_blocks_have_data(container)
        assert container.blocks[0].data == {"uri": "data:image/png;base64,abc"}

    def test_replace_relative_image_urls_delegates_to_html_parser(
        self,
        parser: SelectolaxHtmlParser,
    ) -> None:
        html = (
            '<html><head><base href="https://example.com/"></head>'
            '<body><img src="/pic.png"></body></html>'
        )
        result = parser.replace_relative_image_urls(html)
        assert 'src="https://example.com/pic.png"' in result

    @pytest.mark.asyncio
    async def test_parse_delegates_to_parse_to_blocks(
        self,
        parser: SelectolaxHtmlParser,
    ) -> None:
        expected = BlocksContainer(blocks=[], block_groups=[])
        with patch.object(parser, "clean_html", side_effect=lambda x: x), \
             patch.object(parser, "replace_relative_image_urls", side_effect=lambda x: x), \
             patch.object(parser, "extract_and_replace_images", side_effect=lambda x: (x, [])), \
             patch.object(
                 parser, "parse_to_blocks", new_callable=AsyncMock, return_value=expected
             ) as mock_parse:
            result = await parser.parse(b"<h1>Ready</h1>", "test.html")

        mock_parse.assert_awaited_once_with(
            "<h1>Ready</h1>",
            caption_map=None,
        )
        assert result.block_container is expected
        assert result.metadata == {"record_name": "test.html"}

    def test_clean_before_extract_skips_header_images(
        self,
        parser: SelectolaxHtmlParser,
    ) -> None:
        html = (
            '<header><img src="/logo.png" alt="logo"></header>'
            '<main><img src="/doc.png" alt="doc"></main>'
        )
        cleaned = parser.clean_html(html)
        _, images = parser.extract_and_replace_images(cleaned)
        assert len(images) == 1
        assert images[0]["url"] == "/doc.png"


class TestDomWalkDepth:
    def test_deeply_nested_containers_do_not_recursion_error(
        self, converter: HtmlToBlocksConverter,
    ) -> None:
        depth = _MAX_DOM_PROBE_DEPTH + 10
        html = "<div>" * depth + "deep content" + "</div>" * depth
        container = converter.convert(html)
        paragraphs = [
            block for block in container.blocks
            if block.sub_type == BlockSubType.PARAGRAPH
        ]
        assert paragraphs
        assert any("deep content" in (block.data or "") for block in paragraphs)


class TestSelectolaxHtmlParserCleanHtml:
    def test_removes_script_tags(self, parser: SelectolaxHtmlParser) -> None:
        html = "<html><body><script>alert('hi')</script><p>Content</p></body></html>"
        result = parser.clean_html(html)
        assert "<script>" not in result
        assert "<p>Content</p>" in result

    def test_removes_style_tags(self, parser: SelectolaxHtmlParser) -> None:
        html = "<html><head><style>.foo{color:red}</style></head><body><p>Content</p></body></html>"
        result = parser.clean_html(html)
        assert "<style>" not in result
        assert "<p>Content</p>" in result

    def test_removes_nav_footer_header(self, parser: SelectolaxHtmlParser) -> None:
        html = "<html><body><nav>Nav</nav><header>Header</header><p>Content</p><footer>Footer</footer></body></html>"
        result = parser.clean_html(html)
        assert "<nav>" not in result
        assert "<header>" not in result
        assert "<footer>" not in result
        assert "<p>Content</p>" in result

    def test_removes_noscript_iframe(self, parser: SelectolaxHtmlParser) -> None:
        html = "<html><body><noscript>No JS</noscript><iframe src='x'></iframe><p>Content</p></body></html>"
        result = parser.clean_html(html)
        assert "<noscript>" not in result
        assert "<iframe>" not in result
        assert "<p>Content</p>" in result

    def test_returns_original_on_parse_error(self, parser: SelectolaxHtmlParser) -> None:
        with patch.object(parser, "_logger") as mock_logger:
            with patch(
                "app.modules.parsers.html_parser.selectolax_html_parser.BeautifulSoup",
                side_effect=Exception("parse error"),
            ):
                result = parser.clean_html("<p>Content</p>")
        assert result == "<p>Content</p>"
        mock_logger.warning.assert_called_once()


class TestSelectolaxExtractAndReplaceImages:
    def test_srcset_used_when_src_empty(self, parser: SelectolaxHtmlParser) -> None:
        html = '<img srcset="https://cdn.example.com/p.png 1x" alt="a">'
        _, images = parser.extract_and_replace_images(html)
        assert len(images) == 1
        assert images[0]["new_alt_text"] == "Image_1"

    def test_skips_data_uri(self, parser: SelectolaxHtmlParser) -> None:
        _, images = parser.extract_and_replace_images(
            '<img src="data:image/png;base64,xx" alt="inline">'
        )
        assert images == []

    def test_skips_img_without_usable_src(self, parser: SelectolaxHtmlParser) -> None:
        _, images = parser.extract_and_replace_images('<img srcset="   " alt="x">')
        assert images == []

    def test_multiple_images_numbered(self, parser: SelectolaxHtmlParser) -> None:
        html = (
            '<img src="https://a.com/1.png" alt="one">'
            '<img src="https://a.com/2.png" alt="two">'
        )
        _, images = parser.extract_and_replace_images(html)
        assert [img["new_alt_text"] for img in images] == ["Image_1", "Image_2"]

    def test_srcset_with_empty_first_entry(self, parser: SelectolaxHtmlParser) -> None:
        html = '<img src="" srcset=", https://example.com/b.png 2x" alt="x">'
        _, images = parser.extract_and_replace_images(html)
        assert images == []

    def test_skips_img_without_src_or_srcset(self, parser: SelectolaxHtmlParser) -> None:
        _, images = parser.extract_and_replace_images('<img alt="no-src-no-srcset">')
        assert images == []


class TestSelectolaxHtmlParserParseFlow:
    @pytest.mark.asyncio
    async def test_parse_with_images_builds_caption_map(
        self, parser: SelectolaxHtmlParser
    ) -> None:
        images = [
            {
                "url": "https://example.com/i.png",
                "alt_text": "",
                "new_alt_text": "Image_1",
            }
        ]
        expected = BlocksContainer(blocks=[], block_groups=[])
        with patch.object(parser, "_prepare_html", return_value=("<p>x</p>", images)), \
             patch(
                 "app.modules.parsers.html_parser.selectolax_html_parser.ImageParser.urls_to_base64",
                 new_callable=AsyncMock,
                 return_value=["data:image/png;base64,ZZ"],
             ), \
             patch.object(
                 parser, "parse_to_blocks", new_callable=AsyncMock, return_value=expected
             ) as mock_blocks:
            result = await parser.parse(b"<img>", "file.html")

        mock_blocks.assert_awaited_once_with(
            "<p>x</p>",
            caption_map={"Image_1": "data:image/png;base64,ZZ"},
        )
        assert result.block_container is expected

    @pytest.mark.asyncio
    async def test_parse_accepts_str_content(self, parser: SelectolaxHtmlParser) -> None:
        expected = BlocksContainer(blocks=[], block_groups=[])
        with patch.object(parser, "_prepare_html", return_value=("<p>ok</p>", [])), \
             patch.object(
                 parser, "parse_to_blocks", new_callable=AsyncMock, return_value=expected
             ):
            result = await parser.parse("<p>ok</p>", "s.html")
        assert result.metadata == {"record_name": "s.html"}

    @pytest.mark.asyncio
    async def test_parse_skips_none_base64_urls(
        self, parser: SelectolaxHtmlParser
    ) -> None:
        images = [
            {"url": "https://a.com/1.png", "alt_text": "", "new_alt_text": "Image_1"},
            {"url": "https://a.com/2.png", "alt_text": "", "new_alt_text": "Image_2"},
        ]
        expected = BlocksContainer(blocks=[], block_groups=[])
        with patch.object(parser, "_prepare_html", return_value=("<p>x</p>", images)), \
             patch(
                 "app.modules.parsers.html_parser.selectolax_html_parser.ImageParser.urls_to_base64",
                 new_callable=AsyncMock,
                 return_value=[None, "data:image/png;base64,OK"],
             ), \
             patch.object(
                 parser, "parse_to_blocks", new_callable=AsyncMock, return_value=expected
             ) as mock_blocks:
            await parser.parse(b"<img>", "doc.html")

        mock_blocks.assert_awaited_once_with(
            "<p>x</p>",
            caption_map={"Image_2": "data:image/png;base64,OK"},
        )
