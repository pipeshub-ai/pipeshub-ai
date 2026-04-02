"""Unit tests for app.modules.transformers.document_extraction.

Covers:
- _downscale_base64_image
- _prepare_content (via DocumentExtraction instance)
"""

import base64
import io
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import Block, BlockType, DataFormat

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_text_block(text, index=0):
    return Block(
        type=BlockType.TEXT,
        format=DataFormat.TXT,
        data=text,
        index=index,
    )


def _make_image_block(uri, fmt=DataFormat.BASE64, index=0):
    return Block(
        type=BlockType.IMAGE,
        format=fmt,
        data={"uri": uri},
        index=index,
    )


def _make_table_row_block(data, index=0):
    return Block(
        type=BlockType.TABLE_ROW,
        format=DataFormat.TXT,
        data=data,
        index=index,
    )


def _build_extractor():
    """Build a DocumentExtraction instance with mocked dependencies."""
    from app.modules.transformers.document_extraction import DocumentExtraction

    logger = logging.getLogger("test-doc-extraction")
    graph_provider = MagicMock()
    config_service = MagicMock()
    return DocumentExtraction(logger, graph_provider, config_service)


def _call_downscale(data_uri, max_dim=2000):
    from app.modules.transformers.document_extraction import (
        _downscale_base64_image,
    )
    return _downscale_base64_image(data_uri, max_dim)


