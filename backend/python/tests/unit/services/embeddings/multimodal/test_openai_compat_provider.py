"""Tests for OpenAICompatMultimodalProvider (OpenAI-compatible / LM Studio)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.embeddings.multimodal.openai_compat_provider import (
    OpenAICompatMultimodalProvider,
)


class TestOpenAICompatMultimodalProviderInit:
    def test_missing_base_url_raises(self) -> None:
        with pytest.raises(ValueError, match="base_url"):
            OpenAICompatMultimodalProvider(base_url=None, api_key="k", model_name="m")

    def test_trailing_slash_stripped(self) -> None:
        provider = OpenAICompatMultimodalProvider(
            base_url="http://localhost:1234/", api_key=None, model_name="m"
        )
        assert provider.base_url == "http://localhost:1234"

    def test_default_provider_label(self) -> None:
        provider = OpenAICompatMultimodalProvider(
            base_url="http://localhost:1234", api_key=None, model_name="m"
        )
        assert provider.provider_name == "openAICompatible"

    def test_custom_provider_label_for_lm_studio(self) -> None:
        provider = OpenAICompatMultimodalProvider(
            base_url="http://localhost:1234",
            api_key=None,
            model_name="m",
            provider_label="lmStudio",
        )
        assert provider.provider_name == "lmStudio"


class TestOpenAICompatMultimodalProviderEmbedImages:
    @pytest.mark.asyncio
    async def test_embed_images_success(self) -> None:
        provider = OpenAICompatMultimodalProvider(
            base_url="http://localhost:1234", api_key="k", model_name="clip"
        )

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": [0.1, 0.2]}]}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await provider.embed_images(["aW1hZ2U="])

        assert len(results) == 1
        assert results[0].embedding == [0.1, 0.2]

    @pytest.mark.asyncio
    async def test_invalid_images_are_filtered_and_reported(self) -> None:
        provider = OpenAICompatMultimodalProvider(
            base_url="http://localhost:1234", api_key="k", model_name="clip"
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await provider.embed_images(["not!valid@base64#"])

        assert results[0].embedding is None
        assert results[0].error == "invalid image data"
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_server_that_only_accepts_text_fails_gracefully(self) -> None:
        """A server without multimodal support should degrade to a per-image
        error, not raise, so the caller can fall back to VLM description."""
        logger = MagicMock()
        provider = OpenAICompatMultimodalProvider(
            base_url="http://localhost:1234", api_key=None, model_name="text-only", logger=logger
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = RuntimeError("400 Bad Request: image input not supported")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await provider.embed_images(["aW1hZ2U="])

        assert results[0].embedding is None
        logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_no_api_key_omits_authorization_header(self) -> None:
        provider = OpenAICompatMultimodalProvider(
            base_url="http://localhost:1234", api_key=None, model_name="clip"
        )
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": [0.1]}]}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await provider.embed_images(["aW1hZ2U="])

        headers = mock_client.post.call_args.kwargs["headers"]
        assert "Authorization" not in headers
