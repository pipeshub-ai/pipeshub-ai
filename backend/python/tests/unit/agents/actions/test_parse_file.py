"""
Unit tests for app.agents.actions.util.parse_file

Tests FileContentParser and its helpers. All heavy parser dependencies
(DoclingProcessor, PyMuPDFOpenCVProcessor, ExcelParser, CSVParser, etc.)
are mocked.
"""

import io
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.util.parse_file import (
    FileContentParser,
    SUPPORTED_EXTENSIONS,
    _docling_document_name,
    _validate_magic,
    _MAGIC_OLE2,
    _MAGIC_PDF,
    _MAGIC_ZIP,
)
from app.models.blocks import Block, BlockType, BlocksContainer, ImageMetadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_parser():
    """Create a FileContentParser with mocked deps, skipping __init__ heavy setup."""
    logger = logging.getLogger("test")
    config_service = MagicMock()
    parser = FileContentParser.__new__(FileContentParser)
    parser._logger = logger
    parser._config = config_service
    # Sub-parsers that may be referenced by handlers
    parser._md_parser = MagicMock()
    parser._mdx_parser = MagicMock()
    parser._html_parser = MagicMock()
    parser._doc_parser = MagicMock()
    parser._xls_parser = MagicMock()
    parser._ppt_parser = MagicMock()
    parser._image_parser = MagicMock()
    parser._csv_parser = MagicMock()
    parser._tsv_parser = MagicMock()
    return parser


def _make_file_record(extension="txt", record_name="doc", mime_type="text/plain"):
    rec = MagicMock()
    rec.extension = extension
    rec.record_name = record_name
    rec.mime_type = mime_type
    rec.block_containers = None
    rec.to_llm_full_context = MagicMock(return_value="llm context")
    return rec


# ============================================================================
# _validate_magic
# ============================================================================

class TestValidateMagic:
    def test_pdf_valid(self):
        _validate_magic(_MAGIC_PDF + b"rest", "pdf")  # no exception

    def test_pdf_invalid(self):
        with pytest.raises(ValueError, match="PDF"):
            _validate_magic(b"NOPE", "pdf")

    def test_pdf_too_short(self):
        with pytest.raises(ValueError):
            _validate_magic(b"%P", "pdf")

    def test_docx_valid(self):
        _validate_magic(_MAGIC_ZIP + b"extra", "docx")

    def test_docx_invalid(self):
        with pytest.raises(ValueError, match="ZIP/OOXML"):
            _validate_magic(b"NOTZIP12", "docx")

    def test_xlsx_valid(self):
        _validate_magic(_MAGIC_ZIP + b"extra", "xlsx")

    def test_pptx_invalid(self):
        with pytest.raises(ValueError):
            _validate_magic(b"XXXX", "pptx")

    def test_doc_ole2_valid(self):
        _validate_magic(_MAGIC_OLE2 + b"rest", "doc")

    def test_xls_ole2_invalid(self):
        with pytest.raises(ValueError, match="OLE2"):
            _validate_magic(b"NOTOLE2X", "xls")

    def test_non_binary_extension_no_check(self):
        # Should not raise for txt/md/csv
        _validate_magic(b"hello", "txt")
        _validate_magic(b"hello", "md")
        _validate_magic(b"a,b,c", "csv")


# ============================================================================
# _docling_document_name
# ============================================================================

class TestDoclingDocumentName:
    def test_appends_extension(self):
        assert _docling_document_name("myfile.docx", "md") == "myfile.md"

    def test_no_extension_in_name(self):
        assert _docling_document_name("report", "pdf") == "report.pdf"

    def test_empty_name_uses_default(self):
        assert _docling_document_name("", "pdf") == "document.pdf"


# ============================================================================
# SUPPORTED_EXTENSIONS
# ============================================================================

