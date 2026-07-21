"""Unit tests for internal helpers in html_parser/html_to_blocks.py."""

from __future__ import annotations

import pytest
from selectolax.lexbor import LexborHTMLParser, LexborNode

from app.models.blocks import BlockSubType, BlockType, DataFormat, GroupType
from app.modules.parsers.html_parser.html_to_blocks import (
    HtmlTableNormalizer,
    HtmlToBlocksConverter,
    NormalizedCell,
    NormalizedTable,
    _body_output_row_span,
    _collapse_body_rows,
    _collapse_header_row,
    _collapse_single_column_cell,
    _column_groups,
    _escape_markdown_cell,
    _html_to_markdown,
    _is_hidden,
    _language_from_node,
    _pad_grid,
    _render_table_markdown,
    _resolve_relative_links_on_tree,
    _span_int,
    normalize_html_table,
    normalized_table_to_markdown,
)


def _parse(html: str) -> LexborNode:
    return LexborHTMLParser(html).root


class TestHiddenAndLanguage:
    def test_is_hidden_sr_only(self) -> None:
        node = _parse('<span class="sr-only">skip</span>').css_first("span")
        assert _is_hidden(node) is True

    def test_is_hidden_aria_hidden(self) -> None:
        node = _parse('<div aria-hidden="true">skip</div>').css_first("div")
        assert _is_hidden(node) is True

    def test_language_from_node_none(self) -> None:
        assert _language_from_node(None) is None

    def test_language_from_node_match(self) -> None:
        node = _parse('<code class="language-python">x</code>').css_first("code")
        assert _language_from_node(node) == "python"


class TestHtmlToMarkdownAndLinks:
    def test_html_to_markdown_empty(self) -> None:
        assert _html_to_markdown("") == ""

    def test_resolve_relative_links_skips_absolute_and_mailto(self) -> None:
        html = (
            '<a href="https://x.com">a</a>'
            '<a href="mailto:a@b.com">b</a>'
            '<a href="#section">c</a>'
            '<a href="/rel">d</a>'
        )
        root = _parse(html)
        _resolve_relative_links_on_tree(root, "https://example.com")
        hrefs = [a.attributes.get("href") for a in root.css("a")]
        assert hrefs[0] == "https://x.com"
        assert hrefs[1] == "mailto:a@b.com"
        assert hrefs[2] == "#section"
        assert hrefs[3] == "https://example.com/rel"

    def test_resolve_relative_links_no_base_url(self) -> None:
        root = _parse('<a href="/x">x</a>')
        _resolve_relative_links_on_tree(root, "")
        assert root.css_first("a").attributes["href"] == "/x"


class TestTableGridHelpers:
    def test_span_int_invalid(self) -> None:
        assert _span_int({"rowspan": "bad"}, "rowspan") == 1
        assert _span_int({}, "colspan") == 1

    def test_pad_grid_empty(self) -> None:
        assert _pad_grid([]) == []

    def test_column_groups_without_header(self) -> None:
        groups = _column_groups([], width=3)
        assert groups == [(0, 1), (1, 2), (2, 3)]

    def test_collapse_header_row_deduplicates_levels(self) -> None:
        grid = [[
            NormalizedCell(text="H", colspan=1, is_origin=True),
        ]]
        groups = [(0, 1)]
        out = _collapse_header_row(grid, groups)
        assert out == ["H"]

    def test_collapse_body_rows_empty(self) -> None:
        assert _collapse_body_rows([], [(0, 1)]) == []

    def test_body_output_row_span(self) -> None:
        row = [NormalizedCell(text="x", rowspan=3, is_origin=True)]
        assert _body_output_row_span(row) == 3
        assert _body_output_row_span([]) == 1

    def test_collapse_single_column_cell_value_column(self) -> None:
        rows = [
            [NormalizedCell(text="L", is_origin=True), NormalizedCell(text="v1", is_origin=True)],
            [NormalizedCell(text="", is_origin=False), NormalizedCell(text="v2", is_origin=True)],
        ]
        assert _collapse_single_column_cell(rows, 1) == "v1\nv2"

    def test_collapse_single_column_cell_rowspan_wins(self) -> None:
        rows = [
            [NormalizedCell(text="Span", rowspan=2, is_origin=True)],
            [NormalizedCell(text="", is_origin=False)],
        ]
        assert _collapse_single_column_cell(rows, 0) == "Span"

    def test_escape_markdown_cell(self) -> None:
        assert "|" in _escape_markdown_cell("a | b")
        assert "<br>" in _escape_markdown_cell("line\nbreak")

    def test_render_table_markdown_synthetic_headers(self) -> None:
        table = NormalizedTable(
            column_headers=[],
            body_rows=[["A", "B"]],
            num_cols=2,
            num_body_rows=1,
            has_header=False,
        )
        md = _render_table_markdown(table)
        assert "Column 1" in md
        assert "A" in md

    def test_render_table_markdown_empty(self) -> None:
        table = NormalizedTable([], [], 0, 0, False)
        assert _render_table_markdown(table) == ""

    def test_normalized_table_to_markdown_with_caption(self) -> None:
        table = NormalizedTable(
            column_headers=["Q"],
            body_rows=[["1"]],
            num_cols=1,
            num_body_rows=1,
            has_header=True,
        )
        md = normalized_table_to_markdown(table, caption="Cap")
        assert md.startswith("Cap")
        assert "Q" in md

    def test_normalized_table_to_markdown_empty_table_caption_only(self) -> None:
        table = NormalizedTable([], [], 0, 0, False)
        assert normalized_table_to_markdown(table, caption="Only") == "Only"


