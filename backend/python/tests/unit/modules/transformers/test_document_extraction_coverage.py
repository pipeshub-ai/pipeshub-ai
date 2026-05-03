"""Additional unit tests for document_extraction.py to push coverage above 97%.

Targets the missing lines/branches:
- Lines 161-162: tiktoken.get_encoding raises exception -> enc = None
- Line 172: enc.encode raises exception -> fallback heuristic
- Lines 248-249: TABLE_ROW token budget exceeded (truncation)
- Lines 323-325: extract_metadata exception propagation (raise inside except)
- Line 354->357: _fallback_summary response without .content attribute
- Lines 378-379: process_document delegates to extract_metadata
"""

import base64
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import Block, BlockType, DataFormat


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_text_block(text, index=0):
    return Block(type=BlockType.TEXT, format=DataFormat.TXT, data=text, index=index)


def _make_image_block(uri, fmt=DataFormat.BASE64, index=0):
    return Block(type=BlockType.IMAGE, format=fmt, data={"uri": uri}, index=index)


def _make_table_row_block(data, index=0):
    return Block(type=BlockType.TABLE_ROW, format=DataFormat.TXT, data=data, index=index)


def _build_extractor():
    from app.modules.transformers.document_extraction import DocumentExtraction

    logger = logging.getLogger("test-doc-extraction-cov")
    graph_provider = MagicMock()
    config_service = MagicMock()
    return DocumentExtraction(logger, graph_provider, config_service)


# =========================================================================
# _prepare_content - tiktoken branch coverage
# =========================================================================


class TestPrepareContentTiktokenBranches:
    """Cover tiktoken import/get_encoding/encode exception branches."""

    def test_tiktoken_get_encoding_raises(self):
        """When tiktoken.get_encoding raises, enc stays None and fallback heuristic is used."""
        ext = _build_extractor()
        blocks = [_make_text_block("Hello world", index=0)]

        # Create a mock tiktoken module where get_encoding raises
        mock_tiktoken = MagicMock()
        mock_tiktoken.get_encoding.side_effect = Exception("encoding not found")

        with patch.dict("sys.modules", {"tiktoken": mock_tiktoken}):
            result = ext._prepare_content(blocks, is_multimodal_llm=False, context_length=128000)

        assert len(result) == 1
        assert result[0]["text"] == "Hello world"

    def test_tiktoken_encode_raises(self):
        """When enc.encode raises, the fallback heuristic (len/4) is used."""
        ext = _build_extractor()
        blocks = [_make_text_block("Hello world", index=0)]

        mock_enc = MagicMock()
        mock_enc.encode.side_effect = Exception("encode failure")

        mock_tiktoken = MagicMock()
        mock_tiktoken.get_encoding.return_value = mock_enc

        with patch.dict("sys.modules", {"tiktoken": mock_tiktoken}):
            result = ext._prepare_content(blocks, is_multimodal_llm=False, context_length=128000)

        assert len(result) == 1

    def test_table_row_token_budget_exceeded(self):
        """TABLE_ROW blocks are also subject to token budget truncation."""
        ext = _build_extractor()

        # With context_length=50, budget ~ 42 tokens
        # First table row ~25 tokens, second ~100 tokens -> exceeds budget
        blocks = [
            _make_table_row_block({"row_natural_language_text": "A" * 100}, index=0),  # ~25 tokens
            _make_table_row_block({"row_natural_language_text": "B" * 400}, index=1),  # ~100 tokens
        ]

        with patch.dict("sys.modules", {"tiktoken": None}):
            result = ext._prepare_content(blocks, is_multimodal_llm=False, context_length=50)

        # Only first table row should fit; second exceeds budget
        assert len(result) == 1
        assert result[0]["text"] == "A" * 100

    def test_count_tokens_with_empty_text(self):
        """count_tokens returns 0 for empty text."""
        ext = _build_extractor()
        # A table row with empty text from dict with missing key
        blocks = [_make_table_row_block({"other": "val"}, index=0)]

        result = ext._prepare_content(blocks, is_multimodal_llm=False, context_length=128000)

        assert len(result) == 1
        assert result[0]["text"] == ""


# =========================================================================
# extract_metadata - exception propagation
# =========================================================================


class TestExtractMetadataExceptionPropagation:
    """Cover lines 323-325: raise inside except block."""

    @pytest.mark.asyncio
    async def test_extract_metadata_graph_provider_error_propagates(self):
        """When graph_provider.get_departments raises, the exception propagates."""
        ext = _build_extractor()

        fake_llm = MagicMock()
        fake_config = {"isMultimodal": False, "contextLength": 128000}

        blocks = [_make_text_block("Some content")]

        with patch(
            "app.modules.transformers.document_extraction.get_llm",
            return_value=(fake_llm, fake_config),
        ), patch.object(ext, "graph_provider") as mock_graph:
            mock_graph.get_departments = AsyncMock(
                side_effect=RuntimeError("graph DB down")
            )

            with pytest.raises(RuntimeError, match="graph DB down"):
                await ext.extract_metadata(blocks, "org-1")

    @pytest.mark.asyncio
    async def test_extract_metadata_invoke_raises_propagates(self):
        """When invoke_with_structured_output_and_reflection raises, exception propagates."""
        ext = _build_extractor()

        fake_llm = MagicMock()
        fake_config = {"isMultimodal": False, "contextLength": 128000}

        blocks = [_make_text_block("Some content")]

        with patch(
            "app.modules.transformers.document_extraction.get_llm",
            return_value=(fake_llm, fake_config),
        ), patch.object(ext, "graph_provider") as mock_graph, patch(
            "app.modules.transformers.document_extraction.invoke_with_structured_output_and_reflection",
            side_effect=ValueError("structured output failed"),
        ):
            mock_graph.get_departments = AsyncMock(return_value=["Engineering"])

            with pytest.raises(ValueError, match="structured output failed"):
                await ext.extract_metadata(blocks, "org-1")