# =========================================================================
# _downscale_base64_image
# =========================================================================
class TestDownscaleBase64Image:
    """Tests for the module-level _downscale_base64_image function."""

    def test_pillow_not_installed_returns_none(self):
        """When PIL cannot be imported, the function returns None."""
        import builtins
        real_import = builtins.__import__

        def _blocked_import(name, *args, **kwargs):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("mocked: no PIL")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_blocked_import):
            result = _call_downscale("data:image/png;base64,AAAA")

        assert result is None

    def test_small_image_returned_unchanged(self):
        """An image that fits within max_dim is returned as-is."""
        fake_img = MagicMock()
        fake_img.size = (100, 200)

        with patch("PIL.Image.open", return_value=fake_img):
            raw_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
            b64 = base64.b64encode(raw_bytes).decode()
            uri = f"data:image/png;base64,{b64}"

            result = _call_downscale(uri, max_dim=2000)

        assert result == uri

    def test_large_image_gets_resized(self):
        """An image exceeding max_dim is downscaled."""
        fake_img = MagicMock()
        fake_img.size = (4000, 3000)
        fake_img.mode = "RGB"

        resized_img = MagicMock()
        fake_img.resize.return_value = resized_img

        def mock_save(buffer, format, **kwargs):
            buffer.write(b"resized-data")

        resized_img.save = mock_save

        with patch("PIL.Image.open", return_value=fake_img), \
             patch("PIL.Image.LANCZOS", 1):
            raw_bytes = b"\x89PNG" + b"\x00" * 20
            b64 = base64.b64encode(raw_bytes).decode()
            uri = f"data:image/png;base64,{b64}"

            result = _call_downscale(uri, max_dim=2000)

        assert result is not None
        assert result.startswith("data:image/png;base64,")
        assert result != uri

    def test_jpeg_rgba_converted_to_rgb(self):
        """JPEG images with RGBA mode must be converted to RGB before save."""
        fake_img = MagicMock()
        fake_img.size = (5000, 3000)
        fake_img.mode = "RGBA"

        converted_img = MagicMock()
        converted_img.size = (5000, 3000)
        fake_img.convert.return_value = converted_img

        resized_img = MagicMock()
        converted_img.resize.return_value = resized_img

        def mock_save(buffer, format, **kwargs):
            assert format == "JPEG"
            assert kwargs.get("quality") == 85
            buffer.write(b"jpeg-data")

        resized_img.save = mock_save

        with patch("PIL.Image.open", return_value=fake_img), \
             patch("PIL.Image.LANCZOS", 1):
            raw = b"\xff\xd8\xff" + b"\x00" * 20
            b64 = base64.b64encode(raw).decode()
            uri = f"data:image/jpeg;base64,{b64}"

            result = _call_downscale(uri, max_dim=2000)

        fake_img.convert.assert_called_once_with("RGB")
        assert result is not None
        assert result.startswith("data:image/jpeg;base64,")

    def test_jpeg_palette_mode_converted(self):
        """JPEG images with P (palette) mode must be converted to RGB."""
        fake_img = MagicMock()
        fake_img.size = (3000, 3000)
        fake_img.mode = "P"

        converted_img = MagicMock()
        fake_img.convert.return_value = converted_img
        resized_img = MagicMock()
        converted_img.resize.return_value = resized_img

        def mock_save(buffer, format, **kwargs):
            buffer.write(b"data")

        resized_img.save = mock_save

        with patch("PIL.Image.open", return_value=fake_img), \
             patch("PIL.Image.LANCZOS", 1):
            raw = b"\xff\xd8\xff" + b"\x00" * 20
            b64 = base64.b64encode(raw).decode()
            uri = f"data:image/jpeg;base64,{b64}"

            result = _call_downscale(uri, max_dim=2000)

        fake_img.convert.assert_called_once_with("RGB")
        assert result is not None

    def test_jpeg_la_mode_converted(self):
        """JPEG images with LA mode must be converted to RGB."""
        fake_img = MagicMock()
        fake_img.size = (3000, 3000)
        fake_img.mode = "LA"

        converted_img = MagicMock()
        fake_img.convert.return_value = converted_img
        resized_img = MagicMock()
        converted_img.resize.return_value = resized_img

        def mock_save(buffer, format, **kwargs):
            buffer.write(b"data")

        resized_img.save = mock_save

        with patch("PIL.Image.open", return_value=fake_img), \
             patch("PIL.Image.LANCZOS", 1):
            raw = b"\xff\xd8\xff" + b"\x00" * 20
            b64 = base64.b64encode(raw).decode()
            uri = f"data:image/jpeg;base64,{b64}"

            result = _call_downscale(uri, max_dim=2000)

        fake_img.convert.assert_called_once_with("RGB")

    def test_png_no_quality_kwarg(self):
        """PNG saves should not pass quality kwarg."""
        fake_img = MagicMock()
        fake_img.size = (4000, 3000)
        fake_img.mode = "RGB"

        resized_img = MagicMock()
        fake_img.resize.return_value = resized_img

        saved_kwargs = {}

        def mock_save(buffer, format, **kwargs):
            saved_kwargs.update(kwargs)
            buffer.write(b"png-data")

        resized_img.save = mock_save

        with patch("PIL.Image.open", return_value=fake_img), \
             patch("PIL.Image.LANCZOS", 1):
            raw = b"\x89PNG" + b"\x00" * 20
            b64 = base64.b64encode(raw).decode()
            uri = f"data:image/png;base64,{b64}"

            _call_downscale(uri, max_dim=2000)

        assert "quality" not in saved_kwargs

    def test_unsupported_mime_returns_none(self):
        """Unsupported MIME types return None."""
        raw = b"\x00" * 20
        b64 = base64.b64encode(raw).decode()
        uri = f"data:image/bmp;base64,{b64}"

        result = _call_downscale(uri)

        assert result is None

    def test_corrupt_base64_returns_none(self):
        """Corrupted base64 data returns None instead of raising."""
        uri = "data:image/png;base64,NOT_VALID_BASE64!!!"

        result = _call_downscale(uri)

        assert result is None

    def test_missing_comma_in_data_uri_returns_none(self):
        """A data URI without a comma separator returns None."""
        uri = "data:image/png;base64AAAA"

        result = _call_downscale(uri)

        assert result is None


