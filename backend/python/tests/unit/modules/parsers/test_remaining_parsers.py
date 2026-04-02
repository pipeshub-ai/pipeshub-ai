"""Tests for remaining untested parsers:
- DocParser (app/modules/parsers/docx/docparser.py)
- XLSParser (app/modules/parsers/excel/xls_parser.py)
- MDXParser (app/modules/parsers/markdown/mdx_parser.py)
- PPTParser (app/modules/parsers/pptx/ppt_parser.py)
"""

import subprocess
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest


# ============================================================================
# DocParser
# ============================================================================

class TestDocParser:
    """Tests for app.modules.parsers.docx.docparser.DocParser."""

    def _make_parser(self):
        from app.modules.parsers.docx.docparser import DocParser
        return DocParser()

    @patch("app.modules.parsers.docx.docparser.subprocess.run")
    def test_convert_doc_to_docx_success(self, mock_run):
        """Successful conversion returns BytesIO with docx content."""
        parser = self._make_parser()

        # Mock 'which libreoffice' check
        mock_run.side_effect = [
            MagicMock(returncode=0),  # which libreoffice
            MagicMock(returncode=0),  # libreoffice convert
        ]

        # We need to mock the file operations too
        fake_docx_content = b"PK\x03\x04fake docx content"

        with patch("builtins.open", create=True) as mock_open, \
             patch("os.path.exists", return_value=True):
            # Setup mock for writing input file (wb mode)
            mock_write_handle = MagicMock()
            mock_write_handle.__enter__ = MagicMock(return_value=mock_write_handle)
            mock_write_handle.__exit__ = MagicMock(return_value=False)

            # Setup mock for reading output file (rb mode)
            mock_read_handle = MagicMock()
            mock_read_handle.__enter__ = MagicMock(return_value=mock_read_handle)
            mock_read_handle.__exit__ = MagicMock(return_value=False)
            mock_read_handle.read.return_value = fake_docx_content

            mock_open.side_effect = [mock_write_handle, mock_read_handle]

            result = parser.convert_doc_to_docx(b"fake doc binary")
            assert isinstance(result, BytesIO)
            assert result.read() == fake_docx_content

    @patch("app.modules.parsers.docx.docparser.subprocess.run")
    def test_libreoffice_not_installed_raises(self, mock_run):
        """When LibreOffice is not installed, CalledProcessError is raised."""
        parser = self._make_parser()

        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["which", "libreoffice"],
            output=b"", stderr=b"not found"
        )

        with pytest.raises(subprocess.CalledProcessError):
            parser.convert_doc_to_docx(b"fake doc binary")

    @patch("app.modules.parsers.docx.docparser.subprocess.run")
    def test_conversion_file_not_found_raises(self, mock_run):
        """When converted file is not found, FileNotFoundError is raised."""
        parser = self._make_parser()

        mock_run.side_effect = [
            MagicMock(returncode=0),  # which libreoffice
            MagicMock(returncode=0),  # libreoffice convert
        ]

        with patch("builtins.open", create=True) as mock_open, \
             patch("os.path.exists", return_value=False):
            mock_write_handle = MagicMock()
            mock_write_handle.__enter__ = MagicMock(return_value=mock_write_handle)
            mock_write_handle.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_write_handle

            with pytest.raises(Exception, match="output file not found"):
                parser.convert_doc_to_docx(b"fake doc binary")

    @patch("app.modules.parsers.docx.docparser.subprocess.run")
    def test_conversion_timeout_raises(self, mock_run):
        """When conversion times out, Exception is raised."""
        parser = self._make_parser()

        mock_run.side_effect = [
            MagicMock(returncode=0),  # which libreoffice
            subprocess.TimeoutExpired(cmd="libreoffice", timeout=60),
        ]

        with patch("builtins.open", create=True) as mock_open:
            mock_write_handle = MagicMock()
            mock_write_handle.__enter__ = MagicMock(return_value=mock_write_handle)
            mock_write_handle.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_write_handle

            with pytest.raises(Exception, match="timed out"):
                parser.convert_doc_to_docx(b"fake doc binary")

    @patch("app.modules.parsers.docx.docparser.subprocess.run")
    def test_conversion_generic_error_raises(self, mock_run):
        """Generic errors during conversion are wrapped."""
        parser = self._make_parser()

        mock_run.side_effect = [
            MagicMock(returncode=0),  # which libreoffice
        ]

        with patch("builtins.open", create=True, side_effect=PermissionError("denied")):
            with pytest.raises(Exception, match="Error converting .doc to .docx"):
                parser.convert_doc_to_docx(b"fake doc binary")

    def test_init(self):
        """DocParser can be instantiated."""
        parser = self._make_parser()
        assert parser is not None