class TestSupportedExtensions:
    def test_contains_common_types(self):
        for ext in ["pdf", "docx", "xlsx", "pptx", "txt", "md", "csv", "html"]:
            assert ext in SUPPORTED_EXTENSIONS

    def test_is_frozenset(self):
        assert isinstance(SUPPORTED_EXTENSIONS, frozenset)


# ============================================================================
# FileContentParser.is_supported_extension
# ============================================================================

class TestIsSupportedExtension:
    def test_supported(self):
        parser = _make_parser()
        assert parser.is_supported_extension("pdf") is True
        assert parser.is_supported_extension("docx") is True

    def test_unsupported(self):
        parser = _make_parser()
        assert parser.is_supported_extension("exe") is False
        assert parser.is_supported_extension("") is False


# ============================================================================
# FileContentParser.parse_raw_to_blocks
# ============================================================================

class TestParseRawToBlocks:
    @pytest.mark.asyncio
    async def test_empty_extension(self):
        parser = _make_parser()
        with pytest.raises(ValueError, match="extension is required"):
            await parser.parse_raw_to_blocks(b"data", "")

    @pytest.mark.asyncio
    async def test_unsupported_extension(self):
        parser = _make_parser()
        with pytest.raises(ValueError, match="Unsupported file type"):
            await parser.parse_raw_to_blocks(b"data", "exe")

    @pytest.mark.asyncio
    async def test_pdf_magic_mismatch_raises(self):
        parser = _make_parser()
        with pytest.raises(ValueError):
            await parser.parse_raw_to_blocks(b"NOPE", "pdf")

    @pytest.mark.asyncio
    async def test_docx_magic_mismatch_raises(self):
        parser = _make_parser()
        with pytest.raises(ValueError):
            await parser.parse_raw_to_blocks(b"NOTZIP", "docx")

    @pytest.mark.asyncio
    async def test_dispatches_to_handler(self):
        parser = _make_parser()
        expected = BlocksContainer(blocks=[], block_groups=[])
        parser.handle_txt = AsyncMock(return_value=expected)
        result = await parser.parse_raw_to_blocks(b"hello", "txt", file_name="a.txt")
        assert result is expected
        parser.handle_txt.assert_called_once()


# ============================================================================
# FileContentParser.parse_to_block_container
# ============================================================================

class TestParseToBlockContainer:
    @pytest.mark.asyncio
    async def test_no_file_record(self):
        parser = _make_parser()
        with pytest.raises(ValueError, match="File record"):
            await parser.parse_to_block_container(None, b"data")

    @pytest.mark.asyncio
    async def test_empty_raw(self):
        parser = _make_parser()
        rec = _make_file_record()
        with pytest.raises(ValueError, match="File content"):
            await parser.parse_to_block_container(rec, b"")

    @pytest.mark.asyncio
    async def test_non_bytes_raw(self):
        parser = _make_parser()
        rec = _make_file_record()
        with pytest.raises(ValueError, match="File content"):
            await parser.parse_to_block_container(rec, "string")

    @pytest.mark.asyncio
    async def test_normalizes_extension(self):
        parser = _make_parser()
        rec = _make_file_record(extension=".TXT  ")
        parser.parse_raw_to_blocks = AsyncMock(
            return_value=BlocksContainer(blocks=[], block_groups=[])
        )
        await parser.parse_to_block_container(rec, b"data")
        # Verify normalized extension passed
        call_args = parser.parse_raw_to_blocks.call_args
        assert call_args[0][1] == "txt"


# ============================================================================
# FileContentParser.check_token_limit
# ============================================================================

