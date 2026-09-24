"""Abstraction for provider-specific image (multimodal) embedding.

Every provider that can turn raw image bytes into a dense vector implements
``IMultimodalEmbeddingProvider``. The interface intentionally only produces
embeddings — it knows nothing about ``VectorPoint``, block metadata, or
``page_content``. That keeps provider implementations free of indexing
concerns and lets ``VectorStore`` build points from ``ImageEmbeddingResult``
in one shared place (see ``VectorStore._build_image_points``), instead of
each provider duplicating point-construction logic.
"""

import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.utils.image_utils import normalize_image_to_base64


@dataclass
class ImageEmbeddingResult:
    """Result of embedding a single image, keyed by its position in the
    input list so callers can zip results back to their source chunks even
    when some images fail or are skipped (e.g. oversized, invalid base64).
    """
    index: int
    embedding: list[float] | None = None
    error: str | None = None


class IMultimodalEmbeddingProvider(ABC):
    """Provider-specific strategy for embedding a batch of images.

    Implementations own their own batching/concurrency policy (Cohere/Jina
    batch multiple images per HTTP call, Gemini embeds one image per call,
    etc.) but must always return a result for every input index — either an
    embedding or an error — so ``embed_images`` never silently drops entries.
    """

    # Set from ``MultimodalProviderConfig.normalize_fn`` by subclasses that
    # accept one; ``None`` falls back to the shared utility.
    _normalize_fn: Callable[[str], Any] | None = None

    @abstractmethod
    async def embed_images(self, image_base64s: list[str]) -> list[ImageEmbeddingResult]:
        """Embed a batch of base64-encoded (optionally data-URI-prefixed) images."""
        ...

    async def normalize(self, image_ref: str) -> str | None:
        """Strip any data-URI prefix and validate the payload as base64.

        Lives here so every provider that needs it resolves the injected
        ``normalize_fn`` the same way. The injected callable may be sync or
        async — ``VectorStore`` passes an async instance method so its tests
        can patch normalisation in one place.
        """
        fn = self._normalize_fn or normalize_image_to_base64
        result = fn(image_ref)
        if inspect.isawaitable(result):
            result = await result
        return result

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
