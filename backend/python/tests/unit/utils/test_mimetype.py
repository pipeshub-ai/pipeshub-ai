"""Unit tests for app.utils.mimetype_to_extension."""

import pytest

from app.utils.mimetype_to_extension import get_extension_from_mimetype


class TestGetExtensionFromMimetype:
    """Tests for get_extension_from_mimetype()."""

    def test_pdf(self):
        assert get_extension_from_mimetype("application/pdf") == "pdf"

    def test_pdf_alternative(self):
        assert get_extension_from_mimetype("application/x-pdf") == "pdf"

    def test_png(self):
        assert get_extension_from_mimetype("image/png") == "png"

    def test_jpeg(self):
        assert get_extension_from_mimetype("image/jpeg") == "jpeg"

    def test_jpg_variant(self):
        assert get_extension_from_mimetype("image/jpg") == "jpg"

    def test_docx(self):
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert get_extension_from_mimetype(mime) == "docx"

    def test_xlsx(self):
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert get_extension_from_mimetype(mime) == "xlsx"

    def test_pptx(self):
        mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        assert get_extension_from_mimetype(mime) == "pptx"

    def test_csv(self):
        assert get_extension_from_mimetype("text/csv") == "csv"

    def test_html(self):
        assert get_extension_from_mimetype("text/html") == "html"

    def test_markdown(self):
        assert get_extension_from_mimetype("text/markdown") == "md"

    def test_plain_text(self):
        assert get_extension_from_mimetype("text/plain") == "txt"

    def test_svg(self):
        assert get_extension_from_mimetype("image/svg+xml") == "svg"

    def test_webp(self):
        assert get_extension_from_mimetype("image/webp") == "webp"

    def test_mdx(self):
        assert get_extension_from_mimetype("text/mdx") == "mdx"

    def test_tsv(self):
        assert get_extension_from_mimetype("text/tab-separated-values") == "tsv"

    def test_doc(self):
        assert get_extension_from_mimetype("application/msword") == "doc"

    def test_xls(self):
        assert get_extension_from_mimetype("application/vnd.ms-excel") == "xls"

    def test_ppt(self):
        assert get_extension_from_mimetype("application/vnd.ms-powerpoint") == "ppt"

    def test_gmail_content_returns_html(self):
        assert get_extension_from_mimetype("text/gmail_content") == "html"

    def test_unknown_mimetype_returns_none(self):
        assert get_extension_from_mimetype("application/unknown-format") is None

    def test_empty_string_returns_none(self):
        assert get_extension_from_mimetype("") is None

    def test_none_returns_none(self):
        assert get_extension_from_mimetype(None) is None

    def test_charset_variant_pdf(self):
        """Variants without explicit charset are separate entries; unknown ones return None."""
        # "text/pdf" is a known alias
        assert get_extension_from_mimetype("text/pdf") == "pdf"

    def test_charset_variant_csv(self):
        assert get_extension_from_mimetype("text/csv; charset=utf-8") == "csv"

    def test_charset_variant_html(self):
        assert get_extension_from_mimetype("text/html; charset=utf-8") == "html"
