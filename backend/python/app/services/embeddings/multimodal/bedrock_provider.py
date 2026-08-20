"""AWS Bedrock native multimodal image embedding (e.g. Titan Multimodal).

Used both for direct AWS Bedrock configuration and for other providers that
proxy through Bedrock (Cohere-via-Bedrock, etc. reuse this same runtime
client shape today).
"""

import asyncio
import inspect
import json
from typing import Any, Callable, List, Optional

from app.exceptions.indexing_exceptions import EmbeddingError
from app.services.embeddings.multimodal.interface import (
    IMultimodalEmbeddingProvider,
    ImageEmbeddingResult,
)
from app.utils.image_utils import normalize_image_to_base64

_CONCURRENCY_LIMIT = 10
_DEFAULT_OUTPUT_EMBEDDING_LENGTH = 1024


class BedrockMultimodalProvider(IMultimodalEmbeddingProvider):
    def __init__(
        self,
        model_name: Optional[str],
        region_name: Optional[str] = None,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        normalize_fn: Optional[Callable[[str], Any]] = None,
        logger=None,
    ) -> None:
        self.model_name = model_name
        self.region_name = region_name
        self.aws_access_key_id = aws_access_key_id
        self.aws_secret_access_key = aws_secret_access_key
        # Injectable so callers (e.g. VectorStore) can reuse an existing
        # normalize implementation; defaults to the shared utility.
        self._normalize_fn = normalize_fn or normalize_image_to_base64
        self.logger = logger

    @property
    def provider_name(self) -> str:
        return "bedrock"

    async def _normalize(self, image_ref: str) -> Optional[str]:
        result = self._normalize_fn(image_ref)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def embed_images(self, image_base64s: List[str]) -> List[ImageEmbeddingResult]:
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
            normalized = await self._normalize(image_ref)
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
                                    "outputEmbeddingLength": _DEFAULT_OUTPUT_EMBEDDING_LENGTH
                                },
                            }),
                            contentType="application/json",
                            accept="application/json",
                        ),
                    )
                    body = json.loads(response["body"].read())
                    return ImageEmbeddingResult(index=i, embedding=list(body["embedding"]))
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
        results: List[ImageEmbeddingResult] = []
        for i, r in enumerate(raw_results):
            if isinstance(r, ImageEmbeddingResult):
                results.append(r)
            else:
                if self.logger:
                    self.logger.warning(f"Bedrock embed failed for index {i}: {r}")
                results.append(ImageEmbeddingResult(index=i, error=str(r)))
        return results
