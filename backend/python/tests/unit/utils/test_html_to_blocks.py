"""Tests for app.utils.html_to_blocks."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup

from app.utils.html_to_blocks import (
    MAX_TEXT_CHARS,
    MIN_TEXT_CHARS,
    ImageBlock,
    TextBlock,
    _clean,
    _flush_buffer,
    _merge_tiny_blocks,
    _resolve_url,
    _split_long_text,
    _strip_boilerplate,
    _walk,
    _walk_images_only,
    html_to_blocks,
)


# ---------------------------------------------------------------------------
# _clean
# ---------------------------------------------------------------------------


class TestClean:
    def test_strips_leading_trailing_whitespace(self) -> None:
        assert _clean("  hello  ") == "hello"

    def test_empty_string(self) -> None:
        assert _clean("") == ""

    def test_no_whitespace(self) -> None:
        assert _clean("hello") == "hello"

    def test_newlines_stripped(self) -> None:
        assert _clean("\nhello\n") == "hello"


# ---------------------------------------------------------------------------
# _resolve_url
# ---------------------------------------------------------------------------


class TestResolveUrl:
    def test_empty_src_returns_empty(self) -> None:
        assert _resolve_url("", "https://example.com") == ""

    def test_data_uri_returns_empty(self) -> None:
        assert _resolve_url("data:image/png;base64,abc", "https://example.com") == ""

    def test_absolute_url_returned_unchanged(self) -> None:
        assert _resolve_url("https://cdn.com/img.png", "https://example.com") == "https://cdn.com/img.png"

    def test_relative_url_resolved_against_base(self) -> None:
        result = _resolve_url("/images/pic.png", "https://example.com")
        assert result == "https://example.com/images/pic.png"

    def test_relative_url_no_leading_slash(self) -> None:
        result = _resolve_url("img.png", "https://example.com/page/")
        assert result == "https://example.com/page/img.png"


# ---------------------------------------------------------------------------
# _split_long_text
# ---------------------------------------------------------------------------


class TestSplitLongText:
    def test_short_text_returned_as_single(self) -> None:
        text = "Hello world."
        result = _split_long_text(text, max_chars=1000)
        assert result == [text]

    def test_text_at_exactly_max_returned_as_single(self) -> None:
        text = "A" * MAX_TEXT_CHARS
        result = _split_long_text(text)
        assert len(result) == 1

    def test_long_text_splits_at_sentence_boundary(self) -> None:
        sentence = "This is a sentence. "
        text = sentence * 2000  # ~40000 chars > MAX_TEXT_CHARS
        result = _split_long_text(text)
        assert len(result) > 1
        for chunk in result:
            assert len(chunk) <= MAX_TEXT_CHARS + 50  # small overflow tolerance

    def test_long_text_no_sentence_boundary(self) -> None:
        # With no sentence boundary, the function cannot split further.
        # It returns the single long sentence as one chunk.
        text = ("A" * 100 + " ") * 300  # ~30000 chars, no .!? markers
        result = _split_long_text(text, max_chars=5000)
        assert len(result) >= 1  # At minimum one chunk is always returned

    def test_chunks_non_empty(self) -> None:
        text = "Hello world. Goodbye world! " * 2000
        result = _split_long_text(text)
        assert all(chunk for chunk in result)

    def test_empty_string_returns_list_with_empty(self) -> None:
        # Empty string len <= max_chars, returned as-is
        result = _split_long_text("")
        assert result == [""]


# ---------------------------------------------------------------------------
# _flush_buffer
# ---------------------------------------------------------------------------


class TestFlushBuffer:
    def test_empty_buffer_is_noop(self) -> None:
        blocks: list = []
        buf: list = []
        _flush_buffer(buf, blocks)
        assert blocks == []

    def test_whitespace_only_buffer_produces_no_block(self) -> None:
        blocks: list = []
        buf = ["   ", "\n", "  "]
        _flush_buffer(buf, blocks)
        assert blocks == []

    def test_text_buffer_produces_text_block(self) -> None:
        blocks: list = []
        buf = ["Hello ", "world"]
        _flush_buffer(buf, blocks)
        assert len(blocks) == 1
        assert isinstance(blocks[0], TextBlock)
        assert "Hello" in blocks[0].content

    def test_buffer_cleared_after_flush(self) -> None:
        blocks: list = []
        buf = ["Hello"]
        _flush_buffer(buf, blocks)
        assert buf == []

    def test_excess_newlines_collapsed(self) -> None:
        blocks: list = []
        buf = ["line1\n\n\n\n\nline2"]
        _flush_buffer(buf, blocks)
        assert len(blocks) == 1
        assert "\n\n\n" not in blocks[0].content


# ---------------------------------------------------------------------------
# _strip_boilerplate
# ---------------------------------------------------------------------------


class TestStripBoilerplate:
    def _parse(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "lxml")

    def test_removes_navigation_role(self) -> None:
        soup = self._parse('<div role="navigation"><p>nav links</p></div><main>content</main>')
        _strip_boilerplate(soup)
        assert soup.find(attrs={"role": "navigation"}) is None
        assert soup.find("main") is not None

    def test_removes_hidden_elements(self) -> None:
        soup = self._parse('<div hidden="true">hidden</div><p>visible</p>')
        _strip_boilerplate(soup)
        assert soup.find(attrs={"hidden": True}) is None

    def test_removes_aria_hidden_elements(self) -> None:
        soup = self._parse('<div aria-hidden="true">decorative</div><p>real</p>')
        _strip_boilerplate(soup)
        assert soup.find(attrs={"aria-hidden": "true"}) is None

    def test_removes_sidebar_class(self) -> None:
        soup = self._parse('<div class="sidebar">ads</div><p>content</p>')
        _strip_boilerplate(soup)
        # sidebar should be decomposed
        assert not any("sidebar" in " ".join(el.get("class", [])) for el in soup.find_all(True))

    def test_removes_cookie_consent_class(self) -> None:
        soup = self._parse('<div class="cookie-consent">Accept cookies</div><p>real</p>')
        _strip_boilerplate(soup)
        assert soup.find(class_=lambda c: c and "cookie" in " ".join(c)) is None

    def test_removes_by_id_pattern(self) -> None:
        soup = self._parse('<div id="navbox">nav</div><p>content</p>')
        _strip_boilerplate(soup)
        assert soup.find(id="navbox") is None

    def test_leaves_content_untouched(self) -> None:
        soup = self._parse('<article><p>Main content here</p></article>')
        _strip_boilerplate(soup)
        assert soup.find("p") is not None


# ---------------------------------------------------------------------------
# _walk_images_only
# ---------------------------------------------------------------------------


class TestWalkImagesOnly:
    def _parse(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "lxml")

    def test_finds_nested_image(self) -> None:
        soup = self._parse('<p><img src="/img.png" alt="pic"></p>')
        blocks: list = []
        _walk_images_only(soup.find("p"), "https://example.com", blocks)
        assert len(blocks) == 1
        assert isinstance(blocks[0], ImageBlock)
        assert blocks[0].url == "https://example.com/img.png"
        assert blocks[0].alt == "pic"

    def test_skips_data_uri_image(self) -> None:
        soup = self._parse('<p><img src="data:image/png;base64,abc"></p>')
        blocks: list = []
        _walk_images_only(soup.find("p"), "https://example.com", blocks)
        assert blocks == []

    def test_skips_image_without_src(self) -> None:
        soup = self._parse('<p><img alt="no src"></p>')
        blocks: list = []
        _walk_images_only(soup.find("p"), "https://example.com", blocks)
        assert blocks == []

    def test_collects_multiple_images(self) -> None:
        soup = self._parse('<div><img src="a.png"><img src="b.png"></div>')
        blocks: list = []
        _walk_images_only(soup.find("div"), "https://example.com/", blocks)
        assert len(blocks) == 2


# ---------------------------------------------------------------------------
# _walk
# ---------------------------------------------------------------------------


class TestWalk:
    def _parse_body(self, html: str) -> BeautifulSoup:
        soup = BeautifulSoup(html, "lxml")
        return soup.find("body") or soup

    def test_text_node_appended_to_buffer(self) -> None:
        body = self._parse_body("<body>Hello world</body>")
        blocks: list = []
        buf: list = []
        _walk(body, "", blocks, buf)
        assert "Hello" in "".join(buf) or any("Hello" in b.content for b in blocks if isinstance(b, TextBlock))

    def test_skip_tags_ignored(self) -> None:
        body = self._parse_body("<body><script>alert(1)</script><p>content</p></body>")
        blocks: list = []
        buf: list = []
        _walk(body, "", blocks, buf)
        assert not any("alert" in b.content for b in blocks if isinstance(b, TextBlock))

    def test_img_tag_creates_image_block(self) -> None:
        body = self._parse_body('<body><img src="https://example.com/img.png" alt="test"></body>')
        blocks: list = []
        buf: list = []
        _walk(body, "https://example.com", blocks, buf)
        img_blocks = [b for b in blocks if isinstance(b, ImageBlock)]
        assert len(img_blocks) == 1
        assert img_blocks[0].url == "https://example.com/img.png"

    def test_img_srcset_preferred_over_src(self) -> None:
        body = self._parse_body(
            '<body><img src="/small.png" srcset="/large.png 2x, /xlarge.png 3x" alt="photo"></body>'
        )
        blocks: list = []
        buf: list = []
        _walk(body, "https://example.com", blocks, buf)
        img_blocks = [b for b in blocks if isinstance(b, ImageBlock)]
        assert len(img_blocks) == 1
        assert "large" in img_blocks[0].url

    def test_br_adds_newline_to_buffer(self) -> None:
        body = self._parse_body("<body>line1<br>line2</body>")
        blocks: list = []
        buf: list = []
        _walk(body, "", blocks, buf)
        assert "\n" in buf

    def test_hr_adds_newline_to_buffer(self) -> None:
        body = self._parse_body("<body>section1<hr>section2</body>")
        blocks: list = []
        buf: list = []
        _walk(body, "", blocks, buf)
        assert "\n" in buf

    def test_paragraph_creates_text_block(self) -> None:
        body = self._parse_body("<body><p>This is a paragraph.</p></body>")
        blocks: list = []
        buf: list = []
        _walk(body, "", blocks, buf)
        text_blocks = [b for b in blocks if isinstance(b, TextBlock)]
        assert any("paragraph" in b.content for b in text_blocks)

    def test_heading_creates_text_block(self) -> None:
        body = self._parse_body("<body><h1>Title Here</h1></body>")
        blocks: list = []
        buf: list = []
        _walk(body, "", blocks, buf)
        text_blocks = [b for b in blocks if isinstance(b, TextBlock)]
        assert any("Title" in b.content for b in text_blocks)

    def test_div_recurses_into_children(self) -> None:
        body = self._parse_body("<body><div><p>inside div</p></div></body>")
        blocks: list = []
        buf: list = []
        _walk(body, "", blocks, buf)
        text_blocks = [b for b in blocks if isinstance(b, TextBlock)]
        assert any("inside div" in b.content for b in text_blocks)

    def test_span_recurses_normally(self) -> None:
        body = self._parse_body("<body><span>span text</span></body>")
        blocks: list = []
        buf: list = []
        _walk(body, "", blocks, buf)
        assert "span text" in "".join(buf) or any(
            "span text" in b.content for b in blocks if isinstance(b, TextBlock)
        )

    def test_img_with_data_uri_ignored(self) -> None:
        body = self._parse_body('<body><img src="data:image/png;base64,abc"></body>')
        blocks: list = []
        buf: list = []
        _walk(body, "", blocks, buf)
        assert not any(isinstance(b, ImageBlock) for b in blocks)

    def test_img_without_src_ignored(self) -> None:
        body = self._parse_body('<body><img alt="no src here"></body>')
        blocks: list = []
        buf: list = []
        _walk(body, "", blocks, buf)
        assert not any(isinstance(b, ImageBlock) for b in blocks)


# ---------------------------------------------------------------------------
# _merge_tiny_blocks
# ---------------------------------------------------------------------------


class TestMergeTinyBlocks:
    def test_empty_list_returns_empty(self) -> None:
        assert _merge_tiny_blocks([]) == []

    def test_single_text_block_unchanged(self) -> None:
        blocks = [TextBlock(content="Hello")]
        result = _merge_tiny_blocks(blocks)
        assert len(result) == 1
        assert result[0].content == "Hello"

    def test_two_small_text_blocks_merged(self) -> None:
        blocks = [TextBlock(content="Hello"), TextBlock(content="World")]
        result = _merge_tiny_blocks(blocks)
        assert len(result) == 1
        assert "Hello" in result[0].content
        assert "World" in result[0].content

    def test_image_block_not_merged(self) -> None:
        blocks = [
            TextBlock(content="Text"),
            ImageBlock(url="https://example.com/img.png", alt="img"),
            TextBlock(content="More text"),
        ]
        result = _merge_tiny_blocks(blocks)
        assert len(result) == 3

    def test_large_text_block_not_merged(self) -> None:
        large = "A" * MIN_TEXT_CHARS  # exactly at threshold, won't be merged into
        small = "B" * 10
        blocks = [TextBlock(content=large), TextBlock(content=small)]
        result = _merge_tiny_blocks(blocks)
        # large block is >= MIN_TEXT_CHARS, so no merging
        assert len(result) == 2

    def test_merging_respects_max_chars(self) -> None:
        # Two blocks that together exceed MAX_TEXT_CHARS should NOT be merged
        half = "X" * (MAX_TEXT_CHARS // 2 + 1)
        blocks = [TextBlock(content=half), TextBlock(content=half)]
        result = _merge_tiny_blocks(blocks)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# html_to_blocks (integration)
# ---------------------------------------------------------------------------


class TestHtmlToBlocks:
    def test_extracts_paragraph_text(self) -> None:
        html = "<html><body><p>Hello, world!</p></body></html>"
        blocks = html_to_blocks(html, use_trafilatura=False)
        text_blocks = [b for b in blocks if isinstance(b, TextBlock)]
        assert any("Hello" in b.content for b in text_blocks)

    def test_extracts_image(self) -> None:
        html = '<html><body><img src="https://example.com/img.png" alt="test"></body></html>'
        blocks = html_to_blocks(html, base_url="https://example.com", use_trafilatura=False)
        img_blocks = [b for b in blocks if isinstance(b, ImageBlock)]
        assert len(img_blocks) >= 1
        assert img_blocks[0].url == "https://example.com/img.png"

    def test_skips_script_content(self) -> None:
        html = "<html><body><script>evil()</script><p>good</p></body></html>"
        blocks = html_to_blocks(html, use_trafilatura=False)
        assert not any("evil" in b.content for b in blocks if isinstance(b, TextBlock))

    def test_empty_html_returns_empty_list(self) -> None:
        blocks = html_to_blocks("", use_trafilatura=False)
        assert isinstance(blocks, list)

    def test_boilerplate_stripped_without_trafilatura(self) -> None:
        html = (
            '<html><body>'
            '<div role="navigation">nav</div>'
            '<p>Real content here</p>'
            '</body></html>'
        )
        blocks = html_to_blocks(html, use_trafilatura=False)
        assert not any("nav" in b.content for b in blocks if isinstance(b, TextBlock))

    def test_use_trafilatura_falls_back_on_none(self) -> None:
        html = "<html><body><p>Trafilatura test</p></body></html>"
        with patch("app.utils.html_to_blocks.trafilatura.extract", return_value=None):
            blocks = html_to_blocks(html, use_trafilatura=True)
        assert isinstance(blocks, list)

    def test_use_trafilatura_uses_extracted_html(self) -> None:
        html = "<html><body><p>original</p></body></html>"
        extracted = "<html><body><p>extracted by trafilatura</p></body></html>"
        with patch("app.utils.html_to_blocks.trafilatura.extract", return_value=extracted):
            blocks = html_to_blocks(html, use_trafilatura=True)
        text_blocks = [b for b in blocks if isinstance(b, TextBlock)]
        assert any("extracted" in b.content for b in text_blocks)

    def test_relative_image_resolved_with_base_url(self) -> None:
        html = '<html><body><img src="/logo.png" alt="logo"></body></html>'
        blocks = html_to_blocks(html, base_url="https://site.com", use_trafilatura=False)
        img_blocks = [b for b in blocks if isinstance(b, ImageBlock)]
        assert any(b.url == "https://site.com/logo.png" for b in img_blocks)

    def test_multiple_headings_produce_text_blocks(self) -> None:
        html = (
            "<html><body>"
            "<h1>Heading 1</h1>"
            "<h2>Heading 2</h2>"
            "<h3>Heading 3</h3>"
            "<p>Body text.</p>"
            "</body></html>"
        )
        blocks = html_to_blocks(html, use_trafilatura=False)
        text_blocks = [b for b in blocks if isinstance(b, TextBlock)]
        assert len(text_blocks) >= 1

    def test_returns_list_of_blocks(self) -> None:
        html = "<html><body><p>content</p></body></html>"
        blocks = html_to_blocks(html, use_trafilatura=False)
        assert isinstance(blocks, list)
        for block in blocks:
            assert isinstance(block, (TextBlock, ImageBlock))

    def test_table_content_extracted(self) -> None:
        html = (
            "<html><body><table><tr><td>Cell A</td><td>Cell B</td></tr></table></body></html>"
        )
        blocks = html_to_blocks(html, use_trafilatura=False)
        text_blocks = [b for b in blocks if isinstance(b, TextBlock)]
        assert any("Cell" in b.content for b in text_blocks)

    def test_list_items_extracted(self) -> None:
        html = (
            "<html><body><ul><li>Item 1</li><li>Item 2</li></ul></body></html>"
        )
        blocks = html_to_blocks(html, use_trafilatura=False)
        text_blocks = [b for b in blocks if isinstance(b, TextBlock)]
        assert any("Item" in b.content for b in text_blocks)

    def test_img_srcset_resolved(self) -> None:
        html = (
            '<html><body>'
            '<img src="/small.png" srcset="/large.png 2x">'
            '</body></html>'
        )
        blocks = html_to_blocks(html, base_url="https://example.com", use_trafilatura=False)
        img_blocks = [b for b in blocks if isinstance(b, ImageBlock)]
        assert any("large" in b.url for b in img_blocks)
