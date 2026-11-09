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


class TestGeminiMultimodalProviderCapability:
    def test_a_text_only_model_is_not_sent_images(self) -> None:
        assert not GeminiMultimodalProvider(api_key="k", model_name="gemini-embedding-001").supports_multimodal()
        assert GeminiMultimodalProvider(api_key="k", model_name="gemini-embedding-2").supports_multimodal()


class TestGeminiMultimodalProviderDimension:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("embedding_size", "requested"), [(768, 768), (None, None)])
    async def test_it_requests_the_collections_width(self, embedding_size, requested) -> None:
        provider = GeminiMultimodalProvider(
            api_key="k", model_name="gemini-embedding-2", embedding_size=embedding_size,
        )
        client = MagicMock()
        client.aio.models.embed_content = AsyncMock(
            return_value=MagicMock(embeddings=[MagicMock(values=[0.1])])
        )

        with patch("google.genai.Client", return_value=client):
            await provider.embed_images(["iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="])

        config = client.aio.models.embed_content.await_args.kwargs["config"]
        assert (config.output_dimensionality if config else None) == requested


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