# =========================================================================
# _fallback_summary - response without .content attribute
# =========================================================================


class TestFallbackSummaryBranches:
    """Cover the branch where response has neither .content nor is a string."""

    @pytest.mark.asyncio
    async def test_fallback_response_no_content_no_string(self):
        """Response that is not a string and has no .content returns empty summary -> None."""
        ext = _build_extractor()

        # Create a response without .content attribute and not a string
        mock_response = MagicMock(spec=[])  # no attributes at all

        ext.llm = AsyncMock()
        ext.llm.ainvoke = AsyncMock(return_value=mock_response)

        result = await ext._fallback_summary([{"type": "text", "text": "content"}])

        # summary_text will be "" -> stripped -> empty -> returns None
        assert result is None

    @pytest.mark.asyncio
    async def test_fallback_filters_message_content_types(self):
        """Fallback prompt filters only text and image_url types from message_content."""
        ext = _build_extractor()

        mock_response = MagicMock()
        mock_response.content = "Summary text"

        ext.llm = AsyncMock()
        ext.llm.ainvoke = AsyncMock(return_value=mock_response)

        message_content = [
            {"type": "text", "text": "Document Content: "},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            {"type": "audio", "data": "some audio"},  # should be filtered out
        ]

        result = await ext._fallback_summary(message_content)

        assert result is not None
        assert result.summary == "Summary text"
        # Verify the call included text and image_url but not audio
        call_args = ext.llm.ainvoke.call_args[0][0]
        # The HumanMessage.content list should have 2 prompt items + 2 from message_content
        content_items = call_args[0].content
        types_in_content = [item.get("type") for item in content_items]
        assert "audio" not in types_in_content


# =========================================================================
# process_document
# =========================================================================


class TestProcessDocument:
    """Cover lines 378-379: process_document delegates to extract_metadata."""

    @pytest.mark.asyncio
    async def test_process_document_delegates(self):
        """process_document calls extract_metadata with correct args."""
        ext = _build_extractor()

        from app.modules.transformers.document_extraction import (
            DocumentClassification,
            SubCategories,
        )

        fake_result = DocumentClassification(
            departments=["HR"],
            category="Policy",
            subcategories=SubCategories(level1="a", level2="b", level3="c"),
            languages=["en"],
            sentiment="Neutral",
            confidence_score=0.8,
            topics=["test"],
            summary="Test summary",
        )

        ext.extract_metadata = AsyncMock(return_value=fake_result)

        blocks = [_make_text_block("content")]
        result = await ext.process_document(blocks, "org-1")

        ext.extract_metadata.assert_awaited_once_with(blocks, "org-1")
        assert result is fake_result

    @pytest.mark.asyncio
    async def test_process_document_returns_none_on_extract_none(self):
        """process_document returns None when extract_metadata returns None."""
        ext = _build_extractor()
        ext.extract_metadata = AsyncMock(return_value=None)

        result = await ext.process_document([], "org-1")
        assert result is None


# =========================================================================
# extract_metadata - departments fallback to DepartmentNames
# =========================================================================


class TestExtractMetadataDepartmentsFallback:
    """Cover branch: when departments is empty, fallback to DepartmentNames enum."""

    @pytest.mark.asyncio
    async def test_empty_departments_fallback(self):
        """When graph_provider returns empty departments, use DepartmentNames."""
        from app.modules.transformers.document_extraction import (
            DocumentClassification,
            SubCategories,
        )

        ext = _build_extractor()

        fake_classification = DocumentClassification(
            departments=["Engineering"],
            category="Technical",
            subcategories=SubCategories(level1="SW", level2="", level3=""),
            languages=["English"],
            sentiment="Neutral",
            confidence_score=0.8,
            topics=["test"],
            summary="Test summary",
        )

        fake_llm = MagicMock()
        fake_config = {"isMultimodal": False, "contextLength": None}  # None -> default

        blocks = [_make_text_block("Some content")]

        with patch(
            "app.modules.transformers.document_extraction.get_llm",
            return_value=(fake_llm, fake_config),
        ), patch.object(ext, "graph_provider") as mock_graph, patch(
            "app.modules.transformers.document_extraction.invoke_with_structured_output_and_reflection",
            return_value=fake_classification,
        ):
            # Return empty list to trigger fallback
            mock_graph.get_departments = AsyncMock(return_value=[])

            result = await ext.extract_metadata(blocks, "org-1")

        assert result is not None

    @pytest.mark.asyncio
    async def test_context_length_none_uses_default(self):
        """When contextLength is None, DEFAULT_CONTEXT_LENGTH is used."""
        from app.modules.transformers.document_extraction import (
            DocumentClassification,
            SubCategories,
        )

        ext = _build_extractor()

        fake_classification = DocumentClassification(
            departments=["HR"],
            category="General",
            subcategories=SubCategories(level1="", level2="", level3=""),
            languages=["en"],
            sentiment="Neutral",
            confidence_score=0.5,
            topics=[],
            summary="A summary",
        )

        fake_llm = MagicMock()
        fake_config = {"isMultimodal": False, "contextLength": None}

        blocks = [_make_text_block("Content")]

        with patch(
            "app.modules.transformers.document_extraction.get_llm",
            return_value=(fake_llm, fake_config),
        ), patch.object(ext, "graph_provider") as mock_graph, patch(
            "app.modules.transformers.document_extraction.invoke_with_structured_output_and_reflection",
            return_value=fake_classification,
        ):
            mock_graph.get_departments = AsyncMock(return_value=["HR"])

            result = await ext.extract_metadata(blocks, "org-1")

        assert result is not None
