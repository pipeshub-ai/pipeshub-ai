"""Jina AI native multimodal image embedding (jina-clip-v1/v2)."""

import asyncio
import inspect
from typing import Any, Callable, List, Optional

import httpx

from app.services.embeddings.multimodal.interface import (
    IMultimodalEmbeddingProvider,
    ImageEmbeddingResult,
)
from app.utils.image_utils import normalize_image_to_base64

_CONCURRENCY_LIMIT = 5
_BATCH_SIZE = 32
_JINA_EMBEDDINGS_URL = "https://api.jina.ai/v1/embeddings"


class JinaMultimodalProvider(IMultimodalEmbeddingProvider):
    def __init__(
        self,
        api_key: Optional[str],
        model_name: Optional[str],
        normalize_fn: Optional[Callable[[str], Any]] = None,
        logger=None,
    ) -> None:
        self.api_key = api_key
        self.model_name = model_name
        # Injectable so callers (e.g. VectorStore) can reuse an existing
        # normalize implementation; defaults to the shared utility.
        self._normalize_fn = normalize_fn or normalize_image_to_base64
        self.logger = logger

    @property
    def provider_name(self) -> str:
        return "jinaAI"

    async def _normalize(self, image_ref: str) -> Optional[str]:
        result = self._normalize_fn(image_ref)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def embed_images(self, image_base64s: List[str]) -> List[ImageEmbeddingResult]:
        semaphore = asyncio.Semaphore(_CONCURRENCY_LIMIT)

        async def process_batch(
            client: httpx.AsyncClient, batch_start: int, batch_imgs: List[str]
        ) -> List[ImageEmbeddingResult]:
            async with semaphore:
                normalized = [await self._normalize(img) for img in batch_imgs]
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
                    data = resp.json().get("data", [])
                    valid_results = [
                        ImageEmbeddingResult(index=valid[i][0], embedding=list(item["embedding"]))
                        for i, item in enumerate(data)
                    ]
                    return valid_results + invalid_results
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"Jina batch {batch_start} failed: {e}")
                    return [
                        ImageEmbeddingResult(index=idx, error=str(e)) for idx, _ in valid
                    ] + invalid_results

        async with httpx.AsyncClient(timeout=60.0) as client:
            batches = [
                (start, image_base64s[start:start + _BATCH_SIZE])
                for start in range(0, len(image_base64s), _BATCH_SIZE)
            ]
            results = await asyncio.gather(
                *[process_batch(client, s, imgs) for s, imgs in batches]
            )
        flattened: List[ImageEmbeddingResult] = []
        for r in results:
            flattened.extend(r)
        return flattened
