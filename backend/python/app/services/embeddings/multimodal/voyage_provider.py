"""Voyage native multimodal image embedding (voyage-multimodal-3).

Delegates to the already-multimodal-aware LangChain ``Embeddings`` instance
(``app.utils.custom_embeddings.VoyageEmbeddings``), which knows how to turn a
base64 image string into a ``voyage`` multimodal input part. This provider
only owns batching/concurrency around that call.
"""

import asyncio
import logging

from langchain_core.embeddings import Embeddings

from app.services.embeddings.multimodal.interface import (
    ImageEmbeddingResult,
    IMultimodalEmbeddingProvider,
)

_CONCURRENCY_LIMIT = 5
_DEFAULT_BATCH_SIZE = 7


class VoyageMultimodalProvider(IMultimodalEmbeddingProvider):
    def __init__(
        self, dense_embeddings: Embeddings, logger: logging.Logger | None = None,
    ) -> None:
        self.dense_embeddings = dense_embeddings
        self.logger = logger

    @property
    def provider_name(self) -> str:
        return "voyage"

    async def embed_images(self, image_base64s: list[str]) -> list[ImageEmbeddingResult]:
        batch_size = getattr(self.dense_embeddings, "batch_size", _DEFAULT_BATCH_SIZE)
        semaphore = asyncio.Semaphore(_CONCURRENCY_LIMIT)

        async def process_batch(batch_start: int, batch_imgs: list[str]) -> list[ImageEmbeddingResult]:
            async with semaphore:
                try:
                    embeddings = await self.dense_embeddings.aembed_documents(batch_imgs)
                    # A short response must not silently drop its tail --
                    # every input index owes the caller a result.
                    results = [
                        ImageEmbeddingResult(index=batch_start + i, embedding=list(e))
                        for i, e in enumerate(embeddings)
                    ]
                    results.extend(
                        ImageEmbeddingResult(
                            index=batch_start + i,
                            error="no embedding returned for this image",
                        )
                        for i in range(len(embeddings), len(batch_imgs))
                    )
                    return results
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
        flattened: list[ImageEmbeddingResult] = []
        for r in results:
            flattened.extend(r)
        return flattened
