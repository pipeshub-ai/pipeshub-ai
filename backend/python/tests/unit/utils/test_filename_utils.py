"""Unit tests for app.utils.filename_utils.sanitize_filename_for_content_disposition()."""

import pytest

from app.utils.filename_utils import sanitize_filename_for_content_disposition


class TestSanitizeFilenameForContentDisposition:
    """Tests for sanitize_filename_for_content_disposition()."""

    def test_normal_filename_unchanged(self):
        assert sanitize_filename_for_content_disposition("report.pdf") == "report.pdf"

    def test_filename_with_spaces_preserved(self):
        assert sanitize_filename_for_content_disposition("my report.pdf") == "my report.pdf"

    def test_control_chars_newline_removed(self):
        result = sanitize_filename_for_content_disposition("file\nname.txt")
        assert "\n" not in result
        # Newline replaced with space, then normalized
        assert result == "file name.txt"

    def test_control_chars_carriage_return_removed(self):
        result = sanitize_filename_for_content_disposition("file\rname.txt")
        assert "\r" not in result
        assert result == "file name.txt"

    def test_control_chars_tab_removed(self):
        result = sanitize_filename_for_content_disposition("file\tname.txt")
        assert "\t" not in result
        assert result == "file name.txt"

    def test_null_byte_removed(self):
        result = sanitize_filename_for_content_disposition("file\x00name.txt")
        assert "\x00" not in result
        assert result == "file name.txt"

    def test_delete_char_removed(self):
        result = sanitize_filename_for_content_disposition("file\x7fname.txt")
        assert "\x7f" not in result
        assert result == "file name.txt"

    def test_multiple_control_chars(self):
        result = sanitize_filename_for_content_disposition("a\n\r\tb.txt")
        assert result == "a b.txt"

    def test_non_latin1_chars_removed(self):
        # Chinese characters can't be encoded in latin-1 and should be stripped
        result = sanitize_filename_for_content_disposition("report_\u4e2d\u6587.pdf")
        assert result == "report_.pdf"

    def test_emoji_removed(self):
        result = sanitize_filename_for_content_disposition("fun\U0001f600file.txt")
        assert result == "funfile.txt"

    def test_latin1_compatible_chars_preserved(self):
        # Characters like e-acute (\xe9) are valid latin-1
        result = sanitize_filename_for_content_disposition("caf\xe9.txt")
        assert result == "caf\xe9.txt"

    def test_empty_string_returns_fallback(self):
        result = sanitize_filename_for_content_disposition("")
        assert result == "file"

    def test_empty_after_sanitization_returns_fallback(self):
        # All characters removed after latin-1 encoding
        result = sanitize_filename_for_content_disposition("\u4e2d\u6587\u6587\u4ef6")
        assert result == "file"

    def test_custom_fallback(self):
        result = sanitize_filename_for_content_disposition("", fallback="download")
        assert result == "download"

    def test_custom_fallback_when_sanitized_empty(self):
        result = sanitize_filename_for_content_disposition("\u4e2d\u6587", fallback="document.bin")
        assert result == "document.bin"

    def test_whitespace_only_returns_fallback(self):
        result = sanitize_filename_for_content_disposition("   ")
        assert result == "file"

    def test_leading_trailing_whitespace_stripped(self):
        result = sanitize_filename_for_content_disposition("  report.pdf  ")
        assert result == "report.pdf"

    def test_multiple_spaces_collapsed(self):
        result = sanitize_filename_for_content_disposition("file   name    here.txt")
        assert result == "file name here.txt"

    def test_control_chars_at_boundaries(self):
        result = sanitize_filename_for_content_disposition("\nreport.pdf\n")
        assert result == "report.pdf"

    def test_mixed_control_and_non_latin1(self):
        result = sanitize_filename_for_content_disposition("doc\n\u4e2d.pdf")
        assert result == "doc .pdf"

    def test_only_control_chars_returns_fallback(self):
        result = sanitize_filename_for_content_disposition("\n\r\t")
        assert result == "file"

    def test_dot_filename(self):
        result = sanitize_filename_for_content_disposition(".")
        assert result == "."

    def test_filename_with_path_separators(self):
        # Path separators are normal latin-1 chars, not stripped
        result = sanitize_filename_for_content_disposition("path/to/file.txt")
        assert result == "path/to/file.txt"
