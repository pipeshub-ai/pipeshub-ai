"""Tests for OllamaMultimodalProvider.

Ollama's ``/api/embed`` endpoint is text-only as of this writing (see the
module docstring in ``ollama_provider.py``), so ``supports_multimodal()``
must report ``False`` and every embed attempt should degrade to a per-image
error rather than raise, letting the caller fall back to VLM description.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.embeddings.multimodal.ollama_provider import OllamaMultimodalProvider


class TestOllamaMultimodalProvider:
    def test_supports_multimodal_is_false(self) -> None:
        provider = OllamaMultimodalProvider(base_url=None, model_name="llava")
        assert provider.supports_multimodal() is False

    def test_default_base_url(self) -> None:
        provider = OllamaMultimodalProvider(base_url=None, model_name="llava")
        assert provider.base_url == "http://localhost:11434"

    def test_custom_base_url_trailing_slash_stripped(self) -> None:
        provider = OllamaMultimodalProvider(base_url="http://myhost:11434/", model_name="llava")
        assert provider.base_url == "http://myhost:11434"

    @pytest.mark.asyncio
    async def test_embed_images_returns_error_when_no_embeddings_field(self) -> None:
        """Current Ollama builds accept the request but return no embedding —
        must surface as a per-image error, not raise."""
        provider = OllamaMultimodalProvider(base_url="http://localhost:11434", model_name="llava")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"embeddings": []}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await provider.embed_images(["b64data"])

        assert len(results) == 1
        assert results[0].embedding is None
        assert results[0].error is not None

    @pytest.mark.asyncio
    async def test_embed_images_uses_embedding_when_present(self) -> None:
        """If a future/forked Ollama build does support image embedding, the
        provider should use it without any code change."""
        provider = OllamaMultimodalProvider(base_url="http://localhost:11434", model_name="llava")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"embeddings": [[0.1, 0.2]]}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await provider.embed_images(["b64data"])

        assert results[0].embedding == [0.1, 0.2]

    @pytest.mark.asyncio
    async def test_http_error_returns_error_result_without_raising(self) -> None:
        logger = MagicMock()
        provider = OllamaMultimodalProvider(
            base_url="http://localhost:11434", model_name="llava", logger=logger
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = RuntimeError("connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await provider.embed_images(["b64data"])

        assert results[0].embedding is None
        logger.warning.assert_called()

    def test_provider_name(self) -> None:
        provider = OllamaMultimodalProvider(base_url=None, model_name="llava")
        assert provider.provider_name == "ollama"
