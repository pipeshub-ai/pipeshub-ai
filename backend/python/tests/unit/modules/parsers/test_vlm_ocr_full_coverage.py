"""
Full coverage tests for app.modules.parsers.pdf.vlm_ocr_strategy.VLMOCRStrategy.

Targets all uncovered lines and branches:
- _create_llm_from_config (lines 95-103): model string parsing
- _render_page_to_base64 (lines 188-204): page rendering and base64 conversion
- process_page (lines 265-284): single page processing
- _preprocess_document: task cancellation of not-done tasks (line 329)
- load_document (lines 358-368): full success path
- _call_llm_for_markdown: response without .content attr, no code block wrapping
- _get_multimodal_llm: default not multimodal with no fallback (branch 152->162)
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.parsers.pdf.vlm_ocr_strategy import VLMOCRStrategy


# ============================================================================
# _create_llm_from_config
# ============================================================================


class TestCreateLLMFromConfig:
    """Cover _create_llm_from_config method (lines 95-103)."""

    def test_create_llm_with_model_string(self):
        """Creates LLM from config with a comma-separated model string."""
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        llm_config = {
            "provider": "openai",
            "configuration": {"model": "gpt-4o, gpt-4o-mini"},
        }

        with patch(
            "app.modules.parsers.pdf.vlm_ocr_strategy.get_generator_model",
            return_value=MagicMock(),
        ) as mock_get:
            result = strategy._create_llm_from_config(llm_config)
            # Should use first model name from the comma-separated list
            mock_get.assert_called_once_with("openai", llm_config, "gpt-4o")

        assert strategy.llm_config is llm_config

    def test_create_llm_with_single_model(self):
        """Creates LLM from config with a single model name."""
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        llm_config = {
            "provider": "anthropic",
            "configuration": {"model": "claude-3-opus"},
        }

        with patch(
            "app.modules.parsers.pdf.vlm_ocr_strategy.get_generator_model",
            return_value=MagicMock(),
        ) as mock_get:
            strategy._create_llm_from_config(llm_config)
            mock_get.assert_called_once_with("anthropic", llm_config, "claude-3-opus")

    def test_create_llm_with_no_model_string(self):
        """Creates LLM from config when model string is absent."""
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        llm_config = {
            "provider": "openai",
            "configuration": {},
        }

        with patch(
            "app.modules.parsers.pdf.vlm_ocr_strategy.get_generator_model",
            return_value=MagicMock(),
        ) as mock_get:
            strategy._create_llm_from_config(llm_config)
            mock_get.assert_called_once_with("openai", llm_config, None)

    def test_create_llm_with_empty_model_string(self):
        """Creates LLM from config when model string is empty."""
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        llm_config = {
            "provider": "openai",
            "configuration": {"model": ""},
        }

        with patch(
            "app.modules.parsers.pdf.vlm_ocr_strategy.get_generator_model",
            return_value=MagicMock(),
        ) as mock_get:
            strategy._create_llm_from_config(llm_config)
            # Empty string is falsy, so model_name should be None
            mock_get.assert_called_once_with("openai", llm_config, None)

    def test_create_llm_with_no_configuration_key(self):
        """Creates LLM from config when configuration key is missing entirely."""
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        llm_config = {
            "provider": "openai",
        }

        with patch(
            "app.modules.parsers.pdf.vlm_ocr_strategy.get_generator_model",
            return_value=MagicMock(),
        ) as mock_get:
            strategy._create_llm_from_config(llm_config)
            mock_get.assert_called_once_with("openai", llm_config, None)

    def test_create_llm_model_string_with_whitespace_entries(self):
        """Model string with empty entries after splitting filters them out."""
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        llm_config = {
            "provider": "openai",
            "configuration": {"model": " , , gpt-4o , "},
        }

        with patch(
            "app.modules.parsers.pdf.vlm_ocr_strategy.get_generator_model",
            return_value=MagicMock(),
        ) as mock_get:
            strategy._create_llm_from_config(llm_config)
            mock_get.assert_called_once_with("openai", llm_config, "gpt-4o")


# ============================================================================
# _render_page_to_base64
# ============================================================================


class TestRenderPageToBase64:
    """Cover _render_page_to_base64 method (lines 188-204)."""

    def test_render_success(self):
        """Renders page to base64 image string."""
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        mock_page = MagicMock()
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"\x89PNG\r\nfake"
        mock_page.get_pixmap.return_value = mock_pix

        with patch("app.modules.parsers.pdf.vlm_ocr_strategy.fitz") as mock_fitz:
            mock_fitz.Matrix.return_value = MagicMock()
            result = strategy._render_page_to_base64(mock_page)

        assert result.startswith("data:image/png;base64,")
        # Verify it contains valid base64
        import base64

        b64_part = result.split(",", 1)[1]
        decoded = base64.b64decode(b64_part)
        assert decoded == b"\x89PNG\r\nfake"

    def test_render_error_raises(self):
        """Rendering error propagates the exception."""
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        mock_page = MagicMock()
        mock_page.get_pixmap.side_effect = RuntimeError("Render failed")

        with patch("app.modules.parsers.pdf.vlm_ocr_strategy.fitz") as mock_fitz:
            mock_fitz.Matrix.return_value = MagicMock()
            with pytest.raises(RuntimeError, match="Render failed"):
                strategy._render_page_to_base64(mock_page)


# ============================================================================
# process_page
# ============================================================================


class TestProcessPage:
    """Cover process_page method (lines 265-284)."""

    @pytest.mark.asyncio
    async def test_process_page_success(self):
        """Successful page processing returns correct dict."""
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        mock_page = MagicMock()
        mock_page.number = 2  # 0-indexed, so page_number = 3
        mock_page.rect.width = 612
        mock_page.rect.height = 792

        with patch.object(
            strategy,
            "_render_page_to_base64",
            return_value="data:image/png;base64,abc",
        ), patch.object(
            strategy,
            "_call_llm_for_markdown",
            new_callable=AsyncMock,
            return_value="# Page 3 content",
        ):
            result = await strategy.process_page(mock_page)

        assert result["page_number"] == 3
        assert result["markdown"] == "# Page 3 content"
        assert result["width"] == 612
        assert result["height"] == 792

    @pytest.mark.asyncio
    async def test_process_page_render_error_raises(self):
        """Error during rendering re-raises the exception."""
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        mock_page = MagicMock()
        mock_page.number = 0

        with patch.object(
            strategy,
            "_render_page_to_base64",
            side_effect=RuntimeError("Render error"),
        ):
            with pytest.raises(RuntimeError, match="Render error"):
                await strategy.process_page(mock_page)

    @pytest.mark.asyncio
    async def test_process_page_llm_error_raises(self):
        """Error during LLM call re-raises the exception."""
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        mock_page = MagicMock()
        mock_page.number = 0

        with patch.object(
            strategy,
            "_render_page_to_base64",
            return_value="data:image/png;base64,abc",
        ), patch.object(
            strategy,
            "_call_llm_for_markdown",
            new_callable=AsyncMock,
            side_effect=Exception("LLM failure"),
        ):
            with pytest.raises(Exception, match="LLM failure"):
                await strategy.process_page(mock_page)


# ============================================================================
# _call_llm_for_markdown: additional coverage
# ============================================================================


class TestCallLLMForMarkdownAdditional:
    """Additional coverage for _call_llm_for_markdown."""

    @pytest.mark.asyncio
    async def test_response_without_content_attr(self):
        """Response without .content attribute falls back to str()."""
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        # Create a simple object that has no .content but has __str__
        class NoContentResponse:
            def __str__(self):
                return "Fallback string response"

        strategy.llm = AsyncMock()
        strategy.llm.ainvoke = AsyncMock(return_value=NoContentResponse())

        result = await strategy._call_llm_for_markdown("data:image/png;base64,abc", 1)
        assert result == "Fallback string response"

    @pytest.mark.asyncio
    async def test_plain_content_no_code_block(self):
        """Content without code block markers is returned as-is."""
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        mock_response = MagicMock()
        mock_response.content = "# Simple Header\n\nParagraph text."

        strategy.llm = AsyncMock()
        strategy.llm.ainvoke = AsyncMock(return_value=mock_response)

        result = await strategy._call_llm_for_markdown("data:image/png;base64,abc", 1)
        assert result == "# Simple Header\n\nParagraph text."

    @pytest.mark.asyncio
    async def test_markdown_block_without_closing_backticks(self):
        """```markdown block without closing backticks strips only the opening."""
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        mock_response = MagicMock()
        mock_response.content = "```markdown\n# Header\nContent without closing"

        strategy.llm = AsyncMock()
        strategy.llm.ainvoke = AsyncMock(return_value=mock_response)

        result = await strategy._call_llm_for_markdown("data:image/png;base64,abc", 1)
        assert result == "# Header\nContent without closing"

    @pytest.mark.asyncio
    async def test_generic_block_without_closing_backticks(self):
        """``` block without closing backticks strips only the opening."""
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        mock_response = MagicMock()
        mock_response.content = "```\n# Header\nContent without closing"

        strategy.llm = AsyncMock()
        strategy.llm.ainvoke = AsyncMock(return_value=mock_response)

        result = await strategy._call_llm_for_markdown("data:image/png;base64,abc", 1)
        assert result == "# Header\nContent without closing"


