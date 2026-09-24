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

    @pytest.mark.asyncio
    async def test_http_error_returns_one_error_per_index(self) -> None:
        """Regression: without raise_for_status a 429 body has no "data" key,
        so the batch returned nothing at all for its valid images instead of
        one error result per input index."""
        import httpx

        provider = JinaMultimodalProvider(api_key="jina-key", model_name="jina-clip-v1")
        logger = MagicMock()
        provider.logger = logger

        mock_response = MagicMock()
        mock_response.json.return_value = {"detail": "rate limit exceeded"}
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "429", request=MagicMock(), response=MagicMock(),
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await provider.embed_images(["aW1hZ2Uw", "aW1hZ2Ux"])

        assert [r.index for r in results] == [0, 1]
        assert all(r.embedding is None and r.error for r in results)
        logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_out_of_order_response_maps_by_index(self) -> None:
        """Regression: results were zipped to inputs by list position, so a
        reordered response attached each embedding to the wrong image."""
        provider = JinaMultimodalProvider(api_key="jina-key", model_name="jina-clip-v1")

        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [
            {"index": 1, "embedding": [1.0]},
            {"index": 0, "embedding": [0.0]},
        ]}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await provider.embed_images(["aW1hZ2Uw", "aW1hZ2Ux"])

        assert [(r.index, r.embedding) for r in results] == [(0, [0.0]), (1, [1.0])]

    @pytest.mark.asyncio
    async def test_short_response_errors_the_unanswered_index(self) -> None:
        provider = JinaMultimodalProvider(api_key="jina-key", model_name="jina-clip-v1")

        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"index": 0, "embedding": [0.1]}]}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await provider.embed_images(["aW1hZ2Uw", "aW1hZ2Ux"])

        assert [r.index for r in results] == [0, 1]
        assert results[0].embedding == [0.1]
        assert results[1].embedding is None and results[1].error

    def test_provider_name(self) -> None:
        provider = JinaMultimodalProvider(api_key="k", model_name="jina-clip-v1")
        assert provider.provider_name == "jinaAI"


class TestJinaModelCapability:
    """Jina dispatches its request schema on the model name; only jina-clip-*
    and jina-embeddings-v4/v5 declare an image variant for `input`. A
    text-only model rejects `{"image": ...}` with a 422 that says nothing
    useful unless the body is surfaced."""

    @pytest.mark.parametrize(
        ("model_name", "expected"),
        [
            ("jina-clip-v1", True),
            ("jina-clip-v2", True),
            ("jina-embeddings-v4", True),
            ("jina-embeddings-v5-base", True),
            ("jina-embeddings-v3", False),
            ("jina-embeddings-v2-base-en", False),
            (None, False),
        ],
    )
    def test_image_capability_by_model(self, model_name, expected) -> None:
        from app.services.embeddings.multimodal.jina_provider import supports_image_input

        assert supports_image_input(model_name) is expected

    def test_text_only_model_warns_at_construction(self) -> None:
        logger = MagicMock()
        JinaMultimodalProvider(
            api_key="k", model_name="jina-embeddings-v3", logger=logger,
        )
        logger.warning.assert_called_once()

    def test_image_capable_model_does_not_warn(self) -> None:
        logger = MagicMock()
        JinaMultimodalProvider(api_key="k", model_name="jina-clip-v2", logger=logger)
        logger.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_http_error_surfaces_the_response_body(self) -> None:
        """A bare "422 Unprocessable Entity" is undiagnosable; the server's
        explanation must reach the error result."""
        import httpx

        provider = JinaMultimodalProvider(api_key="k", model_name="jina-clip-v2")
        response = MagicMock()
        response.text = '{"detail":"input.0.image: extra fields not permitted"}'
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "422 Unprocessable Entity", request=MagicMock(), response=response,
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await provider.embed_images(["aW1hZ2U="])

        assert "extra fields not permitted" in results[0].error
