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


class TestBedrockOutputEmbeddingLength:
    """Regression: outputEmbeddingLength was hardcoded to 1024, so on a
    collection of any other dimension every point Bedrock returned was
    discarded by VectorStore._build_image_points as a dimension mismatch."""

    def test_defaults_to_1024_when_size_unknown(self) -> None:
        provider = BedrockMultimodalProvider(model_name="titan", embedding_size=None)
        assert provider.output_embedding_length == 1024

    @pytest.mark.parametrize("size", [256, 384, 1024])
    def test_uses_the_collection_dimension(self, size: int) -> None:
        provider = BedrockMultimodalProvider(model_name="titan", embedding_size=size)
        assert provider.output_embedding_length == size

    def test_unsupported_dimension_warns_and_falls_back(self) -> None:
        logger = MagicMock()
        provider = BedrockMultimodalProvider(
            model_name="titan", embedding_size=1536, logger=logger,
        )
        assert provider.output_embedding_length == 1024
        logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_requested_length_reaches_the_invoke_body(self) -> None:
        import json

        provider = BedrockMultimodalProvider(
            model_name="titan", embedding_size=384,
            normalize_fn=lambda _uri: "aW1hZ2U=",
        )
        response_body = MagicMock()
        response_body.read.return_value = json.dumps({"embedding": [0.1] * 384})
        bedrock = MagicMock()
        bedrock.invoke_model.return_value = {"body": response_body}

        with patch("boto3.client", return_value=bedrock):
            results = await provider.embed_images(["aW1hZ2U="])

        assert results[0].embedding is not None
        body = json.loads(bedrock.invoke_model.call_args.kwargs["body"])
        assert body["embeddingConfig"]["outputEmbeddingLength"] == 384


class TestBedrockErrorReportedInBody:
    """Titan reports per-image generation failures in a `message` field while
    still returning HTTP 200, so boto3 raises nothing and a naive read of
    `body["embedding"]` would KeyError (or worse, index a partial result)."""

    @pytest.mark.asyncio
    async def test_message_in_body_becomes_an_error_result(self) -> None:
        logger = MagicMock()
        provider = BedrockMultimodalProvider(
            model_name="titan", logger=logger, normalize_fn=lambda _uri: "aW1hZ2U=",
        )
        body = MagicMock()
        body.read.return_value = json.dumps({"message": "image too large"})
        bedrock = MagicMock()
        bedrock.invoke_model.return_value = {"body": body}

        with patch("boto3.client", return_value=bedrock):
            results = await provider.embed_images(["aW1hZ2U="])

        assert results[0].embedding is None
        assert "image too large" in results[0].error
        logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_missing_embedding_becomes_an_error_result(self) -> None:
        provider = BedrockMultimodalProvider(
            model_name="titan", normalize_fn=lambda _uri: "aW1hZ2U=",
        )
        body = MagicMock()
        body.read.return_value = json.dumps({"inputTextTokenCount": 0})
        bedrock = MagicMock()
        bedrock.invoke_model.return_value = {"body": body}

        with patch("boto3.client", return_value=bedrock):
            results = await provider.embed_images(["aW1hZ2U="])

        assert results[0].embedding is None
        assert results[0].error