# ============================================================================
# _preprocess_document: task cancellation
# ============================================================================


class TestPreprocessDocumentCancellation:
    """Cover task cancellation path in _preprocess_document (line 329)."""

    @pytest.mark.asyncio
    async def test_cancel_remaining_tasks_on_failure(self):
        """When one page fails after all retries, remaining tasks are cancelled."""
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        mock_page1 = MagicMock()
        mock_page1.number = 0
        mock_page2 = MagicMock()
        mock_page2.number = 1
        strategy.doc = [mock_page1, mock_page2]

        call_count = 0

        async def failing_process(page):
            nonlocal call_count
            call_count += 1
            if page.number == 0:
                raise Exception("Page 1 always fails")
            # Page 2 would succeed but may be cancelled
            await asyncio.sleep(10)
            return {
                "page_number": 2,
                "markdown": "# Page 2",
                "width": 100,
                "height": 200,
            }

        with patch.object(strategy, "process_page", side_effect=failing_process):
            with pytest.raises(Exception, match="Page 1 always fails"):
                await strategy._preprocess_document()


# ============================================================================
# _get_multimodal_llm: additional edge cases
# ============================================================================


class TestGetMultimodalLLMEdgeCases:
    """Additional coverage for _get_multimodal_llm."""

    @pytest.mark.asyncio
    async def test_default_not_multimodal_no_fallback_available(self):
        """Default LLM is not multimodal and no other multimodal LLM exists."""
        logger = logging.getLogger("test")
        config = AsyncMock()
        config.get_config = AsyncMock(
            return_value={
                "llm": [
                    {
                        "provider": "openai",
                        "isDefault": True,
                        "isMultimodal": False,
                        "configuration": {"model": "gpt-4"},
                    },
                ]
            }
        )

        strategy = VLMOCRStrategy(logger, config)

        with patch(
            "app.modules.parsers.pdf.vlm_ocr_strategy.is_multimodal_llm",
            return_value=False,
        ):
            with pytest.raises(ValueError, match="No multimodal LLM found"):
                await strategy._get_multimodal_llm()

    @pytest.mark.asyncio
    async def test_no_default_but_multimodal_available(self):
        """No default LLM but a multimodal LLM is available."""
        logger = logging.getLogger("test")
        config = AsyncMock()
        config.get_config = AsyncMock(
            return_value={
                "llm": [
                    {
                        "provider": "openai",
                        "isDefault": False,
                        "isMultimodal": True,
                        "configuration": {"model": "gpt-4o"},
                    },
                ]
            }
        )

        strategy = VLMOCRStrategy(logger, config)

        with patch(
            "app.modules.parsers.pdf.vlm_ocr_strategy.is_multimodal_llm",
            return_value=True,
        ), patch.object(
            strategy, "_create_llm_from_config", return_value=MagicMock()
        ) as mock_create:
            result = await strategy._get_multimodal_llm()
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_config_get_raises_exception(self):
        """Exception from config.get_config is wrapped in ValueError."""
        logger = logging.getLogger("test")
        config = AsyncMock()
        config.get_config = AsyncMock(side_effect=RuntimeError("Config service down"))

        strategy = VLMOCRStrategy(logger, config)

        with pytest.raises(ValueError, match="Failed to get multimodal LLM"):
            await strategy._get_multimodal_llm()


