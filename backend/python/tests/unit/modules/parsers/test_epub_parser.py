"""Unit tests for app.modules.parsers.epub.epub_parser.EPUBParser."""

import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.parsers.epub.epub_parser import EPUBParser
from app.services.parsing.interface import ParseError, ParseErrorCode


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------
class TestEPUBParserInit:
    def test_default_pdf_parser_is_none(self):
        parser = EPUBParser()
        assert parser.pdf_parser is None

    def test_stores_provided_pdf_parser(self):
        mock_inner = MagicMock()
        parser = EPUBParser(pdf_parser=mock_inner)
        assert parser.pdf_parser is mock_inner


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------
class TestParse:
    @pytest.mark.asyncio
    async def test_raises_when_no_pdf_parser_configured(self):
        parser = EPUBParser()
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"data", "book.epub")
        assert exc_info.value.code == ParseErrorCode.PROVIDER_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_delegates_to_pdf_parser_with_pdf_named_record(self):
        mock_pdf_parser = AsyncMock()
        mock_result = MagicMock()
        mock_pdf_parser.parse.return_value = mock_result

        parser = EPUBParser(pdf_parser=mock_pdf_parser)

        with patch.object(
            parser, "convert_epub_to_pdf_async", AsyncMock(return_value=b"pdf bytes")
        ) as mock_convert:
            result = await parser.parse(b"epub bytes", "book.epub", {"key": "val"})

        mock_convert.assert_called_once_with(b"epub bytes")
        mock_pdf_parser.parse.assert_called_once_with(b"pdf bytes", "book.pdf", {"key": "val"})
        assert result is mock_result

    @pytest.mark.asyncio
    async def test_delegates_without_config(self):
        mock_pdf_parser = AsyncMock()
        mock_pdf_parser.parse.return_value = MagicMock()
        parser = EPUBParser(pdf_parser=mock_pdf_parser)

        with patch.object(
            parser, "convert_epub_to_pdf_async", AsyncMock(return_value=b"pdf bytes")
        ):
            await parser.parse(b"data", "name.epub")

        args, kwargs = mock_pdf_parser.parse.call_args
        assert args[0] == b"pdf bytes"
        assert args[1] == "name.pdf"
        assert args[2] is None

    @pytest.mark.asyncio
    async def test_falls_back_to_converted_pdf_when_record_name_empty(self):
        mock_pdf_parser = AsyncMock()
        mock_pdf_parser.parse.return_value = MagicMock()
        parser = EPUBParser(pdf_parser=mock_pdf_parser)

        with patch.object(
            parser, "convert_epub_to_pdf_async", AsyncMock(return_value=b"pdf bytes")
        ):
            await parser.parse(b"data", "")

        args, _kwargs = mock_pdf_parser.parse.call_args
        assert args[1] == "converted.pdf"

    @pytest.mark.asyncio
    async def test_never_imports_pymupdf(self):
        """EPUBParser must delegate PDF parsing entirely; it must not import
        fitz/PyMuPDF nor call Docling/pdfplumber directly."""
        import app.modules.parsers.epub.epub_parser as epub_parser_module

        assert "fitz" not in dir(epub_parser_module)
        assert "pymupdf" not in dir(epub_parser_module)


# ---------------------------------------------------------------------------
# convert_epub_to_pdf_async
# ---------------------------------------------------------------------------
class TestConvertEpubToPdfAsync:
    @pytest.mark.asyncio
    async def test_calls_convert_with_libreoffice(self):
        parser = EPUBParser()
        with patch(
            "app.modules.parsers.epub.epub_parser.convert_with_libreoffice",
            AsyncMock(return_value=b"pdf output"),
        ) as mock_convert:
            result = await parser.convert_epub_to_pdf_async(b"epub input")

        mock_convert.assert_called_once_with(b"epub input", "epub", "pdf")
        assert result == b"pdf output"


# ---------------------------------------------------------------------------
# convert_epub_to_pdf (sync, LibreOffice subprocess)
# ---------------------------------------------------------------------------
class TestConvertEpubToPdf:
    def test_raises_when_libreoffice_not_installed(self, tmp_path):
        parser = EPUBParser()

        with patch("subprocess.run") as mock_run, \
             patch("tempfile.TemporaryDirectory") as mock_tmpdir:
            mock_tmpdir.return_value.__enter__ = MagicMock(return_value=str(tmp_path))
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            mock_run.side_effect = subprocess.CalledProcessError(
                1, ["which", "libreoffice"], stderr=b""
            )

            with pytest.raises(subprocess.CalledProcessError):
                parser.convert_epub_to_pdf(b"epub data")

    def test_raises_when_output_file_not_found(self, tmp_path):
        parser = EPUBParser()

        def _run(cmd, **kwargs):
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_run), \
             patch("tempfile.TemporaryDirectory") as mock_tmpdir, \
             patch("os.path.exists", return_value=False), \
             patch("builtins.open", MagicMock(
                 return_value=MagicMock(
                     __enter__=MagicMock(return_value=MagicMock(write=MagicMock())),
                     __exit__=MagicMock(return_value=False),
                 )
             )):
            mock_tmpdir.return_value.__enter__ = MagicMock(return_value=str(tmp_path))
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(Exception):
                parser.convert_epub_to_pdf(b"epub data")

    def test_raises_on_timeout(self, tmp_path):
        parser = EPUBParser()

        def _run(cmd, **kwargs):
            if "which" in cmd:
                return MagicMock(returncode=0)
            raise subprocess.TimeoutExpired(cmd, 60)

        with patch("subprocess.run", side_effect=_run), \
             patch("tempfile.TemporaryDirectory") as mock_tmpdir, \
             patch("builtins.open", MagicMock(
                 return_value=MagicMock(
                     __enter__=MagicMock(return_value=MagicMock(write=MagicMock())),
                     __exit__=MagicMock(return_value=False),
                 )
             )):
            mock_tmpdir.return_value.__enter__ = MagicMock(return_value=str(tmp_path))
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(Exception, match="timed out"):
                parser.convert_epub_to_pdf(b"epub data")

    def test_returns_bytes_on_success(self, tmp_path):
        parser = EPUBParser()
        fake_pdf = b"fake pdf bytes"
        pdf_path = str(tmp_path / "input.pdf")

        def _run(cmd, **kwargs):
            if "which" in cmd:
                return MagicMock(returncode=0)
            # Simulate LibreOffice creating the output file
            with open(pdf_path, "wb") as f:
                f.write(fake_pdf)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_run), \
             patch("tempfile.TemporaryDirectory") as mock_tmpdir:
            mock_tmpdir.return_value.__enter__ = MagicMock(return_value=str(tmp_path))
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)

            result = parser.convert_epub_to_pdf(b"epub data")

        assert isinstance(result, bytes)
        assert result == fake_pdf
