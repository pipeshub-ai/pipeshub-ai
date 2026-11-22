"""Unit tests for app.modules.parsers.docx.docparser.DocParser."""

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

        with patch.object(
            parser, "convert_doc_to_docx_async", AsyncMock(return_value=mock_docx_bytes)
        ) as mock_convert:
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
        with patch.object(
            parser, "convert_doc_to_docx_async", AsyncMock(return_value=mock_converted)
        ):
            await parser.parse(b"data", "name.doc")

        args, kwargs = mock_inner.parse.call_args
        assert args[0] is mock_converted
        assert args[1] == "name.doc"
        assert args[2] is None

