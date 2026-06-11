"""Tests for the markdown parser modules.

Covers:
- ``DoclingMarkdownParser``: parse_string, parse_file, extract_and_replace_images
- ``MarkdownItParser``:      parse_to_blocks, extract_and_replace_images
- ``markdown_parser`` shim:  MarkdownParser defaults to MarkdownItParser
"""

from unittest.mock import MagicMock, patch

import pytest

_DOCLING_MOCKS = {
    "docling": MagicMock(),
    "docling.datamodel": MagicMock(),
    "docling.datamodel.document": MagicMock(),
    "docling.document_converter": MagicMock(),
}

with patch.dict("sys.modules", _DOCLING_MOCKS):
    from app.modules.parsers.markdown.docling_markdown_parser import (
        DoclingMarkdownParser,
    )
    from app.modules.parsers.markdown.markdown_it_parser import MarkdownItParser

# Grab the real markdown module that DoclingMarkdownParser.parse_string uses so
# tests can patch it precisely.
_MD_MODULE = DoclingMarkdownParser.parse_string.__globals__["markdown_lib"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def docling_parser():
    with patch.dict("sys.modules", _DOCLING_MOCKS):
        with patch(
            "app.modules.parsers.markdown.docling_markdown_parser.DocumentConverter"
        ):
            return DoclingMarkdownParser()


@pytest.fixture
def markdownit_parser():
    return MarkdownItParser()


# ===========================================================================
# DoclingMarkdownParser.parse_string
# ===========================================================================

class TestDoclingParseString:
    def test_converts_markdown_to_html_bytes(self, docling_parser):
        with patch.object(_MD_MODULE, "markdown", return_value="<h1>Hello</h1>") as mock_fn:
            result = docling_parser.parse_string("# Hello")
            assert isinstance(result, bytes)
            assert b"<h1>Hello</h1>" in result
            mock_fn.assert_called_once_with("# Hello", extensions=["md_in_html"])

    def test_returns_bytes(self, docling_parser):
        with patch.object(_MD_MODULE, "markdown", return_value="<p>plain</p>"):
            assert isinstance(docling_parser.parse_string("plain"), bytes)

    def test_paragraph(self, docling_parser):
        with patch.object(_MD_MODULE, "markdown", return_value="<p>Hello world</p>"):
            result = docling_parser.parse_string("Hello world")
            assert b"<p>" in result and b"Hello world" in result

    def test_bold_text(self, docling_parser):
        with patch.object(_MD_MODULE, "markdown", return_value="<p><strong>bold</strong></p>"):
            assert b"<strong>" in docling_parser.parse_string("**bold**")

    def test_empty_string(self, docling_parser):
        with patch.object(_MD_MODULE, "markdown", return_value=""):
            result = docling_parser.parse_string("")
            assert result == b""

    def test_encoding_is_utf8(self, docling_parser):
        with patch.object(_MD_MODULE, "markdown", return_value="<p>Unicode: éèê</p>"):
            decoded = docling_parser.parse_string("Unicode: éèê").decode("utf-8")
            assert "é" in decoded


# ===========================================================================
# DoclingMarkdownParser.parse_file
# ===========================================================================

class TestDoclingParseFile:
    def test_parse_file_success(self, docling_parser):
        mock_result = MagicMock()
        mock_result.status.value = "success"
        mock_result.document = MagicMock()
        docling_parser.converter.convert = MagicMock(return_value=mock_result)

        result = docling_parser.parse_file("/some/file.md")
        assert result is mock_result.document
        docling_parser.converter.convert.assert_called_once_with("/some/file.md")

    def test_parse_file_failure_raises_value_error(self, docling_parser):
        mock_result = MagicMock()
        mock_result.status.value = "failure"
        docling_parser.converter.convert = MagicMock(return_value=mock_result)

        with pytest.raises(ValueError, match="Failed to parse Markdown"):
            docling_parser.parse_file("/bad.md")

    def test_parse_file_partial_success_raises(self, docling_parser):
        mock_result = MagicMock()
        mock_result.status.value = "partial_success"
        docling_parser.converter.convert = MagicMock(return_value=mock_result)

        with pytest.raises(ValueError, match="Failed to parse Markdown"):
            docling_parser.parse_file("/partial.md")

    def test_parse_file_error_status(self, docling_parser):
        mock_result = MagicMock()
        mock_result.status.value = "error"
        docling_parser.converter.convert = MagicMock(return_value=mock_result)

        with pytest.raises(ValueError, match="Failed to parse Markdown"):
            docling_parser.parse_file("/error.md")

    def test_parse_file_empty_status(self, docling_parser):
        mock_result = MagicMock()
        mock_result.status.value = ""
        docling_parser.converter.convert = MagicMock(return_value=mock_result)

        with pytest.raises(ValueError, match="Failed to parse Markdown"):
            docling_parser.parse_file("/empty_status.md")


# ===========================================================================
# extract_and_replace_images — inline images
# (tested on DoclingMarkdownParser; MarkdownItParser uses the same implementation)
# ===========================================================================

class TestExtractAndReplaceInlineImages:
    def test_inline_image_replaced(self, docling_parser):
        md = "![alt text](https://example.com/img.png)"
        modified, images = docling_parser.extract_and_replace_images(md)
        assert len(images) == 1
        assert images[0]["url"] == "https://example.com/img.png"
        assert images[0]["alt_text"] == "alt text"
        assert images[0]["new_alt_text"] == "Image_1"
        assert images[0]["image_type"] == "markdown"
        assert "![Image_1](https://example.com/img.png)" in modified

    def test_inline_image_with_title(self, docling_parser):
        md = '![alt](https://example.com/img.png "My Title")'
        modified, images = docling_parser.extract_and_replace_images(md)
        assert len(images) == 1
        assert images[0]["url"] == "https://example.com/img.png"
        assert "![Image_1](https://example.com/img.png)" in modified

    def test_multiple_inline_images(self, docling_parser):
        md = "![a](url1.png)\n![b](url2.png)"
        _, images = docling_parser.extract_and_replace_images(md)
        assert len(images) == 2
        assert images[0]["new_alt_text"] == "Image_1"
        assert images[1]["new_alt_text"] == "Image_2"

    def test_empty_alt_text(self, docling_parser):
        md = "![](https://example.com/img.png)"
        _, images = docling_parser.extract_and_replace_images(md)
        assert images[0]["alt_text"] == ""
        assert images[0]["new_alt_text"] == "Image_1"


# ===========================================================================
# extract_and_replace_images — reference-style images
# ===========================================================================

class TestExtractAndReplaceReferenceImages:
    def test_reference_image_replaced(self, docling_parser):
        md = "![alt text][ref1]\n\n[ref1]: https://example.com/img.png"
        modified, images = docling_parser.extract_and_replace_images(md)
        assert len(images) == 1
        assert images[0]["url"] == "https://example.com/img.png"
        assert images[0]["image_type"] == "reference"
        assert "![Image_1][ref1]" in modified

    def test_reference_with_title(self, docling_parser):
        md = '![alt][ref]\n\n[ref]: https://example.com/img.png "title"'
        _, images = docling_parser.extract_and_replace_images(md)
        assert images[0]["url"] == "https://example.com/img.png"

    def test_unknown_reference(self, docling_parser):
        md = "![alt][unknown_ref]"
        _, images = docling_parser.extract_and_replace_images(md)
        assert "unknown reference" in images[0]["url"]


# ===========================================================================
# extract_and_replace_images — HTML images
# ===========================================================================

class TestExtractAndReplaceHTMLImages:
    def test_html_img_tag_replaced(self, docling_parser):
        md = '<img src="https://example.com/img.png" alt="photo">'
        modified, images = docling_parser.extract_and_replace_images(md)
        assert images[0]["url"] == "https://example.com/img.png"
        assert images[0]["alt_text"] == "photo"
        assert images[0]["image_type"] == "html"
        assert 'alt="Image_1"' in modified

    def test_html_img_no_alt(self, docling_parser):
        md = '<img src="https://example.com/img.png">'
        modified, images = docling_parser.extract_and_replace_images(md)
        assert images[0]["alt_text"] == ""
        assert 'alt="Image_1"' in modified

    def test_html_self_closing(self, docling_parser):
        md = '<img src="https://example.com/img.png" alt="x"/>'
        _, images = docling_parser.extract_and_replace_images(md)
        assert images[0]["alt_text"] == "x"


# ===========================================================================
# extract_and_replace_images — mixed types
# ===========================================================================

class TestExtractAndReplaceMixedImages:
    def test_mixed_types_in_one_content(self, docling_parser):
        md = (
            "![inline](https://example.com/1.png)\n"
            "![ref][r1]\n"
            '<img src="https://example.com/3.png" alt="html">\n'
            "\n[r1]: https://example.com/2.png"
        )
        _, images = docling_parser.extract_and_replace_images(md)
        assert len(images) == 3
        types = {img["image_type"] for img in images}
        assert types == {"reference", "markdown", "html"}

    def test_sequential_numbering_across_types(self, docling_parser):
        md = (
            "![ref][r1]\n"
            "![inline](https://example.com/2.png)\n"
            '<img src="https://example.com/3.png" alt="html">\n'
            "\n[r1]: https://example.com/1.png"
        )
        _, images = docling_parser.extract_and_replace_images(md)
        new_alts = {img["new_alt_text"] for img in images}
        assert new_alts == {"Image_1", "Image_2", "Image_3"}


# ===========================================================================
# extract_and_replace_images — no images
# ===========================================================================

class TestExtractAndReplaceNoImages:
    def test_no_images_returns_empty_list(self, docling_parser):
        md = "# Just a heading\n\nSome paragraph text."
        modified, images = docling_parser.extract_and_replace_images(md)
        assert images == []
        assert "Just a heading" in modified

    def test_no_images_plain_text(self, docling_parser):
        md = "Hello world"
        modified, images = docling_parser.extract_and_replace_images(md)
        assert images == []
        assert "Hello world" in modified


# ===========================================================================
# extract_and_replace_images — defensive guard
# ===========================================================================

class TestReferencePositionGuard:
    def test_no_overlap_between_ref_and_inline(self, docling_parser):
        md = "![ref_img][r1]\n![inline_img](url.png)\n\n[r1]: https://ref.com/img.png"
        _, images = docling_parser.extract_and_replace_images(md)
        assert len(images) == 2
        ref_images = [i for i in images if i["image_type"] == "reference"]
        inline_images = [i for i in images if i["image_type"] == "markdown"]
        assert len(ref_images) == 1 and len(inline_images) == 1


# ===========================================================================
# MarkdownItParser.extract_and_replace_images
# (same underlying implementation — smoke-test the method is present & works)
# ===========================================================================

class TestMarkdownItParserImageExtraction:
    def test_extract_and_replace_images_present(self, markdownit_parser):
        md = "![alt](https://example.com/img.png)"
        modified, images = markdownit_parser.extract_and_replace_images(md)
        assert len(images) == 1
        assert images[0]["new_alt_text"] == "Image_1"
        assert "![Image_1]" in modified

    def test_extract_and_replace_no_images(self, markdownit_parser):
        md = "No images here."
        modified, images = markdownit_parser.extract_and_replace_images(md)
        assert images == []
        assert modified == "No images here."


# ===========================================================================
# MarkdownParser shim — backward compatibility
# ===========================================================================

class TestMarkdownParserShim:
    def test_markdown_parser_defaults_to_markdownit(self):
        with patch.dict("os.environ", {"MARKDOWN_PARSER_BACKEND": "markdownit"}, clear=False):
            import importlib

            import app.modules.parsers.markdown.markdown_parser as markdown_parser_module

            importlib.reload(markdown_parser_module)
            MarkdownParser = markdown_parser_module.MarkdownParser

        assert MarkdownParser.__name__ == "MarkdownItParser"
        assert MarkdownParser.__module__ == (
            "app.modules.parsers.markdown.markdown_it_parser"
        )

    def test_markdown_parser_can_select_docling_backend(self):
        with patch.dict("os.environ", {"MARKDOWN_PARSER_BACKEND": "docling"}, clear=False):
            with patch.dict("sys.modules", _DOCLING_MOCKS):
                import importlib

                import app.modules.parsers.markdown.markdown_parser as markdown_parser_module

                importlib.reload(markdown_parser_module)
                MarkdownParser = markdown_parser_module.MarkdownParser

        assert MarkdownParser.__name__ == "DoclingMarkdownParser"
        assert MarkdownParser.__module__ == (
            "app.modules.parsers.markdown.docling_markdown_parser"
        )

    @pytest.mark.asyncio
    async def test_markdown_parser_has_parse(self):
        parser = MarkdownItParser()
        with patch.object(
            parser,
            "parse_to_blocks",
            return_value=MagicMock(),
        ) as mock_parse:
            await parser.parse("# Hello\n")
            mock_parse.assert_called_once_with("# Hello\n", None)
