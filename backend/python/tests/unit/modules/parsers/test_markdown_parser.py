"""Unit tests for app.modules.parsers.markdown.markdown_parser.MarkdownParser."""

from unittest.mock import MagicMock, patch

import pytest

# Mock docling imports before importing MarkdownParser
with patch.dict("sys.modules", {
    "docling": MagicMock(),
    "docling.datamodel": MagicMock(),
    "docling.datamodel.document": MagicMock(),
    "docling.document_converter": MagicMock(),
}):
    from app.modules.parsers.markdown.markdown_parser import MarkdownParser

# The markdown module referenced by parse_string's globals may differ from
# sys.modules["markdown"] due to the patch.dict context used during import.
# Patch the actual module object that the function's closure sees.
_MD_MODULE = MarkdownParser.parse_string.__globals__["markdown"]


@pytest.fixture
def parser():
    with patch.dict("sys.modules", {
        "docling": MagicMock(),
        "docling.datamodel": MagicMock(),
        "docling.datamodel.document": MagicMock(),
        "docling.document_converter": MagicMock(),
    }):
        with patch("app.modules.parsers.markdown.markdown_parser.DocumentConverter"):
            return MarkdownParser()


# ---------------------------------------------------------------------------
# parse_string
# ---------------------------------------------------------------------------
class TestParseString:
    def test_converts_markdown_to_html_bytes(self, parser):
        with patch.object(_MD_MODULE, "markdown", return_value="<h1>Hello</h1>") as mock_fn:
            result = parser.parse_string("# Hello")
            assert isinstance(result, bytes)
            assert b"<h1>Hello</h1>" in result
            mock_fn.assert_called_once_with("# Hello", extensions=["md_in_html"])

    def test_returns_bytes(self, parser):
        with patch.object(_MD_MODULE, "markdown", return_value="<p>plain text</p>"):
            result = parser.parse_string("plain text")
            assert isinstance(result, bytes)

    def test_paragraph(self, parser):
        with patch.object(_MD_MODULE, "markdown", return_value="<p>Hello world</p>"):
            result = parser.parse_string("Hello world")
            assert b"<p>" in result
            assert b"Hello world" in result

    def test_bold_text(self, parser):
        with patch.object(_MD_MODULE, "markdown", return_value="<p><strong>bold</strong></p>"):
            result = parser.parse_string("**bold**")
            assert b"<strong>" in result

    def test_empty_string(self, parser):
        with patch.object(_MD_MODULE, "markdown", return_value=""):
            result = parser.parse_string("")
            assert isinstance(result, bytes)
            assert result == b""

    def test_encoding_is_utf8(self, parser):
        with patch.object(_MD_MODULE, "markdown", return_value="<p>Unicode: \u00e9\u00e8\u00ea</p>"):
            result = parser.parse_string("Unicode: \u00e9\u00e8\u00ea")
            decoded = result.decode("utf-8")
            assert "\u00e9" in decoded


# ---------------------------------------------------------------------------
# extract_and_replace_images -- inline markdown images
# ---------------------------------------------------------------------------
class TestExtractAndReplaceInlineImages:
    def test_inline_image_replaced(self, parser):
        md = "![alt text](https://example.com/img.png)"
        modified, images = parser.extract_and_replace_images(md)
        assert len(images) == 1
        assert images[0]["url"] == "https://example.com/img.png"
        assert images[0]["alt_text"] == "alt text"
        assert images[0]["new_alt_text"] == "Image_1"
        assert images[0]["image_type"] == "markdown"
        assert "![Image_1](https://example.com/img.png)" in modified

    def test_inline_image_with_title(self, parser):
        md = '![alt](https://example.com/img.png "My Title")'
        modified, images = parser.extract_and_replace_images(md)
        assert len(images) == 1
        assert images[0]["url"] == "https://example.com/img.png"
        assert "![Image_1](https://example.com/img.png)" in modified

    def test_multiple_inline_images(self, parser):
        md = "![a](url1.png)\n![b](url2.png)"
        modified, images = parser.extract_and_replace_images(md)
        assert len(images) == 2
        assert images[0]["new_alt_text"] == "Image_1"
        assert images[1]["new_alt_text"] == "Image_2"

    def test_empty_alt_text(self, parser):
        md = "![](https://example.com/img.png)"
        modified, images = parser.extract_and_replace_images(md)
        assert len(images) == 1
        assert images[0]["alt_text"] == ""
        assert images[0]["new_alt_text"] == "Image_1"


