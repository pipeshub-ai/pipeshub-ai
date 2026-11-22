"""AWS Bedrock native multimodal image embedding (e.g. Titan Multimodal).

Used both for direct AWS Bedrock configuration and for other providers that
proxy through Bedrock (Cohere-via-Bedrock, etc. reuse this same runtime
client shape today).
"""

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from app.exceptions.indexing_exceptions import EmbeddingError
from app.services.embeddings.multimodal._concurrent import (
    gather_or_cancel,
    to_thread_settled,
)
from app.services.embeddings.multimodal.interface import (
    ImageEmbeddingResult,
    IMultimodalEmbeddingProvider,
)
from app.services.llm_gateway.embedding_gateway import (
    EmbeddingRateLimited,
    get_embedding_gateway,
)
from app.services.llm_gateway.wire import refused_by_allowance

_CONCURRENCY_LIMIT = 10
_DEFAULT_OUTPUT_EMBEDDING_LENGTH = 1024
# Titan Multimodal rejects any other value; asking for one the collection
# needs but Titan cannot produce is a config error worth naming up front
# rather than discovering as a silent per-point dimension mismatch.
_SUPPORTED_OUTPUT_EMBEDDING_LENGTHS = (256, 384, 1024)


class BedrockMultimodalProvider(IMultimodalEmbeddingProvider):
    def __init__(
        self,
        model_name: str | None,
        region_name: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        embedding_size: int | None = None,
        normalize_fn: Callable[[str], Any] | None = None,
        logger: logging.Logger | None = None,
        sdk_retries: int | None = None,
    ) -> None:
        self.sdk_retries = sdk_retries
        self.model_name = model_name
        self.region_name = region_name
        self.aws_access_key_id = aws_access_key_id
        self.aws_secret_access_key = aws_secret_access_key
        # Consumed by IMultimodalEmbeddingProvider.normalize().
        self._normalize_fn = normalize_fn
        self.logger = logger
        self.output_embedding_length = self._resolve_output_length(embedding_size, logger)

    @staticmethod
    def _resolve_output_length(
        embedding_size: int | None, logger: logging.Logger | None,
    ) -> int:
        if embedding_size is None:
            return _DEFAULT_OUTPUT_EMBEDDING_LENGTH
        if embedding_size in _SUPPORTED_OUTPUT_EMBEDDING_LENGTHS:
            return embedding_size
        if logger:
            logger.warning(
                "Collection dimension %s is not a Titan Multimodal output length %s; "
                "requesting %s instead — image points will be dropped as "
                "dimension mismatches until the collection or embedding model is changed.",
                embedding_size, _SUPPORTED_OUTPUT_EMBEDDING_LENGTHS,
                _DEFAULT_OUTPUT_EMBEDDING_LENGTH,
            )
        return _DEFAULT_OUTPUT_EMBEDDING_LENGTH

    @property
    def provider_name(self) -> str:
        return "bedrock"

    async def embed_images(self, image_base64s: list[str]) -> list[ImageEmbeddingResult]:
        import boto3
        from botocore.exceptions import ClientError, NoCredentialsError

        client_kwargs: dict = {"service_name": "bedrock-runtime"}
        if self.aws_access_key_id and self.aws_secret_access_key and self.region_name:
            client_kwargs.update(
                {
                    "aws_access_key_id": self.aws_access_key_id,
                    "aws_secret_access_key": self.aws_secret_access_key,
                    "region_name": self.region_name,
                }
            )
        if self.sdk_retries is not None:
            from botocore.config import Config

            # botocore repeats a throttled or failed request by default; the gateway is the one retry layer.
            client_kwargs["config"] = Config(retries={"mode": "standard", "total_max_attempts": self.sdk_retries + 1})
        try:
            bedrock = boto3.client(**client_kwargs)
        except NoCredentialsError as e:
            raise EmbeddingError("AWS credentials not found for Bedrock image embeddings.") from e

        # This client does not come from a model factory: count its requests in the wire scope of a gateway attempt.
        from app.services.llm_gateway.wire import install_boto_hooks

        install_boto_hooks(bedrock)
        semaphore = asyncio.Semaphore(_CONCURRENCY_LIMIT)

        async def embed_single(i: int, image_ref: str) -> ImageEmbeddingResult:
            normalized = await self.normalize(image_ref)
            if not normalized:
                return ImageEmbeddingResult(index=i, error="invalid image data")
            async with semaphore:
                try:
                    response = await get_embedding_gateway().call(
                        lambda: to_thread_settled(
                            lambda: bedrock.invoke_model(
                                modelId=self.model_name,
                                body=json.dumps({
                                    "inputImage": normalized,
                                    "embeddingConfig": {
                                        "outputEmbeddingLength": self.output_embedding_length
                                    },
                                }),
                                contentType="application/json",
                                accept="application/json",
                            ),
                        )
                    )
                    body = json.loads(response["body"].read())
                    # Titan reports per-image generation failures in `message`
                    # while still returning HTTP 200, so a missing/!=None
                    # message is the only signal that `embedding` is real.
                    failure = body.get("message")
                    if failure:
                        if self.logger:
                            self.logger.warning(
                                f"Bedrock embed failed for index {i}: {failure}"
                            )
                        return ImageEmbeddingResult(index=i, error=str(failure))
                    embedding = body.get("embedding")
                    if not embedding:
                        return ImageEmbeddingResult(
                            index=i, error="no embedding returned for this image"
                        )
                    return ImageEmbeddingResult(index=i, embedding=list(embedding))
                except (NoCredentialsError, ClientError) as e:
                    if self.logger:
                        self.logger.warning(f"Bedrock embed failed for index {i}: {e}")
                    return ImageEmbeddingResult(index=i, error=str(e))

        async def guarded(i: int, image_ref: str) -> ImageEmbeddingResult:
            # One image's unexpected failure must not abort the batch (every other image still gets a result), but a provider's
            # wait past the call's allowance is the whole record's: it propagates, and the other images are cancelled.
            try:
                return await embed_single(i, image_ref)
            except EmbeddingRateLimited:
                raise
            except Exception as exc:
                if refused_by_allowance(exc):
                    raise  # the job's send allowance is spent: not one image's failure
                if self.logger:
                    self.logger.warning(f"Bedrock embed failed for index {i}: {exc}")
                return ImageEmbeddingResult(index=i, error=str(exc))

        return await gather_or_cancel([guarded(i, ref) for i, ref in enumerate(image_base64s)])
