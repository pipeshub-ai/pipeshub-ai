"""Comprehensive tests for app.modules.parsers.pdf.docling to achieve >97% coverage.

Targets uncovered lines:
- 33-36: _get_local_parse_worker_count() with valid and invalid env var
- 46: _get_process_pool()
- 66-71: _parse_document_in_worker()
- 87-94: multi-worker path in parse_document()
"""

import logging
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_mock_logger():
    return MagicMock(spec=logging.Logger)


def _make_mock_config():
    return {}


def _make_processor():
    """Create a DoclingProcessor with mocked converter."""
    with patch("app.modules.parsers.pdf.docling.DocumentConverter") as MockConverter, \
         patch("app.modules.parsers.pdf.docling.PdfFormatOption"), \
         patch("app.modules.parsers.pdf.docling.WordFormatOption"), \
         patch("app.modules.parsers.pdf.docling.MarkdownFormatOption"), \
         patch("app.modules.parsers.pdf.docling.PdfPipelineOptions"), \
         patch("app.modules.parsers.pdf.docling.PyPdfiumDocumentBackend"):
        from app.modules.parsers.pdf.docling import DoclingProcessor
        processor = DoclingProcessor(_make_mock_logger(), _make_mock_config())
    return processor


# ===========================================================================
# _get_local_parse_worker_count (lines 30-38)
# ===========================================================================
class TestGetLocalParseWorkerCount:
    """Cover _get_local_parse_worker_count with env var variations."""

    def test_valid_env_var_returns_parsed_int(self):
        """When LOCAL_DOCLING_PARSE_WORKERS is a valid positive int, return it."""
        with patch.dict(os.environ, {"LOCAL_DOCLING_PARSE_WORKERS": "4"}):
            from app.modules.parsers.pdf.docling import _get_local_parse_worker_count
            result = _get_local_parse_worker_count()
            assert result == 4

    def test_env_var_zero_returns_one(self):
        """When LOCAL_DOCLING_PARSE_WORKERS is '0', max(1, 0) returns 1."""
        with patch.dict(os.environ, {"LOCAL_DOCLING_PARSE_WORKERS": "0"}):
            from app.modules.parsers.pdf.docling import _get_local_parse_worker_count
            result = _get_local_parse_worker_count()
            assert result == 1

    def test_env_var_negative_returns_one(self):
        """When LOCAL_DOCLING_PARSE_WORKERS is '-3', max(1, -3) returns 1."""
        with patch.dict(os.environ, {"LOCAL_DOCLING_PARSE_WORKERS": "-3"}):
            from app.modules.parsers.pdf.docling import _get_local_parse_worker_count
            result = _get_local_parse_worker_count()
            assert result == 1

    def test_env_var_invalid_returns_one(self):
        """When LOCAL_DOCLING_PARSE_WORKERS is not a number, return 1."""
        with patch.dict(os.environ, {"LOCAL_DOCLING_PARSE_WORKERS": "abc"}):
            from app.modules.parsers.pdf.docling import _get_local_parse_worker_count
            result = _get_local_parse_worker_count()
            assert result == 1

    def test_env_var_empty_returns_one(self):
        """When LOCAL_DOCLING_PARSE_WORKERS is empty string, return 1."""
        with patch.dict(os.environ, {"LOCAL_DOCLING_PARSE_WORKERS": ""}):
            from app.modules.parsers.pdf.docling import _get_local_parse_worker_count
            result = _get_local_parse_worker_count()
            assert result == 1

    def test_env_var_not_set_returns_one(self):
        """When LOCAL_DOCLING_PARSE_WORKERS is not set, return 1."""
        with patch.dict(os.environ, {}, clear=False):
            env = os.environ.copy()
            env.pop("LOCAL_DOCLING_PARSE_WORKERS", None)
            with patch.dict(os.environ, env, clear=True):
                from app.modules.parsers.pdf.docling import _get_local_parse_worker_count
                result = _get_local_parse_worker_count()
                assert result == 1

    def test_env_var_large_value(self):
        """When LOCAL_DOCLING_PARSE_WORKERS is a large valid int, return it."""
        with patch.dict(os.environ, {"LOCAL_DOCLING_PARSE_WORKERS": "16"}):
            from app.modules.parsers.pdf.docling import _get_local_parse_worker_count
            result = _get_local_parse_worker_count()
            assert result == 16


