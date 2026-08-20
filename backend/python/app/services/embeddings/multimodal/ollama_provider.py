"""Ollama image embedding.

As of this writing, Ollama's ``/api/embed`` endpoint only accepts text
``input`` — there is no ``images`` field, and native multimodal embedding
support is an open feature request (ollama/ollama#5304, #16076), not yet
shipped. ``supports_multimodal`` therefore returns ``False`` by default so
callers don't silently store broken/absent vectors when a user marks an
Ollama embedding config as multimodal.

``embed_images`` still makes a best-effort call using the schema proposed
upstream (``images: [...]`` alongside ``input``) so this provider starts
working automatically against any Ollama build/fork that lands that support,
without a code change here. Until then, every image comes back as an
``ImageEmbeddingResult`` error and the caller should rely on the existing
VLM-description fallback (embedding the multimodal LLM's text description of
the image instead — see ``VectorStore.index_documents``) rather than this
provider's native path.
"""

import asyncio
import logging

import httpx

from app.services.embeddings.multimodal.interface import (
    ImageEmbeddingResult,
    IMultimodalEmbeddingProvider,
)

_CONCURRENCY_LIMIT = 5
_DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaMultimodalProvider(IMultimodalEmbeddingProvider):
    def __init__(
        self,
        base_url: str | None,
        model_name: str | None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self.model_name = model_name
        self.logger = logger

    def supports_multimodal(self) -> bool:
        return False

    @property
    def provider_name(self) -> str:
        return "ollama"

    async def embed_images(self, image_base64s: list[str]) -> list[ImageEmbeddingResult]:
        semaphore = asyncio.Semaphore(_CONCURRENCY_LIMIT)

        async def embed_single(
            client: httpx.AsyncClient, i: int, image_base64: str
        ) -> ImageEmbeddingResult:
            async with semaphore:
                try:
                    resp = await client.post(
                        f"{self.base_url}/api/embed",
                        json={"model": self.model_name, "images": [image_base64]},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    embeddings = data.get("embeddings") or []
                    if not embeddings:
                        return ImageEmbeddingResult(
                            index=i,
                            error=(
                                "Ollama did not return an embedding for the image — "
                                "this Ollama build likely doesn't support native "
                                "multimodal embedding yet."
                            ),
                        )
                    return ImageEmbeddingResult(index=i, embedding=list(embeddings[0]))
                except Exception as e:
                    if self.logger:
                        self.logger.warning(
                            f"Ollama image embed failed for index {i} "
                            f"(native multimodal embedding likely unsupported): {e}"
                        )
                    return ImageEmbeddingResult(index=i, error=str(e))

        async with httpx.AsyncClient(timeout=60.0) as client:
            return await asyncio.gather(
                *[embed_single(client, i, b64) for i, b64 in enumerate(image_base64s)]
            )
