"""Abstraction for provider-specific image (multimodal) embedding.

Every provider that can turn raw image bytes into a dense vector implements
``IMultimodalEmbeddingProvider``. The interface intentionally only produces
embeddings — it knows nothing about ``VectorPoint``, block metadata, or
``page_content``. That keeps provider implementations free of indexing
concerns and lets ``VectorStore`` build points from ``ImageEmbeddingResult``
in one shared place (see ``VectorStore._build_image_points``), instead of
each provider duplicating point-construction logic.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ImageEmbeddingResult:
    """Result of embedding a single image, keyed by its position in the
    input list so callers can zip results back to their source chunks even
    when some images fail or are skipped (e.g. oversized, invalid base64).
    """
    index: int
    embedding: Optional[List[float]] = None
    error: Optional[str] = None


class IMultimodalEmbeddingProvider(ABC):
    """Provider-specific strategy for embedding a batch of images.

    Implementations own their own batching/concurrency policy (Cohere/Jina
    batch multiple images per HTTP call, Gemini embeds one image per call,
    etc.) but must always return a result for every input index — either an
    embedding or an error — so ``embed_images`` never silently drops entries.
    """

    @abstractmethod
    async def embed_images(self, image_base64s: List[str]) -> List[ImageEmbeddingResult]:
        """Embed a batch of base64-encoded (optionally data-URI-prefixed) images."""
        ...

    def supports_multimodal(self) -> bool:
        """Whether this provider instance can natively embed images.

        Defaults to True; providers that can only be constructed when native
        image embedding is actually possible (the common case) don't need to
        override this. Providers with a runtime-conditional capability
        (e.g. an Ollama model that isn't vision-capable) should override.
        """
        return True

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Short identifier for logging/metrics (e.g. 'cohere', 'gemini')."""
        ...