# ===========================================================================
# _get_process_pool (line 46)
# ===========================================================================
class TestGetProcessPool:
    """Cover _get_process_pool function."""

    def test_get_process_pool_returns_executor(self):
        """_get_process_pool returns a ProcessPoolExecutor."""
        with patch("app.modules.parsers.pdf.docling.ProcessPoolExecutor") as MockExecutor, \
             patch("app.modules.parsers.pdf.docling.multiprocessing") as mock_mp:
            mock_ctx = MagicMock()
            mock_mp.get_context.return_value = mock_ctx
            mock_pool = MagicMock(spec=ProcessPoolExecutor)
            MockExecutor.return_value = mock_pool

            from app.modules.parsers.pdf.docling import _get_process_pool
            # Clear lru_cache so our mock takes effect
            _get_process_pool.cache_clear()
            result = _get_process_pool()

            MockExecutor.assert_called_once()
            assert result is mock_pool
            # Clear again so it doesn't affect other tests
            _get_process_pool.cache_clear()


# ===========================================================================
# _parse_document_in_worker (lines 66-71)
# ===========================================================================
class TestParseDocumentInWorker:
    """Cover the _parse_document_in_worker function."""

    def test_success_returns_json(self):
        """On success, returns serialized JSON from conv_res.document."""
        mock_conv_result = MagicMock()
        mock_conv_result.status.value = "success"
        mock_conv_result.document.model_dump_json.return_value = '{"text": "hello"}'

        mock_converter = MagicMock()
        mock_converter.convert.return_value = mock_conv_result

        with patch("app.modules.parsers.pdf.docling._get_converter", return_value=mock_converter), \
             patch("app.modules.parsers.pdf.docling.DocumentStream") as MockStream:
            MockStream.return_value = MagicMock()
            from app.modules.parsers.pdf.docling import _parse_document_in_worker
            result = _parse_document_in_worker("test.pdf", b"pdf content")

        assert result == '{"text": "hello"}'
        mock_converter.convert.assert_called_once()

    def test_failure_raises_valueerror(self):
        """On failure status, raises ValueError."""
        mock_conv_result = MagicMock()
        mock_conv_result.status.value = "error"
        mock_conv_result.status.__str__ = lambda self: "error"

        mock_converter = MagicMock()
        mock_converter.convert.return_value = mock_conv_result

        with patch("app.modules.parsers.pdf.docling._get_converter", return_value=mock_converter), \
             patch("app.modules.parsers.pdf.docling.DocumentStream") as MockStream:
            MockStream.return_value = MagicMock()
            from app.modules.parsers.pdf.docling import _parse_document_in_worker
            with pytest.raises(ValueError, match="Failed to parse document"):
                _parse_document_in_worker("bad.pdf", b"bad content")

    def test_creates_document_stream_correctly(self):
        """Verifies DocumentStream is created with correct name and BytesIO stream."""
        mock_conv_result = MagicMock()
        mock_conv_result.status.value = "success"
        mock_conv_result.document.model_dump_json.return_value = "{}"

        mock_converter = MagicMock()
        mock_converter.convert.return_value = mock_conv_result

        with patch("app.modules.parsers.pdf.docling._get_converter", return_value=mock_converter), \
             patch("app.modules.parsers.pdf.docling.DocumentStream") as MockStream:
            MockStream.return_value = MagicMock()
            from app.modules.parsers.pdf.docling import _parse_document_in_worker
            _parse_document_in_worker("report.pdf", b"content here")

            MockStream.assert_called_once()
            call_kwargs = MockStream.call_args
            assert call_kwargs.kwargs.get("name") == "report.pdf"
            stream_arg = call_kwargs.kwargs.get("stream")
            assert isinstance(stream_arg, BytesIO)
            assert stream_arg.getvalue() == b"content here"


