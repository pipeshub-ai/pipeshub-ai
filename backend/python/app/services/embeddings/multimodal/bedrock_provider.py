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
from app.services.embeddings.multimodal.interface import (
    ImageEmbeddingResult,
    IMultimodalEmbeddingProvider,
)

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
    ) -> None:
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
        try:
            bedrock = boto3.client(**client_kwargs)
        except NoCredentialsError as e:
            raise EmbeddingError("AWS credentials not found for Bedrock image embeddings.") from e

        semaphore = asyncio.Semaphore(_CONCURRENCY_LIMIT)

        async def embed_single(i: int, image_ref: str) -> ImageEmbeddingResult:
            normalized = await self.normalize(image_ref)
            if not normalized:
                return ImageEmbeddingResult(index=i, error="invalid image data")
            async with semaphore:
                try:
                    loop = asyncio.get_running_loop()
                    response = await loop.run_in_executor(
                        None,
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

        # return_exceptions=True: an unexpected (non-Client/NoCredentials) error from
        # one image must not abort the whole batch — every other image should still
        # get a result. Bare exceptions are normalised into ImageEmbeddingResult below
        # so callers only ever see the interface's result type.
        raw_results = await asyncio.gather(
            *[embed_single(i, ref) for i, ref in enumerate(image_base64s)],
            return_exceptions=True,
        )
        results: list[ImageEmbeddingResult] = []
        for i, r in enumerate(raw_results):
            if isinstance(r, ImageEmbeddingResult):
                results.append(r)
            else:
                if self.logger:
                    self.logger.warning(f"Bedrock embed failed for index {i}: {r}")
                results.append(ImageEmbeddingResult(index=i, error=str(r)))
        return results
