"""Tests for JinaMultimodalProvider."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.embeddings.multimodal.jina_provider import JinaMultimodalProvider


class TestJinaMultimodalProvider:
    @pytest.mark.asyncio
    async def test_embed_images_success(self) -> None:
        provider = JinaMultimodalProvider(api_key="jina-key", model_name="jina-clip-v1")

        mock_response = MagicMock()
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
    async def test_batch_failure_returns_error_results(self) -> None:
        logger = MagicMock()
        provider = JinaMultimodalProvider(
            api_key="jina-key", model_name="jina-clip-v1", logger=logger
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = RuntimeError("API error")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await provider.embed_images(["aW1hZ2U="])

        assert len(results) == 1
        assert results[0].embedding is None
        logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_invalid_images_filtered_out(self) -> None:
        provider = JinaMultimodalProvider(api_key="jina-key", model_name="jina-clip-v1")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await provider.embed_images(["not!valid@base64#"])

        assert len(results) == 1
        assert results[0].embedding is None
        assert results[0].error == "invalid image data"
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_normalize_fn_is_injectable(self) -> None:
        normalize_fn = AsyncMock(return_value="AAAA")
        provider = JinaMultimodalProvider(
            api_key="k", model_name="jina-clip-v1", normalize_fn=normalize_fn
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": [0.3]}]}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await provider.embed_images(["irrelevant"])

        normalize_fn.assert_awaited_once_with("irrelevant")
        assert results[0].embedding == [0.3]

    @pytest.mark.asyncio
    async def test_all_images_fail_normalization_returns_no_success(self) -> None:
        normalize_fn = AsyncMock(return_value=None)
        provider = JinaMultimodalProvider(
            api_key="k", model_name="jina-clip-v1", normalize_fn=normalize_fn
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await provider.embed_images(["invalid_data"])

        assert len(results) == 1
        assert results[0].embedding is None

    def test_provider_name(self) -> None:
        provider = JinaMultimodalProvider(api_key="k", model_name="jina-clip-v1")
        assert provider.provider_name == "jinaAI"
