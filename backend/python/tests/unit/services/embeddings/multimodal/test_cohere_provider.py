"""Tests for CohereMultimodalProvider."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.embeddings.multimodal.cohere_provider import (
    CohereMultimodalProvider,
    cohere_image_input_type,
)


class TestCohereImageInputTypeHelper:
    @pytest.mark.parametrize(
        ("model_name", "expected"),
        [
            ("embed-english-v3.0", "image"),
            ("embed-multilingual-v3.0", "image"),
            ("embed-v4.0", "search_document"),
            ("embed-v4.5", "search_document"),
            (None, "image"),
        ],
    )
    def test_input_type_by_model_generation(self, model_name, expected) -> None:
        assert cohere_image_input_type(model_name) == expected


class TestCohereMultimodalProvider:
    @pytest.mark.asyncio
    async def test_embed_images_success(self) -> None:
        provider = CohereMultimodalProvider(api_key="test-key", model_name="embed-v3")

        mock_response = MagicMock()
        mock_response.embeddings.float = [[0.1, 0.2, 0.3]]
        mock_co = MagicMock()
        mock_co.embed.return_value = mock_response

        with patch("cohere.ClientV2", return_value=mock_co):
            results = await provider.embed_images(["base64data"])

        assert len(results) == 1
        assert results[0].embedding == [0.1, 0.2, 0.3]
        assert results[0].error is None

    @pytest.mark.asyncio
    async def test_embed_v3_uses_image_input_type(self) -> None:
        """embed-v3.0 must use input_type='image' (texts field must be empty per Cohere docs)."""
        provider = CohereMultimodalProvider(api_key="test-key", model_name="embed-english-v3.0")

        mock_response = MagicMock()
        mock_response.embeddings.float = [[0.1, 0.2, 0.3]]
        mock_co = MagicMock()
        mock_co.embed.return_value = mock_response

        with patch("cohere.ClientV2", return_value=mock_co):
            await provider.embed_images(["b64"])

        assert mock_co.embed.call_args.kwargs["input_type"] == "image"

    @pytest.mark.asyncio
    async def test_embed_v4_uses_search_document_input_type(self) -> None:
        """embed-v4.0 deprecates input_type='image'; Cohere recommends 'search_document'."""
        provider = CohereMultimodalProvider(api_key="test-key", model_name="embed-v4.0")

        mock_response = MagicMock()
        mock_response.embeddings.float = [[0.1, 0.2, 0.3]]
        mock_co = MagicMock()
        mock_co.embed.return_value = mock_response

        with patch("cohere.ClientV2", return_value=mock_co):
            await provider.embed_images(["b64"])

        assert mock_co.embed.call_args.kwargs["input_type"] == "search_document"

    @pytest.mark.asyncio
    async def test_size_limit_error_returns_error_result(self) -> None:
        """Cohere caps images at 5MB; an oversized image must not raise but come
        back as a per-index error so the batch keeps processing."""
        logger = MagicMock()
        provider = CohereMultimodalProvider(
            api_key="test-key", model_name="embed-v3", logger=logger
        )

        mock_co = MagicMock()
        mock_co.embed.side_effect = Exception("image size must be at most 5MB")

        with patch("cohere.ClientV2", return_value=mock_co):
            results = await provider.embed_images(["large_image"])

        assert len(results) == 1
        assert results[0].embedding is None
        assert "image size" in results[0].error
        logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_other_error_returns_error_result_without_raising(self) -> None:
        provider = CohereMultimodalProvider(api_key="test-key", model_name="embed-v3")

        mock_co = MagicMock()
        mock_co.embed.side_effect = RuntimeError("API rate limit exceeded")

        with patch("cohere.ClientV2", return_value=mock_co):
            results = await provider.embed_images(["data"])

        assert len(results) == 1
        assert results[0].embedding is None
        assert "API rate limit exceeded" in results[0].error

    def test_provider_name(self) -> None:
        provider = CohereMultimodalProvider(api_key="k", model_name="embed-v3")
        assert provider.provider_name == "cohere"

    def test_supports_multimodal_defaults_true(self) -> None:
        provider = CohereMultimodalProvider(api_key="k", model_name="embed-v3")
        assert provider.supports_multimodal() is True
