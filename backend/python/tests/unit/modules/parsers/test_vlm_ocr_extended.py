"""
Extended tests for app.modules.parsers.pdf.vlm_ocr_strategy covering missing lines:
- __init__ (lines 86-91)
- _get_multimodal_llm: default not multimodal, fallback to first multimodal
- _get_multimodal_llm: no multimodal LLM found
- _call_llm_for_markdown: generic code block wrapper
- _preprocess_document: retry logic
- _preprocess_document: cancel remaining tasks on failure
- load_document exception
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.parsers.pdf.vlm_ocr_strategy import VLMOCRStrategy


# ============================================================================
# __init__
# ============================================================================


class TestVLMOCRInit:
    def test_init(self):
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)
        assert strategy.config == config
        assert strategy.doc is None
        assert strategy.llm is None
        assert strategy.llm_config is None
        assert strategy.document_analysis_result is None


# ============================================================================
# _get_multimodal_llm
# ============================================================================


class TestGetMultimodalLLM:
    @pytest.mark.asyncio
    async def test_default_multimodal(self):
        logger = logging.getLogger("test")
        config = AsyncMock()
        config.get_config = AsyncMock(return_value={
            "llm": [
                {
                    "provider": "openai",
                    "isDefault": True,
                    "isMultimodal": True,
                    "configuration": {"model": "gpt-4o"},
                }
            ]
        })

        strategy = VLMOCRStrategy(logger, config)
        with patch.object(strategy, "_create_llm_from_config", return_value=MagicMock()) as mock_create:
            llm = await strategy._get_multimodal_llm()
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_default_not_multimodal_fallback(self):
        logger = logging.getLogger("test")
        config = AsyncMock()
        config.get_config = AsyncMock(return_value={
            "llm": [
                {
                    "provider": "openai",
                    "isDefault": True,
                    "isMultimodal": False,
                    "configuration": {"model": "gpt-4"},
                },
                {
                    "provider": "openai",
                    "isDefault": False,
                    "isMultimodal": True,
                    "configuration": {"model": "gpt-4o"},
                },
            ]
        })

        strategy = VLMOCRStrategy(logger, config)
        with patch.object(strategy, "_create_llm_from_config", return_value=MagicMock()) as mock_create:
            llm = await strategy._get_multimodal_llm()
            # Should use the second config (first multimodal)
            call_config = mock_create.call_args[0][0]
            assert call_config["configuration"]["model"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_no_multimodal_raises(self):
        logger = logging.getLogger("test")
        config = AsyncMock()
        config.get_config = AsyncMock(return_value={
            "llm": [
                {
                    "provider": "openai",
                    "isDefault": True,
                    "isMultimodal": False,
                    "configuration": {"model": "gpt-4"},
                },
            ]
        })

        strategy = VLMOCRStrategy(logger, config)
        with pytest.raises(ValueError, match="No multimodal LLM found"):
            await strategy._get_multimodal_llm()

    @pytest.mark.asyncio
    async def test_no_llm_configs_raises(self):
        logger = logging.getLogger("test")
        config = AsyncMock()
        config.get_config = AsyncMock(return_value={"llm": []})

        strategy = VLMOCRStrategy(logger, config)
        with pytest.raises(ValueError, match="No LLM configurations found"):
            await strategy._get_multimodal_llm()


# ============================================================================
# _call_llm_for_markdown - generic code block wrapper
# ============================================================================


class TestCallLLMForMarkdown:
    @pytest.mark.asyncio
    async def test_generic_code_block_stripped(self):
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        mock_response = MagicMock()
        mock_response.content = "```\n# Header\nSome content\n```"

        strategy.llm = AsyncMock()
        strategy.llm.ainvoke = AsyncMock(return_value=mock_response)

        result = await strategy._call_llm_for_markdown("data:image/png;base64,abc", 1)
        assert result == "# Header\nSome content"

    @pytest.mark.asyncio
    async def test_markdown_code_block_stripped(self):
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        mock_response = MagicMock()
        mock_response.content = "```markdown\n# Header\n```"

        strategy.llm = AsyncMock()
        strategy.llm.ainvoke = AsyncMock(return_value=mock_response)

        result = await strategy._call_llm_for_markdown("data:image/png;base64,abc", 1)
        assert result == "# Header"

    @pytest.mark.asyncio
    async def test_llm_exception(self):
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        strategy.llm = AsyncMock()
        strategy.llm.ainvoke = AsyncMock(side_effect=Exception("LLM error"))

        with pytest.raises(Exception, match="LLM error"):
            await strategy._call_llm_for_markdown("data:image/png;base64,abc", 1)


# ============================================================================
# _preprocess_document - retry and cancellation
# ============================================================================


class TestPreprocessDocument:
    @pytest.mark.asyncio
    async def test_retry_on_failure(self):
        """Test retry logic in _preprocess_document."""
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        # Simulate a document with 1 page
        mock_page = MagicMock()
        mock_page.number = 0
        strategy.doc = [mock_page]

        call_count = 0
        async def mock_process_page(page):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Temporary failure")
            return {"page_number": 1, "markdown": "# Page 1", "width": 100, "height": 200}

        with patch.object(strategy, "process_page", side_effect=mock_process_page):
            result = await strategy._preprocess_document()
            assert result["total_pages"] == 1
            assert call_count == 2  # 1 failure + 1 success

    @pytest.mark.asyncio
    async def test_all_retries_fail(self):
        """Test all retries failing."""
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        mock_page = MagicMock()
        mock_page.number = 0
        strategy.doc = [mock_page]

        with patch.object(strategy, "process_page", side_effect=Exception("Persistent failure")):
            with pytest.raises(Exception, match="Persistent failure"):
                await strategy._preprocess_document()


# ============================================================================
# load_document exception
# ============================================================================


class TestLoadDocument:
    @pytest.mark.asyncio
    async def test_load_document_exception(self):
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        with patch("app.modules.parsers.pdf.vlm_ocr_strategy.fitz") as mock_fitz:
            mock_fitz.open.side_effect = Exception("PDF parse error")
            with pytest.raises(Exception, match="PDF parse error"):
                await strategy.load_document(b"fake_pdf_content")
