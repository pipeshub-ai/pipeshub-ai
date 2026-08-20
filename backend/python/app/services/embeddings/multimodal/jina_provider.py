"""Jina AI native multimodal image embedding (jina-clip-v1/v2)."""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import httpx

from app.services.embeddings.multimodal._response import (
    describe_request_error,
    map_embedding_response,
)
from app.services.embeddings.multimodal.interface import (
    ImageEmbeddingResult,
    IMultimodalEmbeddingProvider,
)

_CONCURRENCY_LIMIT = 5
_BATCH_SIZE = 32
_JINA_EMBEDDINGS_URL = "https://api.jina.ai/v1/embeddings"
# Jina dispatches its request schema on the model name. Only these families
# declare an image variant for `input`; the text-only ones reject an
# `{"image": ...}` item with a 422 rather than embedding it.
_IMAGE_CAPABLE_MODEL_MARKERS = ("jina-clip", "jina-embeddings-v4", "jina-embeddings-v5")


def supports_image_input(model_name: str | None) -> bool:
    name = (model_name or "").lower()
    return any(marker in name for marker in _IMAGE_CAPABLE_MODEL_MARKERS)


class JinaMultimodalProvider(IMultimodalEmbeddingProvider):
    def __init__(
        self,
        api_key: str | None,
        model_name: str | None,
        normalize_fn: Callable[[str], Any] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.api_key = api_key
        self.model_name = model_name
        # Consumed by IMultimodalEmbeddingProvider.normalize().
        self._normalize_fn = normalize_fn
        self.logger = logger
        if logger and not supports_image_input(model_name):
            logger.warning(
                "Jina model %r has no image input schema; image requests will be "
                "rejected. Use a jina-clip-* or jina-embeddings-v4/v5 model.",
                model_name,
            )

    @property
    def provider_name(self) -> str:
        return "jinaAI"

    async def embed_images(self, image_base64s: list[str]) -> list[ImageEmbeddingResult]:
        semaphore = asyncio.Semaphore(_CONCURRENCY_LIMIT)

        async def process_batch(
            client: httpx.AsyncClient, batch_start: int, batch_imgs: list[str]
        ) -> list[ImageEmbeddingResult]:
            async with semaphore:
                normalized = [await self.normalize(img) for img in batch_imgs]
                valid = [(batch_start + j, n) for j, n in enumerate(normalized) if n]
                invalid_results = [
                    ImageEmbeddingResult(index=batch_start + j, error="invalid image data")
                    for j, n in enumerate(normalized)
                    if not n
                ]
                if not valid:
                    return invalid_results
                try:
                    resp = await client.post(
                        _JINA_EMBEDDINGS_URL,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {self.api_key}",
                        },
                        json={
                            "model": self.model_name,
                            "input": [{"image": n} for _, n in valid],
                        },
                    )
                    # Without this a 401/429/5xx yields an error body with no
                    # "data" key, and the batch would return nothing at all
                    # for its valid images instead of one error per index.
                    resp.raise_for_status()
                    valid_results = map_embedding_response(
                        resp.json().get("data", []), [index for index, _ in valid],
                    )
                    return valid_results + invalid_results
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"Jina batch {batch_start} failed: {describe_request_error(e)}")
                    return [
                        ImageEmbeddingResult(index=idx, error=describe_request_error(e))
                        for idx, _ in valid
                    ] + invalid_results

        async with httpx.AsyncClient(timeout=60.0) as client:
            batches = [
                (start, image_base64s[start:start + _BATCH_SIZE])
                for start in range(0, len(image_base64s), _BATCH_SIZE)
            ]
            results = await asyncio.gather(
                *[process_batch(client, s, imgs) for s, imgs in batches]
            )
        flattened: list[ImageEmbeddingResult] = []
        for r in results:
            flattened.extend(r)
        return flattened