# ===========================================================================
# parse_document - multi-worker path (lines 87-94)
# ===========================================================================
class TestParseDocumentMultiWorker:
    """Cover the multi-worker branch (LOCAL_DOCLING_PARSE_WORKERS > 1)."""

    @pytest.mark.asyncio
    async def test_multi_worker_path_with_bytes(self):
        """When workers > 1, uses run_in_executor with process pool."""
        processor = _make_processor()

        mock_serialized = '{"text": "parsed"}'
        mock_doc = MagicMock()

        with patch("app.modules.parsers.pdf.docling.LOCAL_DOCLING_PARSE_WORKERS", 2), \
             patch("app.modules.parsers.pdf.docling._get_process_pool") as mock_pool, \
             patch("app.modules.parsers.pdf.docling.DoclingDocument") as MockDoclingDoc, \
             patch("asyncio.get_running_loop") as mock_get_loop:

            mock_loop = MagicMock()
            mock_loop.run_in_executor = AsyncMock(return_value=mock_serialized)
            mock_get_loop.return_value = mock_loop
            MockDoclingDoc.model_validate_json.return_value = mock_doc

            result = await processor.parse_document("test.pdf", b"pdf bytes")

            mock_loop.run_in_executor.assert_awaited_once()
            call_args = mock_loop.run_in_executor.call_args
            assert call_args[0][0] is mock_pool()  # pool argument
            assert call_args[0][2] == "test.pdf"   # doc_name
            assert call_args[0][3] == b"pdf bytes"  # content
            MockDoclingDoc.model_validate_json.assert_called_once_with(mock_serialized)
            assert result is mock_doc

    @pytest.mark.asyncio
    async def test_multi_worker_path_with_bytesio(self):
        """When workers > 1 and BytesIO input, extracts bytes via getvalue()."""
        processor = _make_processor()

        mock_serialized = '{"text": "parsed"}'
        mock_doc = MagicMock()

        with patch("app.modules.parsers.pdf.docling.LOCAL_DOCLING_PARSE_WORKERS", 2), \
             patch("app.modules.parsers.pdf.docling._get_process_pool") as mock_pool, \
             patch("app.modules.parsers.pdf.docling.DoclingDocument") as MockDoclingDoc, \
             patch("asyncio.get_running_loop") as mock_get_loop:

            mock_loop = MagicMock()
            mock_loop.run_in_executor = AsyncMock(return_value=mock_serialized)
            mock_get_loop.return_value = mock_loop
            MockDoclingDoc.model_validate_json.return_value = mock_doc

            content = BytesIO(b"bytesio content")
            result = await processor.parse_document("test.pdf", content)

            mock_loop.run_in_executor.assert_awaited_once()
            call_args = mock_loop.run_in_executor.call_args
            # The raw_content should be bytes extracted from BytesIO
            assert call_args[0][3] == b"bytesio content"
            assert result is mock_doc

    @pytest.mark.asyncio
    async def test_multi_worker_path_executor_error_propagates(self):
        """When run_in_executor raises, it propagates to caller."""
        processor = _make_processor()

        with patch("app.modules.parsers.pdf.docling.LOCAL_DOCLING_PARSE_WORKERS", 2), \
             patch("app.modules.parsers.pdf.docling._get_process_pool"), \
             patch("asyncio.get_running_loop") as mock_get_loop:

            mock_loop = MagicMock()
            mock_loop.run_in_executor = AsyncMock(
                side_effect=RuntimeError("Worker crashed")
            )
            mock_get_loop.return_value = mock_loop

            with pytest.raises(RuntimeError, match="Worker crashed"):
                await processor.parse_document("test.pdf", b"content")


# ===========================================================================
# _get_converter (lines 53-62)
# ===========================================================================
class TestGetConverter:
    """Cover the _get_converter cached factory function."""

    def test_get_converter_creates_document_converter(self):
        """_get_converter returns a DocumentConverter instance."""
        with patch("app.modules.parsers.pdf.docling.DocumentConverter") as MockConverter, \
             patch("app.modules.parsers.pdf.docling.PdfPipelineOptions") as MockPipelineOpts, \
             patch("app.modules.parsers.pdf.docling.PyPdfiumDocumentBackend") as MockBackend, \
             patch("app.modules.parsers.pdf.docling.PdfFormatOption") as MockPdfFmt, \
             patch("app.modules.parsers.pdf.docling.WordFormatOption") as MockWordFmt, \
             patch("app.modules.parsers.pdf.docling.MarkdownFormatOption") as MockMdFmt, \
             patch("app.modules.parsers.pdf.docling.InputFormat") as MockInputFmt:

            mock_converter = MagicMock()
            MockConverter.return_value = mock_converter

            from app.modules.parsers.pdf.docling import _get_converter
            _get_converter.cache_clear()
            result = _get_converter()

            MockConverter.assert_called_once()
            assert result is mock_converter
            _get_converter.cache_clear()


# ===========================================================================
# process_document (line 120-121)
# ===========================================================================
class TestProcessDocument:
    """Cover the process_document noop method."""

    def test_process_document_is_noop(self):
        """process_document() does nothing and returns None."""
        processor = _make_processor()
        result = processor.process_document()
        assert result is None