# =========================================================================
# _prepare_content
# =========================================================================
class TestPrepareContent:
    """Tests for DocumentExtraction._prepare_content."""

    def test_text_blocks_added(self):
        ext = _build_extractor()
        blocks = [_make_text_block("Hello world", index=0)]

        result = ext._prepare_content(blocks, is_multimodal_llm=False, context_length=128000)

        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert result[0]["text"] == "Hello world"

    def test_empty_text_data_skipped(self):
        ext = _build_extractor()
        blocks = [Block(type=BlockType.TEXT, format=DataFormat.TXT, data=None, index=0)]

        result = ext._prepare_content(blocks, is_multimodal_llm=False, context_length=128000)

        assert len(result) == 0

    def test_image_blocks_skipped_when_not_multimodal(self):
        ext = _build_extractor()
        blocks = [
            _make_image_block("data:image/png;base64,AAAA", index=0),
        ]

        result = ext._prepare_content(blocks, is_multimodal_llm=False, context_length=128000)

        assert len(result) == 0

    def test_image_blocks_added_when_multimodal(self):
        ext = _build_extractor()

        raw = b"\x89PNG" + b"\x00" * 20
        b64 = base64.b64encode(raw).decode()
        uri = f"data:image/png;base64,{b64}"
        blocks = [_make_image_block(uri, index=0)]

        with patch(
            "app.modules.transformers.document_extraction._downscale_base64_image",
            return_value=uri,
        ):
            result = ext._prepare_content(blocks, is_multimodal_llm=True, context_length=128000)

        assert len(result) == 1
        assert result[0]["type"] == "image_url"
        assert result[0]["image_url"]["url"] == uri

    def test_image_cap_at_50(self):
        ext = _build_extractor()

        raw = b"\x89PNG" + b"\x00" * 20
        b64 = base64.b64encode(raw).decode()
        uri = f"data:image/png;base64,{b64}"

        blocks = [_make_image_block(uri, index=i) for i in range(60)]

        with patch(
            "app.modules.transformers.document_extraction._downscale_base64_image",
            return_value=uri,
        ):
            result = ext._prepare_content(blocks, is_multimodal_llm=True, context_length=128000)

        image_items = [r for r in result if r["type"] == "image_url"]
        assert len(image_items) == 50

    def test_token_budget_truncates(self):
        """Content is truncated once the token budget is exceeded."""
        ext = _build_extractor()

        # Use a small context length so the budget is low
        # context_length=100 => MAX_TOKENS = 85
        # With the fallback heuristic (~4 chars/token), a 400-char string
        # would be ~100 tokens, exceeding the 85 budget.
        blocks = [
            _make_text_block("A" * 100, index=0),  # ~25 tokens
            _make_text_block("B" * 100, index=1),  # ~25 tokens
            _make_text_block("C" * 100, index=2),  # ~25 tokens
            _make_text_block("D" * 400, index=3),  # ~100 tokens -- would exceed budget
        ]

        with patch.dict("sys.modules", {"tiktoken": None}):
            result = ext._prepare_content(blocks, is_multimodal_llm=False, context_length=100)

        # The first three should fit; the fourth should be truncated away
        assert len(result) == 3

    def test_http_url_image_passed_through(self):
        ext = _build_extractor()
        blocks = [_make_image_block("https://example.com/image.png", index=0)]

        result = ext._prepare_content(blocks, is_multimodal_llm=True, context_length=128000)

        assert len(result) == 1
        assert result[0]["image_url"]["url"] == "https://example.com/image.png"

    def test_http_url_image_no_downscale(self):
        """Remote URLs should not go through _downscale_base64_image."""
        ext = _build_extractor()
        blocks = [_make_image_block("http://example.com/img.jpg", index=0)]

        with patch(
            "app.modules.transformers.document_extraction._downscale_base64_image",
        ) as mock_ds:
            result = ext._prepare_content(blocks, is_multimodal_llm=True, context_length=128000)

        mock_ds.assert_not_called()
        assert len(result) == 1

    def test_unsupported_image_format_skipped(self):
        ext = _build_extractor()
        blocks = [_make_image_block("data:image/tiff;base64,AAAA", index=0)]

        result = ext._prepare_content(blocks, is_multimodal_llm=True, context_length=128000)

        assert len(result) == 0

    def test_downscale_failure_skips_image(self):
        ext = _build_extractor()

        raw = b"\x89PNG" + b"\x00" * 20
        b64 = base64.b64encode(raw).decode()
        uri = f"data:image/png;base64,{b64}"
        blocks = [_make_image_block(uri, index=0)]

        with patch(
            "app.modules.transformers.document_extraction._downscale_base64_image",
            return_value=None,
        ):
            result = ext._prepare_content(blocks, is_multimodal_llm=True, context_length=128000)

        assert len(result) == 0

    def test_table_row_block_dict_data(self):
        ext = _build_extractor()
        blocks = [
            _make_table_row_block(
                {"row_natural_language_text": "Row 1 data"}, index=0
            )
        ]

        result = ext._prepare_content(blocks, is_multimodal_llm=False, context_length=128000)

        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert result[0]["text"] == "Row 1 data"

    def test_table_row_block_non_dict_data(self):
        ext = _build_extractor()
        blocks = [_make_table_row_block("plain text row", index=0)]

        result = ext._prepare_content(blocks, is_multimodal_llm=False, context_length=128000)

        assert len(result) == 1
        assert result[0]["text"] == "plain text row"

    def test_image_without_uri_skipped(self):
        ext = _build_extractor()
        blocks = [
            Block(
                type=BlockType.IMAGE,
                format=DataFormat.BASE64,
                data={"uri": None},
                index=0,
            )
        ]

        result = ext._prepare_content(blocks, is_multimodal_llm=True, context_length=128000)

        assert len(result) == 0

    def test_image_with_no_data_skipped(self):
        ext = _build_extractor()
        blocks = [
            Block(
                type=BlockType.IMAGE,
                format=DataFormat.BASE64,
                data=None,
                index=0,
            )
        ]

        result = ext._prepare_content(blocks, is_multimodal_llm=True, context_length=128000)

        assert len(result) == 0

    def test_image_non_base64_format_skipped(self):
        ext = _build_extractor()
        blocks = [
            Block(
                type=BlockType.IMAGE,
                format=DataFormat.TXT,
                data={"uri": "data:image/png;base64,AAAA"},
                index=0,
            )
        ]

        result = ext._prepare_content(blocks, is_multimodal_llm=True, context_length=128000)

        assert len(result) == 0

    def test_invalid_image_url_format_skipped(self):
        ext = _build_extractor()
        blocks = [_make_image_block("ftp://invalid/image.png", index=0)]

        result = ext._prepare_content(blocks, is_multimodal_llm=True, context_length=128000)

        assert len(result) == 0

    def test_mixed_blocks(self):
        ext = _build_extractor()

        raw = b"\x89PNG" + b"\x00" * 20
        b64 = base64.b64encode(raw).decode()
        uri = f"data:image/png;base64,{b64}"

        blocks = [
            _make_text_block("Text 1", index=0),
            _make_image_block(uri, index=1),
            _make_text_block("Text 2", index=2),
            _make_table_row_block({"row_natural_language_text": "Row"}, index=3),
        ]

        with patch(
            "app.modules.transformers.document_extraction._downscale_base64_image",
            return_value=uri,
        ):
            result = ext._prepare_content(blocks, is_multimodal_llm=True, context_length=128000)

        assert len(result) == 4
        types = [r["type"] for r in result]
        assert types == ["text", "image_url", "text", "text"]

    def test_empty_blocks_list(self):
        ext = _build_extractor()

        result = ext._prepare_content([], is_multimodal_llm=True, context_length=128000)

        assert result == []

    def test_table_row_with_none_data_skipped(self):
        ext = _build_extractor()
        blocks = [_make_table_row_block(None, index=0)]

        result = ext._prepare_content(blocks, is_multimodal_llm=False, context_length=128000)

        assert len(result) == 0


