"""OpenAI-compatible (and LM Studio) image embedding.

The OpenAI ``/v1/embeddings`` API itself is text-only, but some self-hosted
servers exposed behind an "OpenAI-compatible" base URL (CLIP-style wrappers,
some Jina/Nomic deployments) accept a base64 data URI directly as an
``input`` item and return a native image embedding. There is no universal
standard for this, so this provider makes a best-effort POST and surfaces
any failure as a per-image error rather than raising — a server that only
accepts text will simply fail every image, and the existing
VLM-description fallback remains available for that case (see
``VectorStore.index_documents``).

LM Studio's local embedding server uses the same OpenAI-compatible shape, so
it is routed through this same provider.
"""

import asyncio
from typing import List, Optional

import httpx

from app.services.embeddings.multimodal.interface import (
    IMultimodalEmbeddingProvider,
    ImageEmbeddingResult,
)
from app.utils.image_utils import normalize_image_to_base64

_CONCURRENCY_LIMIT = 5
_BATCH_SIZE = 16


class OpenAICompatMultimodalProvider(IMultimodalEmbeddingProvider):
    def __init__(
        self,
        base_url: Optional[str],
        api_key: Optional[str],
        model_name: Optional[str],
        provider_label: str = "openAICompatible",
        logger=None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url (endpoint) is required for OpenAI-compatible embeddings")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self._provider_label = provider_label
        self.logger = logger

    @property
    def provider_name(self) -> str:
        return self._provider_label

    async def embed_images(self, image_base64s: List[str]) -> List[ImageEmbeddingResult]:
        semaphore = asyncio.Semaphore(_CONCURRENCY_LIMIT)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async def process_batch(
            client: httpx.AsyncClient, batch_start: int, batch_imgs: List[str]
        ) -> List[ImageEmbeddingResult]:
            async with semaphore:
                # Send the original (possibly data-URI-prefixed) image reference as the
                # input item — that is the shape image-capable OpenAI-compatible servers
                # expect; ``normalize_image_to_base64`` is only used here to validate
                # that each entry actually looks like image data before sending it.
                valid = [
                    (batch_start + j, img)
                    for j, img in enumerate(batch_imgs)
                    if normalize_image_to_base64(img)
                ]
                invalid_results = [
                    ImageEmbeddingResult(index=batch_start + j, error="invalid image data")
                    for j, img in enumerate(batch_imgs)
                    if not normalize_image_to_base64(img)
                ]
                if not valid:
                    return invalid_results
                try:
                    resp = await client.post(
                        f"{self.base_url}/embeddings",
                        headers=headers,
                        json={
                            "model": self.model_name,
                            "input": [img for _, img in valid],
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json().get("data", [])
                    valid_results = [
                        ImageEmbeddingResult(index=valid[i][0], embedding=list(item["embedding"]))
                        for i, item in enumerate(data)
                    ]
                    return valid_results + invalid_results
                except Exception as e:
                    if self.logger:
                        self.logger.warning(
                            f"{self._provider_label} image embed batch {batch_start} failed "
                            f"(endpoint may not support multimodal embedding input): {e}"
                        )
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