# ============================================================================
# XLSParser
# ============================================================================

class TestXLSParser:
    """Tests for app.modules.parsers.excel.xls_parser.XLSParser."""

    def _make_parser(self):
        from app.modules.parsers.excel.xls_parser import XLSParser
        return XLSParser()

    @patch("app.modules.parsers.excel.xls_parser.subprocess.run")
    def test_convert_xls_to_xlsx_success(self, mock_run):
        """Successful conversion returns xlsx bytes."""
        parser = self._make_parser()

        mock_run.side_effect = [
            MagicMock(returncode=0),  # which libreoffice
            MagicMock(returncode=0),  # libreoffice convert
        ]

        fake_xlsx_content = b"PK\x03\x04fake xlsx content"

        with patch("builtins.open", create=True) as mock_open, \
             patch("os.path.exists", return_value=True):
            mock_write_handle = MagicMock()
            mock_write_handle.__enter__ = MagicMock(return_value=mock_write_handle)
            mock_write_handle.__exit__ = MagicMock(return_value=False)

            mock_read_handle = MagicMock()
            mock_read_handle.__enter__ = MagicMock(return_value=mock_read_handle)
            mock_read_handle.__exit__ = MagicMock(return_value=False)
            mock_read_handle.read.return_value = fake_xlsx_content

            mock_open.side_effect = [mock_write_handle, mock_read_handle]

            result = parser.convert_xls_to_xlsx(b"fake xls binary")
            assert isinstance(result, bytes)
            assert result == fake_xlsx_content

    @patch("app.modules.parsers.excel.xls_parser.subprocess.run")
    def test_libreoffice_not_installed_raises(self, mock_run):
        """When LibreOffice is not installed, CalledProcessError is raised."""
        parser = self._make_parser()

        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["which", "libreoffice"],
            output=b"", stderr=b"not found"
        )

        with pytest.raises(subprocess.CalledProcessError):
            parser.convert_xls_to_xlsx(b"fake xls binary")

    @patch("app.modules.parsers.excel.xls_parser.subprocess.run")
    def test_conversion_file_not_found_raises(self, mock_run):
        """When converted file is not found, FileNotFoundError is raised."""
        parser = self._make_parser()

        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0),
        ]

        with patch("builtins.open", create=True) as mock_open, \
             patch("os.path.exists", return_value=False):
            mock_write_handle = MagicMock()
            mock_write_handle.__enter__ = MagicMock(return_value=mock_write_handle)
            mock_write_handle.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_write_handle

            with pytest.raises(Exception, match="output file not found"):
                parser.convert_xls_to_xlsx(b"fake xls binary")

    @patch("app.modules.parsers.excel.xls_parser.subprocess.run")
    def test_conversion_timeout_raises(self, mock_run):
        """When conversion times out, Exception is raised."""
        parser = self._make_parser()

        mock_run.side_effect = [
            MagicMock(returncode=0),
            subprocess.TimeoutExpired(cmd="libreoffice", timeout=60),
        ]

        with patch("builtins.open", create=True) as mock_open:
            mock_write_handle = MagicMock()
            mock_write_handle.__enter__ = MagicMock(return_value=mock_write_handle)
            mock_write_handle.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_write_handle

            with pytest.raises(Exception, match="timed out"):
                parser.convert_xls_to_xlsx(b"fake xls binary")

    @patch("app.modules.parsers.excel.xls_parser.subprocess.run")
    def test_conversion_generic_error_raises(self, mock_run):
        """Generic errors during conversion are wrapped."""
        parser = self._make_parser()

        mock_run.side_effect = [
            MagicMock(returncode=0),
        ]

        with patch("builtins.open", create=True, side_effect=PermissionError("denied")):
            with pytest.raises(Exception, match="Error converting .xls to .xlsx"):
                parser.convert_xls_to_xlsx(b"fake xls binary")

    def test_init(self):
        """XLSParser can be instantiated."""
        parser = self._make_parser()
        assert parser is not None

    @patch("app.modules.parsers.excel.xls_parser.subprocess.run")
    def test_libreoffice_conversion_error_with_stderr(self, mock_run):
        """CalledProcessError with stderr includes error details."""
        parser = self._make_parser()

        error = subprocess.CalledProcessError(
            returncode=1, cmd=["libreoffice"],
            output=b"", stderr=b"conversion error details"
        )
        mock_run.side_effect = error

        with pytest.raises(subprocess.CalledProcessError) as exc:
            parser.convert_xls_to_xlsx(b"fake xls binary")
        assert exc.value.returncode == 1