# =========================================================================
# extract_metadata
# =========================================================================
class TestExtractMetadata:
    """Tests for DocumentExtraction.extract_metadata."""

    @pytest.mark.asyncio
    async def test_extract_metadata_success_with_structured_output(self):
        """When structured LLM output succeeds, return the classification."""
        from app.modules.transformers.document_extraction import (
            DocumentClassification,
            DocumentExtraction,
            SubCategories,
        )

        ext = _build_extractor()

        fake_classification = DocumentClassification(
            departments=["Engineering"],
            category="Technical",
            subcategories=SubCategories(level1="Software", level2="Backend", level3="API"),
            languages=["English"],
            sentiment="Positive",
            confidence_score=0.95,
            topics=["microservices"],
            summary="A document about microservices.",
        )

        fake_llm = MagicMock()
        fake_config = {"isMultimodal": False, "contextLength": 128000}

        blocks = [_make_text_block("Hello world about microservices")]

        with patch(
            "app.modules.transformers.document_extraction.get_llm",
            return_value=(fake_llm, fake_config),
        ), patch.object(
            ext, "graph_provider"
        ) as mock_graph, patch(
            "app.modules.transformers.document_extraction.invoke_with_structured_output_and_reflection",
            return_value=fake_classification,
        ):
            mock_graph.get_departments = AsyncMock(return_value=["Engineering"])

            result = await ext.extract_metadata(blocks, "org-1")

        assert result is not None
        assert result.departments == ["Engineering"]
        assert result.summary == "A document about microservices."

    @pytest.mark.asyncio
    async def test_extract_metadata_structured_fails_falls_back(self):
        """When structured output returns None, falls back to _fallback_summary."""
        from unittest.mock import AsyncMock

        from app.modules.transformers.document_extraction import (
            DocumentClassification,
            DocumentExtraction,
            SubCategories,
        )

        ext = _build_extractor()

        fallback_result = DocumentClassification(
            departments=[],
            category="",
            subcategories=SubCategories(level1="", level2="", level3=""),
            languages=[],
            sentiment="Neutral",
            confidence_score=0.0,
            topics=[],
            summary="Fallback summary text",
        )

        fake_llm = MagicMock()
        fake_config = {"isMultimodal": False, "contextLength": 128000}

        blocks = [_make_text_block("Some content")]

        with patch(
            "app.modules.transformers.document_extraction.get_llm",
            return_value=(fake_llm, fake_config),
        ), patch.object(
            ext, "graph_provider"
        ) as mock_graph, patch(
            "app.modules.transformers.document_extraction.invoke_with_structured_output_and_reflection",
            return_value=None,
        ), patch.object(
            ext, "_fallback_summary", new_callable=AsyncMock, return_value=fallback_result
        ) as mock_fallback:
            mock_graph.get_departments = AsyncMock(return_value=[])

            result = await ext.extract_metadata(blocks, "org-1")

        assert result is not None
        assert result.summary == "Fallback summary text"
        mock_fallback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_extract_metadata_empty_content_returns_none(self):
        """When no blocks produce content, returns None."""
        from unittest.mock import AsyncMock

        ext = _build_extractor()

        fake_llm = MagicMock()
        fake_config = {"isMultimodal": False, "contextLength": 128000}

        blocks = []  # empty

        with patch(
            "app.modules.transformers.document_extraction.get_llm",
            return_value=(fake_llm, fake_config),
        ), patch.object(
            ext, "graph_provider"
        ) as mock_graph:
            mock_graph.get_departments = AsyncMock(return_value=["Dept"])

            result = await ext.extract_metadata(blocks, "org-1")

        assert result is None