class TestHtmlTableNormalizer:
    def test_nested_table_in_cell(self) -> None:
        html = """
        <table>
          <tr><td>
            outer
            <table><tr><th>Inner</th></tr><tr><td>val</td></tr></table>
          </td></tr>
        </table>
        """
        node = _parse(html).css_first("table")
        normalized = normalize_html_table(node)
        assert normalized.body_rows
        cell = normalized.body_rows[0][0]
        assert "outer" in cell
        assert "| Inner |" in cell and "| val |" in cell

    def test_rowspan_colspan_expansion(self) -> None:
        html = """
        <table>
          <tr><td rowspan="2">R</td><td>A</td></tr>
          <tr><td>B</td></tr>
        </table>
        """
        node = _parse(html).css_first("table")
        normalized = normalize_html_table(node)
        assert len(normalized.body_rows) >= 1


class TestHtmlToBlocksConverterEdgeCases:
    @pytest.fixture
    def converter(self) -> HtmlToBlocksConverter:
        return HtmlToBlocksConverter()

    def test_empty_html_returns_empty_container(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("")
        assert container.blocks == []
        assert container.block_groups == []

    def test_sr_only_heading_skipped(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert('<h1 class="sr-only">Hidden</h1><p>Visible</p>')
        headings = [b for b in container.blocks if b.sub_type == BlockSubType.HEADING]
        assert headings == []
        assert any("Visible" in (b.data or "") for b in container.blocks)

    def test_heading_merged_into_following_paragraph(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<h2>Section</h2><p>Body text</p>")
        paragraphs = [b for b in container.blocks if b.sub_type == BlockSubType.PARAGRAPH]
        assert len(paragraphs) == 1
        assert paragraphs[0].format == DataFormat.MARKDOWN
        assert paragraphs[0].data.startswith("## Section")
        assert "Body text" in paragraphs[0].data

    def test_heading_merge_empty_heading_text(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<h2></h2><p>Only body</p>")
        paragraphs = [b for b in container.blocks if b.sub_type == BlockSubType.PARAGRAPH]
        assert len(paragraphs) == 1
        assert paragraphs[0].data == "Only body"

    def test_pre_without_code_emits_code_group(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<pre>bare code</pre>")
        code_blocks = [b for b in container.blocks if b.sub_type == BlockSubType.CODE]
        assert len(code_blocks) == 1
        assert code_blocks[0].data == "bare code"

    def test_standalone_code_tag(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert('<code class="language-js">let x=1</code>')
        assert container.block_groups[0].type == GroupType.CODE
        assert container.blocks[0].data == "let x=1"

    def test_summary_tag_as_heading(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<summary>Fold</summary><p>Content</p>")
        headings = [b for b in container.blocks if b.sub_type == BlockSubType.HEADING]
        assert any(h.data == "Fold" for h in headings)

    def test_shallow_div_becomes_paragraph(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<div>Shallow text only</div>")
        paragraphs = [b for b in container.blocks if b.sub_type == BlockSubType.PARAGRAPH]
        assert any("Shallow text only" in (p.data or "") for p in paragraphs)

    def test_empty_blockquote_no_blocks(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<blockquote></blockquote>")
        assert container.blocks == []

    def test_empty_list_item_skipped(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<ul><li></li><li>Real</li></ul>")
        items = [b for b in container.blocks if b.sub_type == BlockSubType.LIST_ITEM]
        assert len(items) == 1
        assert items[0].data == "Real"

    def test_data_uri_from_srcset(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert(
            '<img srcset="data:image/png;base64,abc 2x" alt="pic">',
        )
        images = [b for b in container.blocks if b.type == BlockType.IMAGE]
        assert len(images) == 1
        assert images[0].data["uri"].startswith("data:image")

    def test_caption_map_non_data_uri_ignored(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert(
            '<img alt="fig" src="http://x.com/p.png">',
            caption_map={"fig": "https://not-data.example/x.png"},
        )
        assert not any(b.type == BlockType.IMAGE for b in container.blocks)

    def test_details_summary_and_body(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert(
            "<details><summary>Title</summary><p>Inside</p></details>",
        )
        headings = [b for b in container.blocks if b.sub_type == BlockSubType.HEADING]
        assert any(h.data == "Title" for h in headings)
        assert any("Inside" in (b.data or "") for b in container.blocks)

    def test_depth_cap_emits_paragraph(self, converter: HtmlToBlocksConverter) -> None:
        from app.modules.parsers.html_parser import html_to_blocks as mod

        depth = mod._MAX_DOM_PROBE_DEPTH + 5
        html = "<div>" * depth + "deep content" + "</div>" * depth
        container = converter.convert(html)
        paragraphs = [
            b for b in container.blocks if b.sub_type == BlockSubType.PARAGRAPH
        ]
        assert any("deep content" in (b.data or "") for b in paragraphs)

    def test_inline_empty_element_skipped(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<strong></strong><p>After</p>")
        assert any("After" in (b.data or "") for b in container.blocks)

    def test_paragraph_with_br_plain_text(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<p>line1<br>line2</p>")
        block = container.blocks[0]
        assert block.format == DataFormat.MARKDOWN
        assert "line1" in block.data and "line2" in block.data

    def test_heading_merge_skips_script_sibling(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert("<h2>Title</h2><script>x</script><p>Body</p>")
        paragraphs = [b for b in container.blocks if b.sub_type == BlockSubType.PARAGRAPH]
        merged = [b for b in paragraphs if b.data and "Title" in b.data and "Body" in b.data]
        assert merged

    def test_heading_merge_with_heading_image(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert(
            '<h2>Title<img src="data:image/png;base64,abc" alt=""></h2><p>Body</p>',
        )
        images = [b for b in container.blocks if b.type == BlockType.IMAGE]
        assert len(images) == 1
        paragraphs = [b for b in container.blocks if b.sub_type == BlockSubType.PARAGRAPH]
        assert any("Body" in (p.data or "") for p in paragraphs)

    def test_pre_with_image_and_text(self, converter: HtmlToBlocksConverter) -> None:
        container = converter.convert(
            '<pre><img src="data:image/png;base64,x">print(1)</pre>',
        )
        images = [b for b in container.blocks if b.type == BlockType.IMAGE]
        code_blocks = [b for b in container.blocks if b.sub_type == BlockSubType.CODE]
        assert images
        assert code_blocks

    def test_is_hidden_class_as_list(self) -> None:
        class MockNode:
            attributes = {"class": ["sr-only", "other"]}

        assert _is_hidden(MockNode()) is True

    def test_table_bare_rows_without_thead(self) -> None:
        html = """
        <table>
          <tr><th>H</th></tr>
          <tr><td>v</td></tr>
        </table>
        """
        node = _parse(html).css_first("table")
        normalized = normalize_html_table(node)
        assert normalized.has_header
        assert normalized.body_rows == [["v"]]