# ============================================================================
# MDXParser
# ============================================================================

class TestMDXParser:
    """Tests for app.modules.parsers.markdown.mdx_parser.MDXParser."""

    def _make_parser(self):
        from app.modules.parsers.markdown.mdx_parser import MDXParser
        return MDXParser()

    def test_init(self):
        """MDXParser can be instantiated."""
        parser = self._make_parser()
        assert parser is not None
        assert 'CodeGroup' in parser.allowed_tags
        assert 'Info' in parser.allowed_tags
        assert 'Accordion' in parser.allowed_tags

    def test_plain_markdown_unchanged(self):
        """Plain markdown without JSX passes through unchanged."""
        parser = self._make_parser()
        content = b"# Hello World\n\nThis is a paragraph."
        result = parser.convert_mdx_to_md(content)
        assert b"# Hello World" in result
        assert b"This is a paragraph." in result

    def test_info_tag_converted_to_blockquote(self):
        """<Info> tags are converted to blockquote format."""
        parser = self._make_parser()
        content = b"<Info>Important note here</Info>"
        result = parser.convert_mdx_to_md(content)
        decoded = result.decode("utf-8")
        assert "**Note:**" in decoded
        assert "Important note here" in decoded

    def test_code_group_tag_preserves_content(self):
        """<CodeGroup> tags preserve inner content."""
        parser = self._make_parser()
        content = b"<CodeGroup>\n```python\nprint('hello')\n```\n</CodeGroup>"
        result = parser.convert_mdx_to_md(content)
        decoded = result.decode("utf-8")
        assert "print('hello')" in decoded

    def test_accordion_with_title_converted_to_heading(self):
        """<Accordion title="..."> is converted to ### heading."""
        parser = self._make_parser()
        content = b'<Accordion title="FAQ Item">Answer to question</Accordion>'
        result = parser.convert_mdx_to_md(content)
        decoded = result.decode("utf-8")
        assert "### FAQ Item" in decoded
        assert "Answer to question" in decoded

    def test_self_closing_tags_removed(self):
        """Self-closing JSX tags are removed."""
        parser = self._make_parser()
        content = b"Some text <CustomComponent /> more text"
        result = parser.convert_mdx_to_md(content)
        decoded = result.decode("utf-8")
        assert "CustomComponent" not in decoded
        assert "Some text" in decoded
        assert "more text" in decoded

    def test_orphan_closing_tags_removed(self):
        """Orphaned closing tags are removed."""
        parser = self._make_parser()
        content = b"Text before </OrphanTag> text after"
        result = parser.convert_mdx_to_md(content)
        decoded = result.decode("utf-8")
        assert "OrphanTag" not in decoded

    def test_unknown_jsx_block_tags_removed(self):
        """Unknown JSX block tags (not in allowed list) are removed."""
        parser = self._make_parser()
        content = b"<UnknownTag>hidden content</UnknownTag>"
        result = parser.convert_mdx_to_md(content)
        decoded = result.decode("utf-8")
        assert "hidden content" not in decoded
        assert "UnknownTag" not in decoded

    def test_accordion_group_preserves_content(self):
        """<AccordionGroup> preserves inner content."""
        parser = self._make_parser()
        content = b"<AccordionGroup>Inner content here</AccordionGroup>"
        result = parser.convert_mdx_to_md(content)
        decoded = result.decode("utf-8")
        assert "Inner content here" in decoded

    def test_excess_whitespace_cleaned(self):
        """Excess blank lines are collapsed."""
        parser = self._make_parser()
        content = b"Line 1\n\n\n\n\nLine 2"
        result = parser.convert_mdx_to_md(content)
        decoded = result.decode("utf-8")
        # Should not have more than 2 consecutive newlines
        assert "\n\n\n" not in decoded

    def test_utf8_content_preserved(self):
        """UTF-8 content is preserved correctly."""
        parser = self._make_parser()
        content = "# Title with unicode: cafe\u0301".encode("utf-8")
        result = parser.convert_mdx_to_md(content)
        assert "cafe\u0301" in result.decode("utf-8")

    def test_empty_content(self):
        """Empty content returns empty bytes."""
        parser = self._make_parser()
        result = parser.convert_mdx_to_md(b"")
        assert result == b""

    def test_mixed_jsx_and_markdown(self):
        """File with both JSX and markdown content is handled correctly."""
        parser = self._make_parser()
        content = b"""# Title

<Info>This is a note</Info>

Regular paragraph here.

<CustomWidget prop="value" />

## Subtitle

More text."""
        result = parser.convert_mdx_to_md(content)
        decoded = result.decode("utf-8")
        assert "# Title" in decoded
        assert "**Note:**" in decoded
        assert "Regular paragraph here." in decoded
        assert "CustomWidget" not in decoded
        assert "## Subtitle" in decoded
        assert "More text." in decoded

    def test_conversion_error_raises(self):
        """Non-UTF8 content that fails to decode raises an error."""
        parser = self._make_parser()
        # Create invalid UTF-8 bytes
        invalid_utf8 = b"\xff\xfe invalid"
        with pytest.raises(Exception, match="Error converting MDX"):
            parser.convert_mdx_to_md(invalid_utf8)

    def test_nested_accordion_with_title(self):
        """Multiple Accordion tags with titles converted correctly."""
        parser = self._make_parser()
        content = b"""<Accordion title="Q1">Answer 1</Accordion>
<Accordion title="Q2">Answer 2</Accordion>"""
        result = parser.convert_mdx_to_md(content)
        decoded = result.decode("utf-8")
        assert "### Q1" in decoded
        assert "Answer 1" in decoded
        assert "### Q2" in decoded
        assert "Answer 2" in decoded