# =========================================================================
# _fallback_summary
# =========================================================================
class TestFallbackSummary:
    """Tests for DocumentExtraction._fallback_summary."""

    @pytest.mark.asyncio
    async def test_fallback_success(self):
        from unittest.mock import AsyncMock

        ext = _build_extractor()

        mock_response = MagicMock()
        mock_response.content = "This is a summary of the doc."

        ext.llm = AsyncMock()
        ext.llm.ainvoke = AsyncMock(return_value=mock_response)

        message_content = [
            {"type": "text", "text": "Document Content: "},
            {"type": "text", "text": "Hello world document."},
        ]

        result = await ext._fallback_summary(message_content)

        assert result is not None
        assert result.summary == "This is a summary of the doc."
        assert result.sentiment == "Neutral"
        assert result.confidence_score == 0.0

    @pytest.mark.asyncio
    async def test_fallback_empty_response_returns_none(self):
        from unittest.mock import AsyncMock

        ext = _build_extractor()

        mock_response = MagicMock()
        mock_response.content = ""

        ext.llm = AsyncMock()
        ext.llm.ainvoke = AsyncMock(return_value=mock_response)

        result = await ext._fallback_summary([{"type": "text", "text": "content"}])

        assert result is None

    @pytest.mark.asyncio
    async def test_fallback_exception_returns_none(self):
        from unittest.mock import AsyncMock

        ext = _build_extractor()

        ext.llm = AsyncMock()
        ext.llm.ainvoke = AsyncMock(side_effect=Exception("LLM failure"))

        result = await ext._fallback_summary([{"type": "text", "text": "content"}])

        assert result is None

    @pytest.mark.asyncio
    async def test_fallback_string_response(self):
        from unittest.mock import AsyncMock

        ext = _build_extractor()

        ext.llm = AsyncMock()
        ext.llm.ainvoke = AsyncMock(return_value="Plain string summary")

        result = await ext._fallback_summary([{"type": "text", "text": "content"}])

        assert result is not None
        assert result.summary == "Plain string summary"


