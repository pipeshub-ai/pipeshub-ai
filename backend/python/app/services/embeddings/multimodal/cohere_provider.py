"""Cohere native multimodal image embedding.

Images go through the v2 ``inputs`` parameter, and Cohere documents that
``inputs`` accepts only ``search_query``/``search_document``/
``classification``/``clustering`` as ``input_type`` — ``"image"`` is
excluded from that set, even though it remains a valid enum value for the
older ``images`` parameter. On embed-v4.0 Cohere additionally states that
``"image"`` silently falls back to ``search_document`` and recommends
passing ``search_document`` directly.

Batch image embedding via ``inputs`` is an embed-v4.0 feature (up to 96
inputs); Cohere states embed-v3.0 does not support it at all — that
generation needs the separate ``images`` parameter, one image per call.
This provider therefore targets v4 and warns when configured with an
older model rather than silently sending a request the docs do not cover.
"""

import asyncio
import logging

from app.services.embeddings.multimodal.interface import (
    ImageEmbeddingResult,
    IMultimodalEmbeddingProvider,
)

_CONCURRENCY_LIMIT = 10


# The only input_type Cohere documents as valid for an image sent through
# the `inputs` parameter. Kept as a named constant so the reason survives.
_IMAGE_INPUT_TYPE = "search_document"


def supports_inputs_image_batch(model_name: str | None) -> bool:
    """Whether this model generation can embed images via ``inputs``."""
    name = (model_name or "").lower()
    return "v4" in name or "embed-4" in name


def cohere_image_input_type(model_name: str | None) -> str:
    """Always ``search_document`` — see the module docstring.

    ``model_name`` is retained so callers keep a single place to consult if
    Cohere ever re-introduces a generation-specific value.
    """
    return _IMAGE_INPUT_TYPE


class CohereMultimodalProvider(IMultimodalEmbeddingProvider):
    def __init__(self, api_key: str | None, model_name: str | None, logger: logging.Logger | None = None) -> None:
        self.api_key = api_key
        self.model_name = model_name
        self.logger = logger
        self.input_type = cohere_image_input_type(model_name)
        if logger and not supports_inputs_image_batch(model_name):
            logger.warning(
                "Cohere model %r predates embed-v4.0; Cohere does not support image "
                "embedding through the `inputs` parameter for that generation, so "
                "these requests may be rejected.", model_name,
            )

    @property
    def provider_name(self) -> str:
        return "cohere"

    async def embed_images(self, image_base64s: list[str]) -> list[ImageEmbeddingResult]:
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
                    if self.logger:
                        # An oversized image is an expected per-image skip;
                        # anything else (auth, rate limit, network) is not and
                        # must not disappear into the result object unlogged.
                        if "image size must be at most" in str(e):
                            self.logger.warning(f"Skipping image {i}: {e}")
                        else:
                            self.logger.warning(f"Cohere embed failed for index {i}: {e}")
                    return ImageEmbeddingResult(index=i, error=str(e))

        return await asyncio.gather(
            *[embed_single(i, b64) for i, b64 in enumerate(image_base64s)]
        )
