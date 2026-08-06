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
from typing import List, Optional

from app.services.embeddings.multimodal.interface import (
    IMultimodalEmbeddingProvider,
    ImageEmbeddingResult,
)
from app.utils.image_utils import get_mime_type_from_base64, normalize_image_to_base64

_CONCURRENCY_LIMIT = 5
_DEFAULT_MIME_TYPE = "image/png"


class GeminiMultimodalProvider(IMultimodalEmbeddingProvider):
    def __init__(self, api_key: Optional[str], model_name: Optional[str], logger=None) -> None:
        self.api_key = api_key
        self.model_name = self._normalize_model_name(model_name)
        self.logger = logger

    @staticmethod
    def _normalize_model_name(model_name: Optional[str]) -> str:
        name = model_name or ""
        return name if name.startswith("models/") else f"models/{name}"

    @property
    def provider_name(self) -> str:
        return "gemini"

    async def embed_images(self, image_base64s: List[str]) -> List[ImageEmbeddingResult]:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        semaphore = asyncio.Semaphore(_CONCURRENCY_LIMIT)

        async def embed_single(i: int, image_ref: str) -> ImageEmbeddingResult:
            normalized = normalize_image_to_base64(image_ref)
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