# =========================================================================
# apply
# =========================================================================
class TestDocumentExtractionApply:
    """Tests for DocumentExtraction.apply."""

    @pytest.mark.asyncio
    async def test_apply_success_sets_semantic_metadata(self):
        from unittest.mock import AsyncMock

        from app.modules.transformers.document_extraction import (
            DocumentClassification,
            SubCategories,
        )

        ext = _build_extractor()

        fake_classification = DocumentClassification(
            departments=["HR"],
            category="Policy",
            subcategories=SubCategories(level1="Benefits", level2="Health", level3="Insurance"),
            languages=["English"],
            sentiment="Neutral",
            confidence_score=0.85,
            topics=["benefits"],
            summary="A benefits policy document.",
        )

        ext.process_document = AsyncMock(return_value=fake_classification)

        record = MagicMock()
        record.block_containers.blocks = [_make_text_block("content")]
        record.org_id = "org-123"
        record.semantic_metadata = None

        ctx = MagicMock()
        ctx.record = record

        await ext.apply(ctx)

        assert record.semantic_metadata is not None
        assert record.semantic_metadata.departments == ["HR"]
        assert record.semantic_metadata.summary == "A benefits policy document."

    @pytest.mark.asyncio
    async def test_apply_extraction_returns_none(self):
        from unittest.mock import AsyncMock

        ext = _build_extractor()
        ext.process_document = AsyncMock(return_value=None)

        record = MagicMock()
        record.block_containers.blocks = []
        record.org_id = "org-123"

        ctx = MagicMock()
        ctx.record = record

        await ext.apply(ctx)

        assert record.semantic_metadata is None


