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

from app.services.embeddings.multimodal._concurrent import (
    gather_or_cancel,
    to_thread_settled,
)
from app.services.embeddings.multimodal.interface import (
    ImageEmbeddingResult,
    IMultimodalEmbeddingProvider,
)
from app.services.llm_gateway.embedding_gateway import (
    EmbeddingRateLimited,
    get_embedding_gateway,
)
from app.services.llm_gateway.wire import refused_by_allowance

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


def _install_wire_hooks(client: object) -> None:
    """Count this client's requests in the wire scope of a gateway attempt (a client built outside the model factories has
    no hooks of its own)."""
    from app.services.llm_gateway import wire

    for http in wire.find_http_clients(client):
        wire.install_wire_hook(http)


class CohereMultimodalProvider(IMultimodalEmbeddingProvider):
    def __init__(
        self, api_key: str | None, model_name: str | None, logger: logging.Logger | None = None,
        sdk_retries: int | None = None,
    ) -> None:
        self.api_key = api_key
        self.sdk_retries = sdk_retries
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
        _install_wire_hooks(co)
        semaphore = asyncio.Semaphore(_CONCURRENCY_LIMIT)

        async def embed_single(i: int, image_base64: str) -> ImageEmbeddingResult:
            image_input = {
                "content": [{"type": "image_url", "image_url": {"url": image_base64}}]
            }
            async with semaphore:
                try:
                    # One retry layer: the SDK repeats nothing when told so, and the gateway repeats a failed request. In a
                    # thread that keeps the context, so the request is counted by the wire scope of the attempt.
                    options = {"max_retries": self.sdk_retries} if self.sdk_retries is not None else None
                    response = await get_embedding_gateway().call(
                        lambda: to_thread_settled(
                            lambda: co.embed(
                                model=self.model_name,
                                input_type=self.input_type,
                                embedding_types=["float"],
                                inputs=[image_input],
                                request_options=options,
                            ),
                        )
                    )
                    return ImageEmbeddingResult(
                        index=i, embedding=list(response.embeddings.float[0])
                    )
                except EmbeddingRateLimited:
                    raise  # the provider's wait goes to the record handler, which hands the record back
                except Exception as e:
                    if refused_by_allowance(e):
                        raise  # the job's send allowance is spent: not one image's failure
                    if self.logger:
                        # An oversized image is an expected per-image skip;
                        # anything else (auth, rate limit, network) is not and
                        # must not disappear into the result object unlogged.
                        if "image size must be at most" in str(e):
                            self.logger.warning(f"Skipping image {i}: {e}")
                        else:
                            self.logger.warning(f"Cohere embed failed for index {i}: {e}")
                    return ImageEmbeddingResult(index=i, error=str(e))

        return await gather_or_cancel(
            [embed_single(i, b64) for i, b64 in enumerate(image_base64s)]
        )