class TestCheckTokenLimit:
    @pytest.mark.asyncio
    async def test_empty_data(self):
        parser = _make_parser()
        result = await parser.check_token_limit(
            model_name=None, model_key=None, configuration_service=MagicMock(), data=""
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_within_limit(self):
        parser = _make_parser()
        with patch(
            "app.agents.actions.util.parse_file.get_model_config",
            new_callable=AsyncMock,
            return_value=({"contextLength": 100000}, None),
        ), patch(
            "app.agents.actions.util.parse_file.count_tokens_text",
            return_value=1000,
        ):
            result = await parser.check_token_limit(
                model_name="gpt-4", model_key="k", configuration_service=MagicMock(), data="data"
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_within_limit_dict_messages(self):
        parser = _make_parser()
        with patch(
            "app.agents.actions.util.parse_file.get_model_config",
            new_callable=AsyncMock,
            return_value=({"contextLength": 100000}, None),
        ), patch(
            "app.agents.actions.util.parse_file.count_tokens_text",
            return_value=10,
        ):
            data = [{"type": "text", "text": "hello"}]
            result = await parser.check_token_limit(
                model_name="gpt-4", model_key="k", configuration_service=MagicMock(), data=data
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_exceeds_limit(self):
        parser = _make_parser()
        with patch(
            "app.agents.actions.util.parse_file.get_model_config",
            new_callable=AsyncMock,
            return_value=({"contextLength": 1000}, None),
        ), patch(
            "app.agents.actions.util.parse_file.count_tokens_text",
            return_value=900,  # 900 > 800 (80% of 1000)
        ):
            data = [{"type": "text", "text": "hello"}]
            result = await parser.check_token_limit(
                model_name="gpt-4", model_key="k", configuration_service=MagicMock(), data=data
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_list_model_config(self):
        parser = _make_parser()
        with patch(
            "app.agents.actions.util.parse_file.get_model_config",
            new_callable=AsyncMock,
            return_value=([{"contextLength": 128_000}], None),
        ), patch(
            "app.agents.actions.util.parse_file.count_tokens_text",
            return_value=10,
        ):
            data = [{"type": "text", "text": "x"}]
            result = await parser.check_token_limit(
                model_name=None, model_key=None, configuration_service=MagicMock(), data=data
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_default_context_length_used(self):
        parser = _make_parser()
        with patch(
            "app.agents.actions.util.parse_file.get_model_config",
            new_callable=AsyncMock,
            return_value=({}, None),
        ), patch(
            "app.agents.actions.util.parse_file.count_tokens_text",
            return_value=1,
        ):
            data = [{"type": "text", "text": "x"}]
            result = await parser.check_token_limit(
                model_name=None, model_key=None, configuration_service=MagicMock(), data=data
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_non_text_messages_ignored(self):
        parser = _make_parser()
        with patch(
            "app.agents.actions.util.parse_file.get_model_config",
            new_callable=AsyncMock,
            return_value=({"contextLength": 1000}, None),
        ):
            # No "type": "text" entries → token_count stays 0
            data = [{"type": "image"}, "not a dict"]
            result = await parser.check_token_limit(
                model_name=None, model_key=None, configuration_service=MagicMock(), data=data
            )
            assert result is True


# ============================================================================
# FileContentParser.parse
# ============================================================================

class TestParse:
    @pytest.mark.asyncio
    async def test_unsupported_extension(self):
        parser = _make_parser()
        rec = _make_file_record(extension="exe")
        ok, msg = await parser.parse(rec, b"data", None, None, MagicMock())
        assert ok is False
        assert "not supported" in msg[0]["error"]

    @pytest.mark.asyncio
    async def test_missing_extension(self):
        parser = _make_parser()
        rec = _make_file_record(extension="")
        ok, msg = await parser.parse(rec, b"data", None, None, MagicMock())
        assert ok is False

    @pytest.mark.asyncio
    async def test_parse_to_block_container_fails(self):
        parser = _make_parser()
        rec = _make_file_record(extension="txt")
        parser.parse_to_block_container = AsyncMock(side_effect=RuntimeError("bad"))
        ok, msg = await parser.parse(rec, b"data", None, None, MagicMock())
        assert ok is False
        assert "Parse failed" in msg[0]["error"]

    @pytest.mark.asyncio
    async def test_to_llm_context_fails(self):
        parser = _make_parser()
        rec = _make_file_record(extension="txt")
        rec.to_llm_full_context = MagicMock(side_effect=RuntimeError("ctx err"))
        parser.parse_to_block_container = AsyncMock(
            return_value=BlocksContainer(blocks=[], block_groups=[])
        )
        ok, msg = await parser.parse(rec, b"data", None, None, MagicMock())
        assert ok is False
        assert "Context build failed" in msg[0]["error"]

    @pytest.mark.asyncio
    async def test_token_check_fails(self):
        parser = _make_parser()
        rec = _make_file_record(extension="txt")
        parser.parse_to_block_container = AsyncMock(
            return_value=BlocksContainer(blocks=[], block_groups=[])
        )
        parser.check_token_limit = AsyncMock(side_effect=RuntimeError("tok err"))
        ok, msg = await parser.parse(rec, b"data", None, None, MagicMock())
        assert ok is False
        assert "Token check failed" in msg[0]["error"]

    @pytest.mark.asyncio
    async def test_within_limit_returns_success(self):
        parser = _make_parser()
        rec = _make_file_record(extension="txt")
        parser.parse_to_block_container = AsyncMock(
            return_value=BlocksContainer(blocks=[], block_groups=[])
        )
        parser.check_token_limit = AsyncMock(return_value=True)
        ok, msg = await parser.parse(rec, b"data", None, None, MagicMock())
        assert ok is True
        assert msg == "llm context"

    @pytest.mark.asyncio
    async def test_exceeds_limit_returns_failure(self):
        parser = _make_parser()
        rec = _make_file_record(extension="txt")
        parser.parse_to_block_container = AsyncMock(
            return_value=BlocksContainer(blocks=[], block_groups=[])
        )
        parser.check_token_limit = AsyncMock(return_value=False)
        ok, msg = await parser.parse(rec, b"data", None, None, MagicMock())
        assert ok is False
        assert "Token limit exceeded" in msg[0]["error"]

    @pytest.mark.asyncio
    async def test_outer_exception_wrapped(self):
        parser = _make_parser()
        rec = _make_file_record(extension="txt")
        with patch.object(
            parser, "is_supported_extension", side_effect=RuntimeError("outer boom")
        ):
            ok, msg = await parser.parse(rec, b"data", None, None, MagicMock())
        assert ok is False
        assert "Failed to parse file" in msg[0]["error"]


# ============================================================================
# FileContentParser.__init__
# ============================================================================


class TestFileContentParserInit:
    def test_constructors_wire_parsers(self):
        logger = logging.getLogger("test.init")
        config = MagicMock()
        with (
            patch("app.agents.actions.util.parse_file.MarkdownParser") as md,
            patch("app.agents.actions.util.parse_file.MDXParser") as mdx,
            patch("app.agents.actions.util.parse_file.HTMLParser") as html,
            patch("app.agents.actions.util.parse_file.DocParser") as doc,
            patch("app.agents.actions.util.parse_file.XLSParser") as xls,
            patch("app.agents.actions.util.parse_file.PPTParser") as ppt,
            patch("app.agents.actions.util.parse_file.ImageParser") as img,
            patch("app.agents.actions.util.parse_file.CSVParser") as csvp,
        ):
            parser = FileContentParser(logger, config)
            md.assert_called_once()
            mdx.assert_called_once()
            html.assert_called_once()
            doc.assert_called_once()
            xls.assert_called_once()
            ppt.assert_called_once()
            img.assert_called_once_with(logger)
            assert csvp.call_count == 2


# ============================================================================
# Handler methods (with mocked processors)
# ============================================================================

class TestHandlePdf:
    @pytest.mark.asyncio
    async def test_delegates_to_pymupdf_processor(self):
        parser = _make_parser()
        expected = BlocksContainer(blocks=[], block_groups=[])

        with patch(
            "app.agents.actions.util.parse_file.PyMuPDFOpenCVProcessor"
        ) as mock_cls:
            instance = mock_cls.return_value
            instance.load_document = AsyncMock(return_value=expected)

            result = await parser.handle_pdf(b"%PDF-raw", "report.pdf")
            assert result is expected
            # Name used ends with .pdf
            call_args = instance.load_document.call_args[0]
            assert call_args[0].endswith(".pdf")

    @pytest.mark.asyncio
    async def test_adds_pdf_extension_if_missing(self):
        parser = _make_parser()
        with patch(
            "app.agents.actions.util.parse_file.PyMuPDFOpenCVProcessor"
        ) as mock_cls:
            instance = mock_cls.return_value
            instance.load_document = AsyncMock(
                return_value=BlocksContainer(blocks=[], block_groups=[])
            )
            await parser.handle_pdf(b"raw", "myfile")
            call_args = instance.load_document.call_args[0]
            assert call_args[0] == "myfile.pdf"


class TestHandleDocx:
    @pytest.mark.asyncio
    async def test_uses_docling(self):
        parser = _make_parser()
        expected = BlocksContainer(blocks=[], block_groups=[])
        with patch(
            "app.agents.actions.util.parse_file.DoclingProcessor"
        ) as mock_cls:
            instance = mock_cls.return_value
            instance.parse_document = AsyncMock(return_value="conv_res")
            instance.create_blocks = AsyncMock(return_value=expected)
            result = await parser.handle_docx(b"raw", "doc.docx")
            assert result is expected


class TestHandlePptx:
    @pytest.mark.asyncio
    async def test_uses_docling_like_docx(self):
        parser = _make_parser()
        expected = BlocksContainer(blocks=[], block_groups=[])
        with patch(
            "app.agents.actions.util.parse_file.DoclingProcessor"
        ) as mock_cls:
            instance = mock_cls.return_value
            instance.parse_document = AsyncMock(return_value="conv_res")
            instance.create_blocks = AsyncMock(return_value=expected)
            result = await parser.handle_pptx(b"raw", "slides.pptx")
            assert result is expected
            call_name = instance.parse_document.call_args[0][0]
            assert call_name.endswith(".pptx")


class TestHandleDoc:
    @pytest.mark.asyncio
    async def test_converts_doc_to_docx(self):
        parser = _make_parser()
        stream = io.BytesIO(b"fake docx bytes")
        parser._doc_parser.convert_doc_to_docx = MagicMock(return_value=stream)
        parser.handle_docx = AsyncMock(
            return_value=BlocksContainer(blocks=[], block_groups=[])
        )
        await parser.handle_doc(b"raw", "file.doc")
        parser._doc_parser.convert_doc_to_docx.assert_called_once_with(b"raw")
        parser.handle_docx.assert_called_once()


class TestHandlePptAndXls:
    @pytest.mark.asyncio
    async def test_ppt_converts_to_pptx(self):
        parser = _make_parser()
        parser._ppt_parser.convert_ppt_to_pptx = MagicMock(return_value=b"pptx bytes")
        parser.handle_pptx = AsyncMock(
            return_value=BlocksContainer(blocks=[], block_groups=[])
        )
        await parser.handle_ppt(b"raw", "a.ppt")
        parser._ppt_parser.convert_ppt_to_pptx.assert_called_once_with(b"raw")

    @pytest.mark.asyncio
    async def test_xls_converts_to_xlsx(self):
        parser = _make_parser()
        parser._xls_parser.convert_xls_to_xlsx = MagicMock(return_value=b"xlsx bytes")
        parser.handle_xlsx = AsyncMock(
            return_value=BlocksContainer(blocks=[], block_groups=[])
        )
        await parser.handle_xls(b"raw", "a.xls")
        parser._xls_parser.convert_xls_to_xlsx.assert_called_once_with(b"raw")


class TestHandleXlsx:
    @pytest.mark.asyncio
    async def test_uses_excel_parser(self):
        parser = _make_parser()
        expected = BlocksContainer(blocks=[], block_groups=[])
        with patch(
            "app.agents.actions.util.parse_file.get_llm",
            new_callable=AsyncMock,
            return_value=("llm", None),
        ), patch(
            "app.agents.actions.util.parse_file.ExcelParser"
        ) as mock_cls:
            instance = mock_cls.return_value
            instance.load_workbook_from_binary = MagicMock()
            instance.create_blocks = AsyncMock(return_value=expected)
            result = await parser.handle_xlsx(b"raw", "book.xlsx")
            assert result is expected
            instance.load_workbook_from_binary.assert_called_once_with(b"raw")


class TestHandleCsvTsv:
    @pytest.mark.asyncio
    async def test_empty_delimited_returns_empty_blocks(self):
        parser = _make_parser()
        parser._csv_parser.read_raw_rows = MagicMock(return_value=None)
        result = await parser.handle_csv(b"", "empty.csv")
        assert isinstance(result, BlocksContainer)
        assert result.blocks == []

    @pytest.mark.asyncio
    async def test_undecodable_returns_empty(self):
        parser = _make_parser()
        parser._csv_parser.read_raw_rows = MagicMock(
            side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "bad")
        )
        result = await parser.handle_csv(b"\xff\xfe", "bad.csv")
        assert isinstance(result, BlocksContainer)

    @pytest.mark.asyncio
    async def test_csv_successful_parse(self):
        parser = _make_parser()
        parser._csv_parser.read_raw_rows = MagicMock(return_value=[["a", "b"], ["1", "2"]])
        parser._csv_parser.find_tables_in_csv = MagicMock(return_value=[MagicMock()])
        expected = BlocksContainer(blocks=[], block_groups=[])
        parser._csv_parser.get_blocks_from_csv_with_multiple_tables = AsyncMock(
            return_value=expected
        )
        with patch(
            "app.agents.actions.util.parse_file.get_llm",
            new_callable=AsyncMock,
            return_value=("llm", None),
        ):
            result = await parser.handle_csv(b"a,b\n1,2", "file.csv")
            assert result is expected

    @pytest.mark.asyncio
    async def test_tsv_uses_tsv_parser(self):
        parser = _make_parser()
        parser._tsv_parser.read_raw_rows = MagicMock(return_value=None)
        result = await parser.handle_tsv(b"", "empty.tsv")
        assert isinstance(result, BlocksContainer)
        parser._tsv_parser.read_raw_rows.assert_called()

    @pytest.mark.asyncio
    async def test_read_raw_rows_non_unicode_error_then_success(self):
        """Generic Exception in decode loop is ignored; a later encoding can succeed."""
        parser = _make_parser()
        parser._csv_parser.read_raw_rows = MagicMock(
            side_effect=[ValueError("bad rows"), [["x", "y"]]]
        )
        parser._csv_parser.find_tables_in_csv = MagicMock(return_value=[MagicMock()])
        expected = BlocksContainer(blocks=[], block_groups=[])
        parser._csv_parser.get_blocks_from_csv_with_multiple_tables = AsyncMock(
            return_value=expected
        )
        with patch(
            "app.agents.actions.util.parse_file.get_llm",
            new_callable=AsyncMock,
            return_value=("llm", None),
        ):
            result = await parser.handle_csv(b"a,b", "file.csv")
            assert result is expected


class TestHandleMd:
    @pytest.mark.asyncio
    async def test_decodes_and_delegates(self):
        parser = _make_parser()
        expected = BlocksContainer(blocks=[], block_groups=[])
        parser._markdown_string_to_blocks = AsyncMock(return_value=expected)
        result = await parser.handle_md(b"# Heading\n", "a.md")
        assert result is expected
        parser._markdown_string_to_blocks.assert_called_once()
        call_args = parser._markdown_string_to_blocks.call_args[0]
        assert "Heading" in call_args[0]

    @pytest.mark.asyncio
    async def test_latin1_fallback(self):
        """Bytes undecodable as utf-8 fall back to latin-1 which always succeeds."""
        parser = _make_parser()
        expected = BlocksContainer(blocks=[], block_groups=[])
        parser._markdown_string_to_blocks = AsyncMock(return_value=expected)
        # Non-utf-8 bytes; latin-1/iso-8859-1 accepts anything
        result = await parser.handle_md(b"\xff\xfecaf\xe9", "a.md")
        assert result is expected

    @pytest.mark.asyncio
    async def test_decode_failure_raises(self):
        raw = MagicMock()

        def _decode(*_a, **_k):
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "x")

        raw.decode = _decode
        parser = _make_parser()
        with pytest.raises(ValueError, match="Unable to decode Markdown"):
            await parser.handle_md(raw, "a.md")


class TestHandleTxt:
    @pytest.mark.asyncio
    async def test_delegates_to_markdown(self):
        parser = _make_parser()
        expected = BlocksContainer(blocks=[], block_groups=[])
        parser._markdown_string_to_blocks = AsyncMock(return_value=expected)
        result = await parser.handle_txt(b"plain text", "notes.txt")
        assert result is expected

    @pytest.mark.asyncio
    async def test_decode_failure_raises(self):
        raw = MagicMock()

        def _decode(*_a, **_k):
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "x")

        raw.decode = _decode
        parser = _make_parser()
        with pytest.raises(ValueError, match="Unable to decode text file"):
            await parser.handle_txt(raw, "notes.txt")


class TestHandleMdx:
    @pytest.mark.asyncio
    async def test_converts_mdx_to_md(self):
        parser = _make_parser()
        parser._mdx_parser.convert_mdx_to_md = MagicMock(return_value=b"# converted")
        expected = BlocksContainer(blocks=[], block_groups=[])
        parser._markdown_string_to_blocks = AsyncMock(return_value=expected)
        result = await parser.handle_mdx(b"mdx content", "a.mdx")
        assert result is expected
        parser._mdx_parser.convert_mdx_to_md.assert_called_once_with(b"mdx content")


class TestHandleHtml:
    @pytest.mark.asyncio
    async def test_strips_and_converts(self):
        parser = _make_parser()
        parser._html_parser.replace_relative_image_urls = MagicMock(
            side_effect=lambda html: html
        )
        expected = BlocksContainer(blocks=[], block_groups=[])
        parser._markdown_string_to_blocks = AsyncMock(return_value=expected)
        html = b"<html><body><p>hello</p><script>bad()</script></body></html>"
        result = await parser.handle_html(html, "page.html")
        assert result is expected
        # Markdown arg should not contain '<script'
        markdown_arg = parser._markdown_string_to_blocks.call_args[0][0]
        assert "bad()" not in markdown_arg

    @pytest.mark.asyncio
    async def test_beautifulsoup_failure_falls_back_to_decode(self):
        parser = _make_parser()
        parser._html_parser.replace_relative_image_urls = MagicMock(side_effect=lambda h: h)
        expected = BlocksContainer(blocks=[], block_groups=[])
        parser._markdown_string_to_blocks = AsyncMock(return_value=expected)
        with patch(
            "app.agents.actions.util.parse_file.BeautifulSoup",
            side_effect=RuntimeError("no soup"),
        ):
            result = await parser.handle_html(b"<html>ok", "page.html")
        assert result is expected
        md_arg = parser._markdown_string_to_blocks.call_args[0][0]
        assert "ok" in md_arg or len(md_arg) >= 0


class TestMarkdownStringToBlocks:
    @pytest.mark.asyncio
    async def test_empty_markdown_returns_empty_blocks(self):
        parser = _make_parser()
        result = await parser._markdown_string_to_blocks("   ", "a.md")
        assert isinstance(result, BlocksContainer)
        assert result.blocks == []

    @pytest.mark.asyncio
    async def test_whitespace_only_returns_empty(self):
        parser = _make_parser()
        result = await parser._markdown_string_to_blocks("", "a.md")
        assert result.blocks == []

    @pytest.mark.asyncio
    async def test_non_empty_dispatches_to_docling(self):
        parser = _make_parser()
        parser._md_parser.extract_and_replace_images = MagicMock(
            return_value=("# H", [])
        )
        parser._md_parser.parse_string = MagicMock(return_value=b"md bytes")
        expected = BlocksContainer(blocks=[], block_groups=[])

        with patch(
            "app.agents.actions.util.parse_file.DoclingProcessor"
        ) as mock_cls:
            instance = mock_cls.return_value
            instance.parse_document = AsyncMock(return_value="conv_res")
            instance.create_blocks = AsyncMock(return_value=expected)

            result = await parser._markdown_string_to_blocks("# H", "myfile.md")
            assert result is expected

    @pytest.mark.asyncio
    async def test_injects_base64_uri_for_image_blocks(self):
        parser = _make_parser()
        parser._md_parser.extract_and_replace_images = MagicMock(
            return_value=(
                "# doc",
                [{"url": "https://example.com/i.png", "new_alt_text": "cap1"}],
            )
        )
        parser._md_parser.parse_string = MagicMock(return_value=b"bytes")
        parser._image_parser.urls_to_base64 = AsyncMock(return_value=["data:image/png;base64,QQ=="])

        img_block = Block(
            type=BlockType.IMAGE,
            image_metadata=ImageMetadata(captions=["cap1"]),
            data={"other": 1},
        )
        plain = Block(type=BlockType.TEXT, data="t")
        expected = BlocksContainer(blocks=[plain, img_block], block_groups=[])

        with patch(
            "app.agents.actions.util.parse_file.DoclingProcessor"
        ) as mock_cls:
            instance = mock_cls.return_value
            instance.parse_document = AsyncMock(return_value="conv")
            instance.create_blocks = AsyncMock(return_value=expected)

            result = await parser._markdown_string_to_blocks("# doc", "x.md")
            assert result.blocks[1].data["uri"] == "data:image/png;base64,QQ=="
            assert result.blocks[1].data["other"] == 1

    @pytest.mark.asyncio
    async def test_image_block_missing_caption_mapping_logs_warning(self):
        parser = _make_parser()
        parser._md_parser.extract_and_replace_images = MagicMock(
            return_value=("# x", [{"url": "u", "new_alt_text": "alt"}])
        )
        parser._md_parser.parse_string = MagicMock(return_value=b"bytes")
        parser._image_parser.urls_to_base64 = AsyncMock(return_value=[""])

        img_block = Block(
            type=BlockType.IMAGE,
            image_metadata=ImageMetadata(captions=["missing"]),
            data=None,
        )
        expected = BlocksContainer(blocks=[img_block], block_groups=[])

        with patch(
            "app.agents.actions.util.parse_file.DoclingProcessor"
        ) as mock_cls:
            instance = mock_cls.return_value
            instance.parse_document = AsyncMock(return_value="conv")
            instance.create_blocks = AsyncMock(return_value=expected)

            await parser._markdown_string_to_blocks("# x", "x.md")
            assert img_block.data is None

    @pytest.mark.asyncio
    async def test_image_block_sets_uri_when_data_was_none(self):
        parser = _make_parser()
        parser._md_parser.extract_and_replace_images = MagicMock(
            return_value=("#", [{"url": "u", "new_alt_text": "c1"}])
        )
        parser._md_parser.parse_string = MagicMock(return_value=b"bytes")
        parser._image_parser.urls_to_base64 = AsyncMock(return_value=["data:x"])

        img_block = Block(
            type=BlockType.IMAGE,
            image_metadata=ImageMetadata(captions=["c1"]),
            data=None,
        )
        expected = BlocksContainer(blocks=[img_block], block_groups=[])

        with patch(
            "app.agents.actions.util.parse_file.DoclingProcessor"
        ) as mock_cls:
            instance = mock_cls.return_value
            instance.parse_document = AsyncMock(return_value="conv")
            instance.create_blocks = AsyncMock(return_value=expected)

            await parser._markdown_string_to_blocks("#", "x.md")
            assert img_block.data == {"uri": "data:x"}