# =========================================================================
# _prepare_content — deeper: image downscaling, token budget, TABLE_ROW
# =========================================================================
class TestPrepareContentDeeper:
    """Deeper tests for _prepare_content covering remaining branches."""

    def test_image_downscale_called_for_base64(self):
        """Downscale function is called for base64 images."""
        ext = _build_extractor()

        raw = b"\x89PNG" + b"\x00" * 20
        b64 = base64.b64encode(raw).decode()
        uri = f"data:image/png;base64,{b64}"
        blocks = [_make_image_block(uri, index=0)]

        with patch(
            "app.modules.transformers.document_extraction._downscale_base64_image",
            return_value=uri,
        ) as mock_ds:
            ext._prepare_content(blocks, is_multimodal_llm=True, context_length=128000)
            mock_ds.assert_called_once()

    def test_token_budget_exhaustion_stops_processing(self):
        """When token budget is exceeded, remaining blocks are skipped."""
        ext = _build_extractor()

        # With context_length=50, budget ~ 42 tokens
        # Each block of 200 chars ~50 tokens -> first fits, second exceeds
        blocks = [
            _make_text_block("A" * 160, index=0),  # ~40 tokens
            _make_text_block("B" * 200, index=1),  # ~50 tokens -> exceeds
        ]

        with patch.dict("sys.modules", {"tiktoken": None}):
            result = ext._prepare_content(blocks, is_multimodal_llm=False, context_length=50)

        # Only first block should fit
        assert len(result) == 1

    def test_table_row_dict_with_missing_key(self):
        """TABLE_ROW with dict data missing row_natural_language_text yields empty text."""
        ext = _build_extractor()
        blocks = [_make_table_row_block({"other_key": "value"}, index=0)]

        result = ext._prepare_content(blocks, is_multimodal_llm=False, context_length=128000)

        # The dict.get("row_natural_language_text") returns None, which is falsy,
        # so the text becomes "" (empty string)
        assert len(result) == 1
        assert result[0]["text"] == ""

    def test_multiple_images_at_cap(self):
        """Exactly 50 images are allowed."""
        ext = _build_extractor()

        raw = b"\x89PNG" + b"\x00" * 20
        b64 = base64.b64encode(raw).decode()
        uri = f"data:image/png;base64,{b64}"

        blocks = [_make_image_block(uri, index=i) for i in range(50)]

        with patch(
            "app.modules.transformers.document_extraction._downscale_base64_image",
            return_value=uri,
        ):
            result = ext._prepare_content(blocks, is_multimodal_llm=True, context_length=128000)

        image_items = [r for r in result if r["type"] == "image_url"]
        assert len(image_items) == 50

    def test_image_jpeg_format_accepted(self):
        """JPEG images are accepted."""
        ext = _build_extractor()
        raw = b"\xff\xd8\xff" + b"\x00" * 20
        b64 = base64.b64encode(raw).decode()
        uri = f"data:image/jpeg;base64,{b64}"
        blocks = [_make_image_block(uri, index=0)]

        with patch(
            "app.modules.transformers.document_extraction._downscale_base64_image",
            return_value=uri,
        ):
            result = ext._prepare_content(blocks, is_multimodal_llm=True, context_length=128000)

        assert len(result) == 1
        assert result[0]["type"] == "image_url"

    def test_mixed_text_table_row_image(self):
        """Mixed text, table_row, and image blocks are all processed."""
        ext = _build_extractor()

        raw = b"\x89PNG" + b"\x00" * 20
        b64 = base64.b64encode(raw).decode()
        uri = f"data:image/png;base64,{b64}"

        blocks = [
            _make_text_block("First paragraph", index=0),
            _make_table_row_block({"row_natural_language_text": "Row data"}, index=1),
            _make_image_block(uri, index=2),
            _make_text_block("Second paragraph", index=3),
        ]

        with patch(
            "app.modules.transformers.document_extraction._downscale_base64_image",
            return_value=uri,
        ):
            result = ext._prepare_content(blocks, is_multimodal_llm=True, context_length=128000)

        assert len(result) == 4
        types = [r["type"] for r in result]
        assert types == ["text", "text", "image_url", "text"]