# ============================================================================
# load_document: full success path
# ============================================================================


class TestLoadDocumentSuccess:
    """Cover load_document success path (lines 358-368)."""

    @pytest.mark.asyncio
    async def test_load_document_full_success(self):
        """Full successful load_document flow: open PDF -> get LLM -> process."""
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        mock_doc = MagicMock()
        mock_doc.__len__ = lambda s: 2

        mock_preprocess_result = {
            "pages": [
                {"page_number": 1, "markdown": "# Page 1", "width": 612, "height": 792},
                {"page_number": 2, "markdown": "# Page 2", "width": 612, "height": 792},
            ],
            "markdown": "# Page 1\n\n---\n\n# Page 2",
            "total_pages": 2,
        }

        with patch("app.modules.parsers.pdf.vlm_ocr_strategy.fitz") as mock_fitz:
            mock_fitz.open.return_value = mock_doc

            with patch.object(
                strategy,
                "_get_multimodal_llm",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ) as mock_get_llm, patch.object(
                strategy,
                "_preprocess_document",
                new_callable=AsyncMock,
                return_value=mock_preprocess_result,
            ) as mock_preprocess:
                await strategy.load_document(b"fake pdf content")

        assert strategy.doc is mock_doc
        assert strategy.llm is not None
        assert strategy.document_analysis_result is mock_preprocess_result
        mock_get_llm.assert_called_once()
        mock_preprocess.assert_called_once()


# ============================================================================
# _preprocess_document: success path
# ============================================================================


class TestPreprocessDocumentSuccess:
    """Cover _preprocess_document success path with multiple pages."""

    @pytest.mark.asyncio
    async def test_preprocess_multiple_pages(self):
        """Processes multiple pages and joins markdown with separator."""
        logger = logging.getLogger("test")
        config = MagicMock()
        strategy = VLMOCRStrategy(logger, config)

        mock_page1 = MagicMock()
        mock_page1.number = 0
        mock_page2 = MagicMock()
        mock_page2.number = 1
        strategy.doc = [mock_page1, mock_page2]

        async def mock_process(page):
            num = page.number + 1
            return {
                "page_number": num,
                "markdown": f"# Page {num}",
                "width": 612,
                "height": 792,
            }

        with patch.object(strategy, "process_page", side_effect=mock_process):
            result = await strategy._preprocess_document()

        assert result["total_pages"] == 2
        assert len(result["pages"]) == 2
        assert "# Page 1" in result["markdown"]
        assert "# Page 2" in result["markdown"]
        assert "---" in result["markdown"]
