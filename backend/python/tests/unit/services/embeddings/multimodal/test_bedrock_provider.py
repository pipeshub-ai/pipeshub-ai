"""Tests for BedrockMultimodalProvider."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions.indexing_exceptions import EmbeddingError
from app.services.embeddings.multimodal.bedrock_provider import BedrockMultimodalProvider


def _provider(**kwargs) -> BedrockMultimodalProvider:
    defaults = {
        "model_name": "amazon.titan-embed-image-v1",
        "region_name": "us-east-1",
        "aws_access_key_id": "AKID",
        "aws_secret_access_key": "secret",
        "logger": MagicMock(),
    }
    defaults.update(kwargs)
    return BedrockMultimodalProvider(**defaults)


class TestBedrockMultimodalProvider:
    @pytest.mark.asyncio
    async def test_embed_images_success(self) -> None:
        provider = _provider()

        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps({"embedding": [0.1, 0.2]}).encode()
        mock_client = MagicMock()
        mock_client.invoke_model.return_value = {"body": mock_body}

        with patch("boto3.client", return_value=mock_client):
            results = await provider.embed_images(["aW1hZ2U="])

        assert len(results) == 1
        assert results[0].embedding == [0.1, 0.2]

    @pytest.mark.asyncio
    async def test_no_credentials_during_client_creation_raises(self) -> None:
        from botocore.exceptions import NoCredentialsError

        provider = _provider(aws_access_key_id=None, aws_secret_access_key=None, region_name=None)

        with patch("boto3.client", side_effect=NoCredentialsError()):
            with pytest.raises(EmbeddingError, match="AWS credentials"):
                await provider.embed_images(["AAAA"])

    @pytest.mark.asyncio
    async def test_invalid_image_skipped(self) -> None:
        provider = _provider()
        mock_client = MagicMock()

        with patch("boto3.client", return_value=mock_client):
            results = await provider.embed_images(["not!valid@base64#"])

        assert len(results) == 1
        assert results[0].embedding is None
        assert results[0].error == "invalid image data"

    @pytest.mark.asyncio
    async def test_client_error_during_invoke_returns_error_result(self) -> None:
        from botocore.exceptions import ClientError

        provider = _provider()
        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = ClientError(
            {"Error": {"Code": "ValidationException", "Message": "bad input"}},
            "InvokeModel",
        )

        with patch("boto3.client", return_value=mock_client):
            results = await provider.embed_images(["AAAA"])

        assert len(results) == 1
        assert results[0].embedding is None
        provider.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_no_credentials_during_invoke_returns_error_result(self) -> None:
        from botocore.exceptions import NoCredentialsError

        provider = _provider()
        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = NoCredentialsError()

        with patch("boto3.client", return_value=mock_client):
            results = await provider.embed_images(["AAAA"])

        assert len(results) == 1
        assert results[0].embedding is None
        provider.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_unexpected_error_during_invoke_does_not_raise(self) -> None:
        """An error type not explicitly handled (e.g. ValueError) must still
        surface as a per-index error result rather than aborting the batch."""
        provider = _provider()
        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = ValueError("unexpected bedrock error")

        with patch("boto3.client", return_value=mock_client):
            results = await provider.embed_images(["AAAA"])

        assert len(results) == 1
        assert results[0].embedding is None

    @pytest.mark.asyncio
    async def test_normalize_fn_is_injectable(self) -> None:
        """VectorStore injects its own normalize function so existing
        instance-level test patches keep working after the dispatch moved here."""
        normalize_fn = AsyncMock(return_value="AAAA")
        provider = _provider(normalize_fn=normalize_fn)

        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps({"embedding": [0.5]}).encode()
        mock_client = MagicMock()
        mock_client.invoke_model.return_value = {"body": mock_body}

        with patch("boto3.client", return_value=mock_client):
            results = await provider.embed_images(["irrelevant"])

        normalize_fn.assert_awaited_once_with("irrelevant")
        assert results[0].embedding == [0.5]

    @pytest.mark.asyncio
    async def test_normalize_fn_returning_none_skips_image(self) -> None:
        normalize_fn = AsyncMock(return_value=None)
        provider = _provider(normalize_fn=normalize_fn)
        mock_client = MagicMock()

        with patch("boto3.client", return_value=mock_client):
            results = await provider.embed_images(["invalid_data"])

        assert results[0].embedding is None
        assert results[0].error == "invalid image data"

    def test_provider_name(self) -> None:
        assert _provider().provider_name == "bedrock"
