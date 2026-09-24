"""Gemini native multimodal image embedding via the ``google-genai`` SDK.

LangChain's ``GoogleGenerativeAIEmbeddings`` only accepts text (see
``langchain_google_genai.embeddings.embed_documents``); there is no
LangChain-level API for embedding raw image bytes with Gemini's multimodal
embedding models (e.g. ``gemini-embedding-2``). This provider calls the
underlying ``google-genai`` SDK client directly — the same client the
LangChain integration wraps — passing the image as a ``types.Part``.
"""

import asyncio
import base64
import logging
from collections.abc import Callable
from typing import Any

from app.services.embeddings.multimodal.interface import (
    ImageEmbeddingResult,
    IMultimodalEmbeddingProvider,
)
from app.utils.image_utils import get_mime_type_from_base64

_CONCURRENCY_LIMIT = 5
_DEFAULT_MIME_TYPE = "image/png"
# gemini-embedding-2 is the first Gemini embedding model that accepts image
# input; gemini-embedding-001 is documented as text-only and the experimental
# gemini-embedding-exp-03-07 was shut down. Configuring either leaves every
# image failing with a provider-side error and no obvious cause.
_TEXT_ONLY_MODEL_MARKERS = ("gemini-embedding-001", "gemini-embedding-exp", "text-embedding-")


def supports_image_input(model_name: str | None) -> bool:
    name = (model_name or "").lower()
    return not any(marker in name for marker in _TEXT_ONLY_MODEL_MARKERS)


class GeminiMultimodalProvider(IMultimodalEmbeddingProvider):
    def __init__(
        self,
        api_key: str | None,
        model_name: str | None,
        normalize_fn: Callable[[str], Any] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.api_key = api_key
        self.model_name = self._normalize_model_name(model_name)
        # Consumed by IMultimodalEmbeddingProvider.normalize().
        self._normalize_fn = normalize_fn
        self.logger = logger
        if logger and not supports_image_input(model_name):
            logger.warning(
                "Gemini embedding model %r does not accept image input; use "
                "gemini-embedding-2 for multimodal embedding.", model_name,
            )

    @staticmethod
    def _normalize_model_name(model_name: str | None) -> str:
        name = model_name or ""
        return name if name.startswith("models/") else f"models/{name}"

    @property
    def provider_name(self) -> str:
        return "gemini"

    async def embed_images(self, image_base64s: list[str]) -> list[ImageEmbeddingResult]:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        semaphore = asyncio.Semaphore(_CONCURRENCY_LIMIT)

        async def embed_single(i: int, image_ref: str) -> ImageEmbeddingResult:
            normalized = await self.normalize(image_ref)
            if not normalized:
                return ImageEmbeddingResult(index=i, error="invalid image data")
            async with semaphore:
                try:
                    raw_bytes = base64.b64decode(normalized)
                    mime_type = get_mime_type_from_base64(normalized) or _DEFAULT_MIME_TYPE
                    response = await client.aio.models.embed_content(
                        model=self.model_name,
                        contents=[types.Part.from_bytes(data=raw_bytes, mime_type=mime_type)],
                    )
                    return ImageEmbeddingResult(
                        index=i, embedding=list(response.embeddings[0].values)
                    )
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"Gemini image embed failed for index {i}: {e}")
                    return ImageEmbeddingResult(index=i, error=str(e))

        return await asyncio.gather(
            *[embed_single(i, ref) for i, ref in enumerate(image_base64s)]
        )
