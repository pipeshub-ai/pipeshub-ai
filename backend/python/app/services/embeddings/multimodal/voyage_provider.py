"""Voyage native multimodal image embedding (voyage-multimodal-3).

Delegates to the already-multimodal-aware LangChain ``Embeddings`` instance
(``app.utils.custom_embeddings.VoyageEmbeddings``), which knows how to turn a
base64 image string into a ``voyage`` multimodal input part. This provider
only owns batching/concurrency around that call.
"""

import asyncio
from typing import Any, List, Optional

from app.services.embeddings.multimodal.interface import (
    IMultimodalEmbeddingProvider,
    ImageEmbeddingResult,
)

_CONCURRENCY_LIMIT = 5
_DEFAULT_BATCH_SIZE = 7


class VoyageMultimodalProvider(IMultimodalEmbeddingProvider):
    def __init__(self, dense_embeddings: Any, logger=None) -> None:
        self.dense_embeddings = dense_embeddings
        self.logger = logger

    @property
    def provider_name(self) -> str:
        return "voyage"

    async def embed_images(self, image_base64s: List[str]) -> List[ImageEmbeddingResult]:
        batch_size = getattr(self.dense_embeddings, "batch_size", _DEFAULT_BATCH_SIZE)
        semaphore = asyncio.Semaphore(_CONCURRENCY_LIMIT)

        async def process_batch(batch_start: int, batch_imgs: List[str]) -> List[ImageEmbeddingResult]:
            async with semaphore:
                try:
                    embeddings = await self.dense_embeddings.aembed_documents(batch_imgs)
                    return [
                        ImageEmbeddingResult(index=batch_start + i, embedding=list(e))
                        for i, e in enumerate(embeddings)
                    ]
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"Voyage batch {batch_start} failed: {e}")
                    return [
                        ImageEmbeddingResult(index=batch_start + i, error=str(e))
                        for i in range(len(batch_imgs))
                    ]

        batches = [
            (start, image_base64s[start:start + batch_size])
            for start in range(0, len(image_base64s), batch_size)
        ]
        results = await asyncio.gather(*[process_batch(s, imgs) for s, imgs in batches])
        flattened: List[ImageEmbeddingResult] = []
        for r in results:
            flattened.extend(r)
        return flattened
