"""Unit tests for Selectolax HTML parsing (converter + parser wrapper)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.models.blocks import (
    BlockSubType,
    BlockType,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.modules.parsers.html_parser.html_to_blocks import HtmlToBlocksConverter
from app.modules.parsers.html_parser.selectolax_html_parser import SelectolaxHtmlParser

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


class TestHeadings:
    def test_heading_produces_text_block(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<h1>Title</h1><h2>Subtitle</h2>")
        headings = [
            block for block in container.blocks
            if block.sub_type == BlockSubType.HEADING
        ]
        assert len(headings) == 2
        assert headings[0].data == "Title"
        assert headings[0].format == DataFormat.TXT
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
        assert block.format == DataFormat.TXT


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
        assert len(container.block_groups) == 1
        assert container.block_groups[0].type == GroupType.CODE
        assert len(code_blocks) == 3
        assert heading_blocks == []
        assert code_blocks[0].data == "A"
        assert code_blocks[1].data == "B"
        assert code_blocks[2].data == "Hello"
        assert _child_block_indices(container.block_groups[0]) == [0, 1, 2]


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
            "Q1 / Revenue",
            "Q1 / Units",
            "Q2 / Revenue",
            "Q2 / Units",
            "YTD Total",
        ]
        assert table_group.data is None
        row_blocks = [
            block for block in container.blocks
            if block.type == BlockType.TABLE_ROW
        ]
        assert row_blocks[0].data["cells"] == [
            "North",
            "$1.2M",
            "4,200",
            "$1.5M",
            "5,100",
            "$2.7M",
        ]
        assert row_blocks[0].data["row_natural_language_text"] == (
            "Region: North, Q1 / Revenue: $1.2M, Q1 / Units: 4,200, "
            "Q2 / Revenue: $1.5M, Q2 / Units: 5,100, YTD Total: $2.7M"
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
        assert table_group.data is None
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
    def test_image_block(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert(
            '<p>Text <img src="/pic.png" alt="diagram"></p>',
            base_url="https://example.com",
        )
        images = [block for block in container.blocks if block.type == BlockType.IMAGE]
        assert len(images) == 1
        assert images[0].data == {"url": "https://example.com/pic.png"}

    def test_caption_map(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert(
            '<img alt="fig1" src="x">',
            caption_map={"fig1": "data:image/png;base64,abc"},
        )
        block = container.blocks[0]
        assert block.data == {"uri": "data:image/png;base64,abc"}
        assert block.format == DataFormat.BASE64


class TestBlockquote:
    def test_blockquote_group(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<blockquote><p>Quoted</p></blockquote>")
        assert container.block_groups[0].type == GroupType.TEXT_SECTION
        assert container.block_groups[0].sub_type == GroupSubType.QUOTE
        assert container.blocks[0].format == DataFormat.MARKDOWN
        assert container.blocks[0].data == "Quoted"
        assert _child_block_indices(container.block_groups[0]) == [0]


class TestDivider:
    def test_hr_produces_divider(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<hr>")
        assert container.blocks[0].sub_type == BlockSubType.DIVIDER


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

        table_group = next(
            group for group in container.block_groups
            if group.type == GroupType.TABLE
        )
        assert table_group.table_metadata is not None
        assert table_group.table_metadata.captions == ["Quarterly Revenue Report"]
        assert table_group.data is None
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


class TestSelectolaxHtmlParser:
    def test_parse_to_blocks_delegates_to_converter(
        self,
        parser: SelectolaxHtmlParser,
    ) -> None:
        container = parser.parse_to_blocks("<h1>Title</h1>")
        assert container.blocks[0].sub_type == BlockSubType.HEADING
        assert container.blocks[0].data == "Title"

    def test_parse_to_blocks_passes_base_url_and_caption_map(
        self,
        parser: SelectolaxHtmlParser,
    ) -> None:
        container = parser.parse_to_blocks(
            '<img alt="fig1" src="pic.png">',
            base_url="https://example.com",
            caption_map={"fig1": "data:image/png;base64,abc"},
        )
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
    async def test_parse_cleans_absolutizes_and_delegates(
        self,
        parser: SelectolaxHtmlParser,
    ) -> None:
        with patch.object(parser, "clean_html", return_value="<h1>Cleaned</h1>") as mock_clean, \
             patch.object(parser, "replace_relative_image_urls", return_value="<h1>AbsURLs</h1>") as mock_abs, \
             patch.object(parser, "parse_to_blocks", return_value=MagicMock()) as mock_parse:
            await parser.parse("<script>x</script><h1>Title</h1>", caption_map={"a": "b"}, base_url="https://x.com")

            mock_clean.assert_called_once_with("<script>x</script><h1>Title</h1>")
            mock_abs.assert_called_once_with("<h1>Cleaned</h1>")
            mock_parse.assert_called_once_with(
                "<h1>AbsURLs</h1>",
                base_url="https://x.com",
                caption_map={"a": "b"},
            )


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
