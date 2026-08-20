"""Tests for CohereMultimodalProvider."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.embeddings.multimodal.cohere_provider import (
    CohereMultimodalProvider,
    cohere_image_input_type,
    supports_inputs_image_batch,
)


class TestCohereImageInputTypeHelper:
    """Cohere documents `inputs` as accepting only search_query /
    search_document / classification / clustering — `image` is excluded from
    that set, so it is never the right value on this code path regardless of
    model generation."""

    @pytest.mark.parametrize(
        "model_name",
        ["embed-english-v3.0", "embed-multilingual-v3.0", "embed-v4.0", "embed-v4.5", None],
    )
    def test_input_type_is_always_search_document(self, model_name) -> None:
        assert cohere_image_input_type(model_name) == "search_document"

    @pytest.mark.parametrize(
        ("model_name", "expected"),
        [
            ("embed-v4.0", True),
            ("embed-4-preview", True),
            ("embed-english-v3.0", False),
            ("embed-multilingual-v3.0", False),
            (None, False),
        ],
    )
    def test_inputs_image_batch_support_by_generation(self, model_name, expected) -> None:
        assert supports_inputs_image_batch(model_name) is expected

    def test_pre_v4_model_warns_once_at_construction(self) -> None:
        logger = MagicMock()
        CohereMultimodalProvider(
            api_key="k", model_name="embed-english-v3.0", logger=logger,
        )
        logger.warning.assert_called_once()

    def test_v4_model_does_not_warn(self) -> None:
        logger = MagicMock()
        CohereMultimodalProvider(api_key="k", model_name="embed-v4.0", logger=logger)
        logger.warning.assert_not_called()


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
    async def test_pre_v4_model_still_sends_a_documented_input_type(self) -> None:
        """`image` is not a legal input_type for the `inputs` parameter, so even
        a v3 model must not be sent it."""
        provider = CohereMultimodalProvider(api_key="test-key", model_name="embed-english-v3.0")

        mock_response = MagicMock()
        mock_response.embeddings.float = [[0.1, 0.2, 0.3]]
        mock_co = MagicMock()
        mock_co.embed.return_value = mock_response

        with patch("cohere.ClientV2", return_value=mock_co):
            await provider.embed_images(["b64"])

        assert mock_co.embed.call_args.kwargs["input_type"] == "search_document"

    @pytest.mark.asyncio
    async def test_embed_v4_uses_search_document_input_type(self) -> None:
        """Cohere recommends `search_document` for images on embed-v4.0."""
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