# =========================================================================
# extract_metadata — deeper: multimodal LLM, context length handling
# =========================================================================
class TestExtractMetadataDeeper:
    """Deeper tests for extract_metadata."""

    @pytest.mark.asyncio
    async def test_extract_metadata_with_multimodal_llm(self):
        """When LLM config has isMultimodal=True, images are included."""
        from unittest.mock import AsyncMock

        from app.modules.transformers.document_extraction import (
            DocumentClassification,
            SubCategories,
        )

        ext = _build_extractor()

        fake_classification = DocumentClassification(
            departments=["Engineering"],
            category="Technical",
            subcategories=SubCategories(level1="Software", level2="", level3=""),
            languages=["English"],
            sentiment="Positive",
            confidence_score=0.9,
            topics=["API"],
            summary="API documentation with images.",
        )

        fake_llm = MagicMock()
        fake_config = {"isMultimodal": True, "contextLength": 128000}

        raw = b"\x89PNG" + b"\x00" * 20
        b64 = base64.b64encode(raw).decode()
        uri = f"data:image/png;base64,{b64}"
        blocks = [
            _make_text_block("API documentation"),
            _make_image_block(uri, index=1),
        ]

        with patch(
            "app.modules.transformers.document_extraction.get_llm",
            return_value=(fake_llm, fake_config),
        ), patch.object(
            ext, "graph_provider"
        ) as mock_graph, patch(
            "app.modules.transformers.document_extraction.invoke_with_structured_output_and_reflection",
            return_value=fake_classification,
        ), patch(
            "app.modules.transformers.document_extraction._downscale_base64_image",
            return_value=uri,
        ):
            mock_graph.get_departments = AsyncMock(return_value=["Engineering"])

            result = await ext.extract_metadata(blocks, "org-1")

        assert result is not None
        assert result.summary == "API documentation with images."

    @pytest.mark.asyncio
    async def test_extract_metadata_small_context_length(self):
        """When context length is small, content is truncated."""
        from unittest.mock import AsyncMock

        from app.modules.transformers.document_extraction import (
            DocumentClassification,
            SubCategories,
        )

        ext = _build_extractor()

        fake_classification = DocumentClassification(
            departments=[],
            category="General",
            subcategories=SubCategories(level1="", level2="", level3=""),
            languages=["English"],
            sentiment="Neutral",
            confidence_score=0.5,
            topics=[],
            summary="Short summary.",
        )

        fake_llm = MagicMock()
        fake_config = {"isMultimodal": False, "contextLength": 100}

        blocks = [
            _make_text_block("Short text"),
        ]

        with patch(
            "app.modules.transformers.document_extraction.get_llm",
            return_value=(fake_llm, fake_config),
        ), patch.object(
            ext, "graph_provider"
        ) as mock_graph, patch(
            "app.modules.transformers.document_extraction.invoke_with_structured_output_and_reflection",
            return_value=fake_classification,
        ):
            mock_graph.get_departments = AsyncMock(return_value=[])

            result = await ext.extract_metadata(blocks, "org-1")

        assert result is not None
        assert result.summary == "Short summary."

    @pytest.mark.asyncio
    async def test_extract_metadata_llm_exception_propagates(self):
        """When get_llm raises, exception propagates (called before try block)."""
        ext = _build_extractor()

        with patch(
            "app.modules.transformers.document_extraction.get_llm",
            side_effect=RuntimeError("LLM unavailable"),
        ):
            with pytest.raises(RuntimeError, match="LLM unavailable"):
                await ext.extract_metadata([_make_text_block("text")], "org-1")


# =========================================================================
# _downscale_base64_image — additional edge cases
# =========================================================================
class TestDownscaleDeeper:
    """Additional edge cases for _downscale_base64_image."""

    def test_exact_max_dim_returned_unchanged(self):
        """Image exactly at max_dim is returned unchanged."""
        fake_img = MagicMock()
        fake_img.size = (2000, 2000)

        with patch("PIL.Image.open", return_value=fake_img):
            raw_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
            b64 = base64.b64encode(raw_bytes).decode()
            uri = f"data:image/png;base64,{b64}"

            result = _call_downscale(uri, max_dim=2000)

        assert result == uri

    def test_gif_format_returns_none(self):
        """GIF format is not supported."""
        raw = b"GIF89a" + b"\x00" * 20
        b64 = base64.b64encode(raw).decode()
        uri = f"data:image/gif;base64,{b64}"

        result = _call_downscale(uri)
        assert result is None
