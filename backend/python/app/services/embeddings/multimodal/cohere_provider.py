"""Cohere native multimodal image embedding (embed-v3.0 / embed-v4.0).

Cohere's image ``input_type`` differs by model generation: embed-v3.0
requires ``"image"``; embed-v4.0 deprecates that value in favour of
``"search_document"`` (Cohere's own docs recommend it, and ``"images"`` on
v4 silently falls back to ``search_document`` anyway).
"""

import asyncio
from typing import List, Optional

from app.services.embeddings.multimodal.interface import (
    IMultimodalEmbeddingProvider,
    ImageEmbeddingResult,
)

_CONCURRENCY_LIMIT = 10


def cohere_image_input_type(model_name: Optional[str]) -> str:
    name = (model_name or "").lower()
    if "v4" in name or "embed-4" in name:
        return "search_document"
    return "image"


class CohereMultimodalProvider(IMultimodalEmbeddingProvider):
    def __init__(self, api_key: Optional[str], model_name: Optional[str], logger=None) -> None:
        self.api_key = api_key
        self.model_name = model_name
        self.logger = logger
        self.input_type = cohere_image_input_type(model_name)

    @property
    def provider_name(self) -> str:
        return "cohere"

    async def embed_images(self, image_base64s: List[str]) -> List[ImageEmbeddingResult]:
        import cohere

        co = cohere.ClientV2(api_key=self.api_key)
        semaphore = asyncio.Semaphore(_CONCURRENCY_LIMIT)

        async def embed_single(i: int, image_base64: str) -> ImageEmbeddingResult:
            image_input = {
                "content": [{"type": "image_url", "image_url": {"url": image_base64}}]
            }
            async with semaphore:
                try:
                    loop = asyncio.get_running_loop()
                    response = await loop.run_in_executor(
                        None,
                        lambda: co.embed(
                            model=self.model_name,
                            input_type=self.input_type,
                            embedding_types=["float"],
                            inputs=[image_input],
                        ),
                    )
                    return ImageEmbeddingResult(
                        index=i, embedding=list(response.embeddings.float[0])
                    )
                except Exception as e:
                    if self.logger and "image size must be at most" in str(e):
                        self.logger.warning(f"Skipping image {i}: {e}")
                    return ImageEmbeddingResult(index=i, error=str(e))

        return await asyncio.gather(
            *[embed_single(i, b64) for i, b64 in enumerate(image_base64s)]
        )
