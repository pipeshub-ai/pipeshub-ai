"""Unit tests for app.modules.parsers.docx.docparser.DocParser."""

import subprocess
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.parsers.docx.docparser import DocParser
from app.services.parsing.interface import ParseError, ParseErrorCode


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------
class TestDocParserInit:
    def test_default_docx_parser_is_none(self):
        parser = DocParser()
        assert parser.docx_parser is None

    def test_stores_provided_docx_parser(self):
        mock_inner = MagicMock()
        parser = DocParser(docx_parser=mock_inner)
        assert parser.docx_parser is mock_inner


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------
class TestParse:
    @pytest.mark.asyncio
    async def test_raises_when_no_docx_parser_configured(self):
        parser = DocParser()
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"data", "file.doc")
        assert exc_info.value.code == ParseErrorCode.PROVIDER_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_delegates_to_inner_parser(self):
        mock_inner = AsyncMock()
        mock_result = MagicMock()
        mock_inner.parse.return_value = mock_result

        mock_docx_bytes = BytesIO(b"converted")
        parser = DocParser(docx_parser=mock_inner)

        with patch.object(parser, "convert_doc_to_docx", return_value=mock_docx_bytes) as mock_convert:
            result = await parser.parse(b"doc bytes", "doc.doc", {"key": "val"})

        mock_convert.assert_called_once_with(b"doc bytes")
        mock_inner.parse.assert_called_once_with(mock_docx_bytes, "doc.doc", {"key": "val"})
        assert result is mock_result

    @pytest.mark.asyncio
    async def test_delegates_without_config(self):
        mock_inner = AsyncMock()
        mock_inner.parse.return_value = MagicMock()
        parser = DocParser(docx_parser=mock_inner)

        mock_converted = BytesIO(b"")
        with patch.object(parser, "convert_doc_to_docx", return_value=mock_converted):
            await parser.parse(b"data", "name.doc")

        args, kwargs = mock_inner.parse.call_args
        assert args[0] is mock_converted
        assert args[1] == "name.doc"
        assert args[2] is None


# ---------------------------------------------------------------------------
# convert_doc_to_docx
# ---------------------------------------------------------------------------
class TestConvertDocToDocx:
    def test_raises_when_libreoffice_not_installed(self, tmp_path):
        parser = DocParser()

        with patch("subprocess.run") as mock_run, \
             patch("tempfile.TemporaryDirectory") as mock_tmpdir:
            mock_tmpdir.return_value.__enter__ = MagicMock(return_value=str(tmp_path))
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            mock_run.side_effect = subprocess.CalledProcessError(
                1, ["which", "libreoffice"], stderr=b""
            )

            with pytest.raises(subprocess.CalledProcessError):
                parser.convert_doc_to_docx(b"doc data")

    def test_raises_when_output_file_not_found(self, tmp_path):
        parser = DocParser()

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
                parser.convert_doc_to_docx(b"doc data")

    def test_raises_on_timeout(self, tmp_path):
        parser = DocParser()

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
                parser.convert_doc_to_docx(b"doc data")

    def test_returns_bytesio_on_success(self, tmp_path):
        parser = DocParser()
        fake_docx = b"fake docx bytes"
        docx_path = str(tmp_path / "input.docx")

        def _run(cmd, **kwargs):
            if "which" in cmd:
                return MagicMock(returncode=0)
            # Simulate LibreOffice creating the output file
            with open(docx_path, "wb") as f:
                f.write(fake_docx)
            return MagicMock(returncode=0)

        input_path = str(tmp_path / "input.doc")

        with patch("subprocess.run", side_effect=_run), \
             patch("tempfile.TemporaryDirectory") as mock_tmpdir:
            mock_tmpdir.return_value.__enter__ = MagicMock(return_value=str(tmp_path))
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)

            result = parser.convert_doc_to_docx(b"doc data")

        assert isinstance(result, BytesIO)
        assert result.read() == fake_docx
