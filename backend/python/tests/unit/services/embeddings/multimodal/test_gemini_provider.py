"""Tests for GeminiMultimodalProvider."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.embeddings.multimodal.gemini_provider import GeminiMultimodalProvider


class TestGeminiMultimodalProviderModelName:
    def test_adds_models_prefix_when_missing(self) -> None:
        provider = GeminiMultimodalProvider(api_key="k", model_name="gemini-embedding-2")
        assert provider.model_name == "models/gemini-embedding-2"

    def test_keeps_existing_models_prefix(self) -> None:
        provider = GeminiMultimodalProvider(api_key="k", model_name="models/gemini-embedding-2")
        assert provider.model_name == "models/gemini-embedding-2"


class TestGeminiMultimodalProviderEmbedImages:
    @pytest.mark.asyncio
    async def test_embed_images_success(self) -> None:
        provider = GeminiMultimodalProvider(api_key="k", model_name="gemini-embedding-2")
        png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20).decode()

        mock_embedding = MagicMock()
        mock_embedding.values = [0.1, 0.2, 0.3]
        mock_response = MagicMock()
        mock_response.embeddings = [mock_embedding]

        mock_client = MagicMock()
        mock_client.aio.models.embed_content = AsyncMock(return_value=mock_response)

        with patch("google.genai.Client", return_value=mock_client):
            results = await provider.embed_images([png_b64])

        assert len(results) == 1
        assert results[0].embedding == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_invalid_image_returns_error(self) -> None:
        provider = GeminiMultimodalProvider(api_key="k", model_name="gemini-embedding-2")

        with patch("google.genai.Client", return_value=MagicMock()):
            results = await provider.embed_images(["not!valid@base64#"])

        assert len(results) == 1
        assert results[0].embedding is None
        assert results[0].error == "invalid image data"

    @pytest.mark.asyncio
    async def test_api_error_returns_error_result_without_raising(self) -> None:
        logger = MagicMock()
        provider = GeminiMultimodalProvider(
            api_key="k", model_name="gemini-embedding-2", logger=logger
        )
        png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20).decode()

        mock_client = MagicMock()
        mock_client.aio.models.embed_content = AsyncMock(side_effect=RuntimeError("quota exceeded"))

        with patch("google.genai.Client", return_value=mock_client):
            results = await provider.embed_images([png_b64])

        assert len(results) == 1
        assert results[0].embedding is None
        logger.warning.assert_called()

    def test_provider_name(self) -> None:
        provider = GeminiMultimodalProvider(api_key="k", model_name="gemini-embedding-2")
        assert provider.provider_name == "gemini"