# ============================================================================
# PPTParser
# ============================================================================

class TestPPTParser:
    """Tests for app.modules.parsers.pptx.ppt_parser.PPTParser."""

    def _make_parser(self):
        from app.modules.parsers.pptx.ppt_parser import PPTParser
        return PPTParser()

    @patch("app.modules.parsers.pptx.ppt_parser.subprocess.run")
    def test_convert_ppt_to_pptx_success(self, mock_run):
        """Successful conversion returns pptx bytes."""
        parser = self._make_parser()

        mock_run.side_effect = [
            MagicMock(returncode=0),  # which libreoffice
            MagicMock(returncode=0),  # libreoffice convert
        ]

        fake_pptx_content = b"PK\x03\x04fake pptx content"

        with patch("builtins.open", create=True) as mock_open, \
             patch("os.path.exists", return_value=True):
            mock_write_handle = MagicMock()
            mock_write_handle.__enter__ = MagicMock(return_value=mock_write_handle)
            mock_write_handle.__exit__ = MagicMock(return_value=False)

            mock_read_handle = MagicMock()
            mock_read_handle.__enter__ = MagicMock(return_value=mock_read_handle)
            mock_read_handle.__exit__ = MagicMock(return_value=False)
            mock_read_handle.read.return_value = fake_pptx_content

            mock_open.side_effect = [mock_write_handle, mock_read_handle]

            result = parser.convert_ppt_to_pptx(b"fake ppt binary")
            assert isinstance(result, bytes)
            assert result == fake_pptx_content

    @patch("app.modules.parsers.pptx.ppt_parser.subprocess.run")
    def test_libreoffice_not_installed_raises(self, mock_run):
        """When LibreOffice is not installed, CalledProcessError is raised."""
        parser = self._make_parser()

        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["which", "libreoffice"],
            output=b"", stderr=b"not found"
        )

        with pytest.raises(subprocess.CalledProcessError):
            parser.convert_ppt_to_pptx(b"fake ppt binary")

    @patch("app.modules.parsers.pptx.ppt_parser.subprocess.run")
    def test_conversion_file_not_found_raises(self, mock_run):
        """When converted file is not found, FileNotFoundError is raised."""
        parser = self._make_parser()

        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0),
        ]

        with patch("builtins.open", create=True) as mock_open, \
             patch("os.path.exists", return_value=False):
            mock_write_handle = MagicMock()
            mock_write_handle.__enter__ = MagicMock(return_value=mock_write_handle)
            mock_write_handle.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_write_handle

            with pytest.raises(Exception, match="output file not found"):
                parser.convert_ppt_to_pptx(b"fake ppt binary")

    @patch("app.modules.parsers.pptx.ppt_parser.subprocess.run")
    def test_conversion_timeout_raises(self, mock_run):
        """When conversion times out, Exception is raised."""
        parser = self._make_parser()

        mock_run.side_effect = [
            MagicMock(returncode=0),
            subprocess.TimeoutExpired(cmd="libreoffice", timeout=60),
        ]

        with patch("builtins.open", create=True) as mock_open:
            mock_write_handle = MagicMock()
            mock_write_handle.__enter__ = MagicMock(return_value=mock_write_handle)
            mock_write_handle.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_write_handle

            with pytest.raises(Exception, match="timed out"):
                parser.convert_ppt_to_pptx(b"fake ppt binary")

    @patch("app.modules.parsers.pptx.ppt_parser.subprocess.run")
    def test_conversion_generic_error_raises(self, mock_run):
        """Generic errors during conversion are wrapped."""
        parser = self._make_parser()

        mock_run.side_effect = [
            MagicMock(returncode=0),
        ]

        with patch("builtins.open", create=True, side_effect=PermissionError("denied")):
            with pytest.raises(Exception, match="Error converting .ppt to .pptx"):
                parser.convert_ppt_to_pptx(b"fake ppt binary")

    def test_init(self):
        """PPTParser can be instantiated."""
        parser = self._make_parser()
        assert parser is not None

    @patch("app.modules.parsers.pptx.ppt_parser.subprocess.run")
    def test_libreoffice_error_with_stderr(self, mock_run):
        """CalledProcessError with stderr includes error details in message."""
        parser = self._make_parser()

        error = subprocess.CalledProcessError(
            returncode=1, cmd=["libreoffice"],
            output=b"", stderr=b"detailed error info"
        )
        mock_run.side_effect = error

        with pytest.raises(subprocess.CalledProcessError) as exc:
            parser.convert_ppt_to_pptx(b"fake ppt binary")
        assert exc.value.returncode == 1

    @patch("app.modules.parsers.pptx.ppt_parser.subprocess.run")
    def test_writes_input_binary_to_temp_file(self, mock_run):
        """Input binary is written to temporary file correctly."""
        parser = self._make_parser()

        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0),
        ]

        input_binary = b"test ppt content"
        written_data = []

        with patch("builtins.open", create=True) as mock_open, \
             patch("os.path.exists", return_value=True):
            mock_write_handle = MagicMock()
            mock_write_handle.__enter__ = MagicMock(return_value=mock_write_handle)
            mock_write_handle.__exit__ = MagicMock(return_value=False)
            mock_write_handle.write = MagicMock(side_effect=lambda d: written_data.append(d))

            mock_read_handle = MagicMock()
            mock_read_handle.__enter__ = MagicMock(return_value=mock_read_handle)
            mock_read_handle.__exit__ = MagicMock(return_value=False)
            mock_read_handle.read.return_value = b"converted"

            mock_open.side_effect = [mock_write_handle, mock_read_handle]

            parser.convert_ppt_to_pptx(input_binary)
            assert written_data[0] == input_binary