# ---------------------------------------------------------------------------
# extract_and_replace_images -- reference-style images
# ---------------------------------------------------------------------------
class TestExtractAndReplaceReferenceImages:
    def test_reference_image_replaced(self, parser):
        md = "![alt text][ref1]\n\n[ref1]: https://example.com/img.png"
        modified, images = parser.extract_and_replace_images(md)
        assert len(images) == 1
        assert images[0]["url"] == "https://example.com/img.png"
        assert images[0]["alt_text"] == "alt text"
        assert images[0]["new_alt_text"] == "Image_1"
        assert images[0]["image_type"] == "reference"
        assert "![Image_1][ref1]" in modified

    def test_reference_with_title(self, parser):
        md = '![alt][ref]\n\n[ref]: https://example.com/img.png "title"'
        modified, images = parser.extract_and_replace_images(md)
        assert len(images) == 1
        assert images[0]["url"] == "https://example.com/img.png"

    def test_unknown_reference(self, parser):
        md = "![alt][unknown_ref]"
        modified, images = parser.extract_and_replace_images(md)
        assert len(images) == 1
        assert "unknown reference" in images[0]["url"]


# ---------------------------------------------------------------------------
# extract_and_replace_images -- HTML images
# ---------------------------------------------------------------------------
class TestExtractAndReplaceHTMLImages:
    def test_html_img_tag_replaced(self, parser):
        md = '<img src="https://example.com/img.png" alt="photo">'
        modified, images = parser.extract_and_replace_images(md)
        assert len(images) == 1
        assert images[0]["url"] == "https://example.com/img.png"
        assert images[0]["alt_text"] == "photo"
        assert images[0]["new_alt_text"] == "Image_1"
        assert images[0]["image_type"] == "html"
        assert 'alt="Image_1"' in modified

    def test_html_img_no_alt(self, parser):
        md = '<img src="https://example.com/img.png">'
        modified, images = parser.extract_and_replace_images(md)
        assert len(images) == 1
        assert images[0]["alt_text"] == ""
        assert 'alt="Image_1"' in modified

    def test_html_self_closing(self, parser):
        md = '<img src="https://example.com/img.png" alt="x"/>'
        modified, images = parser.extract_and_replace_images(md)
        assert len(images) == 1
        assert images[0]["alt_text"] == "x"


# ---------------------------------------------------------------------------
# extract_and_replace_images -- mixed types
# ---------------------------------------------------------------------------
class TestExtractAndReplaceMixedImages:
    def test_mixed_types_in_one_content(self, parser):
        md = (
            "![inline](https://example.com/1.png)\n"
            "![ref][r1]\n"
            '<img src="https://example.com/3.png" alt="html">\n'
            "\n[r1]: https://example.com/2.png"
        )
        modified, images = parser.extract_and_replace_images(md)
        assert len(images) == 3
        types = [img["image_type"] for img in images]
        assert "reference" in types
        assert "markdown" in types
        assert "html" in types

    def test_sequential_numbering_across_types(self, parser):
        md = (
            "![ref][r1]\n"
            "![inline](https://example.com/2.png)\n"
            '<img src="https://example.com/3.png" alt="html">\n'
            "\n[r1]: https://example.com/1.png"
        )
        modified, images = parser.extract_and_replace_images(md)
        alt_texts = [img["new_alt_text"] for img in images]
        # Numbers should be sequential (1, 2, 3) regardless of type
        assert "Image_1" in alt_texts
        assert "Image_2" in alt_texts
        assert "Image_3" in alt_texts


# ---------------------------------------------------------------------------
# extract_and_replace_images -- no images
# ---------------------------------------------------------------------------
class TestExtractAndReplaceNoImages:
    def test_no_images_returns_empty_list(self, parser):
        md = "# Just a heading\n\nSome paragraph text."
        modified, images = parser.extract_and_replace_images(md)
        assert images == []
        # Content should still be present (BeautifulSoup may normalize whitespace)
        assert "Just a heading" in modified

    def test_no_images_plain_text(self, parser):
        md = "Hello world"
        modified, images = parser.extract_and_replace_images(md)
        assert images == []
        assert "Hello world" in modified
