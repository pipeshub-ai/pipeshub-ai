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

Two incompatible multimodal conventions exist in the wild, so both are
tried: the standard ``input`` schema (used by routers such as
Requesty/LiteLLM proxying to natively multimodal models like Gemini
Embedding 2), then vLLM's chat-``messages`` extension, which self-hosted
vLLM multimodal embedding servers use instead and which accepts only one
image per request.

LM Studio's local embedding server uses the same OpenAI-compatible shape, so
it is routed through this same provider.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import httpx

from app.services.embeddings.multimodal._response import (
    describe_request_error,
    map_embedding_response,
)
from app.services.embeddings.multimodal.interface import (
    ImageEmbeddingResult,
    IMultimodalEmbeddingProvider,
)

_CONCURRENCY_LIMIT = 5
_BATCH_SIZE = 16
_REQUEST_TIMEOUT_SECONDS = 60.0


class OpenAICompatMultimodalProvider(IMultimodalEmbeddingProvider):
    def __init__(
        self,
        base_url: str | None,
        api_key: str | None,
        model_name: str | None,
        provider_label: str = "openAICompatible",
        normalize_fn: Callable[[str], Any] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url (endpoint) is required for OpenAI-compatible embeddings")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self._provider_label = provider_label
        # Consumed by IMultimodalEmbeddingProvider.normalize().
        self._normalize_fn = normalize_fn
        self.logger = logger

    @property
    def provider_name(self) -> str:
        return self._provider_label

    async def embed_images(self, image_base64s: list[str]) -> list[ImageEmbeddingResult]:
        semaphore = asyncio.Semaphore(_CONCURRENCY_LIMIT)
        endpoint = f"{self.base_url}/embeddings"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async def process_batch(
            client: httpx.AsyncClient, batch_start: int, batch_imgs: list[str]
        ) -> list[ImageEmbeddingResult]:
            async with semaphore:
                valid: list[tuple[int, str]] = []
                invalid_results: list[ImageEmbeddingResult] = []
                for offset, img in enumerate(batch_imgs):
                    index = batch_start + offset
                    normalized = await self.normalize(img)
                    if normalized:
                        valid.append((index, _as_data_uri(img, normalized)))
                    else:
                        invalid_results.append(
                            ImageEmbeddingResult(index=index, error="invalid image data")
                        )
                if not valid:
                    return invalid_results

                request_indices = [index for index, _ in valid]
                try:
                    data = await self._post_input_format(
                        client, endpoint, headers, [uri for _, uri in valid]
                    )
                except Exception as standard_err:
                    # vLLM's `messages` extension never adopted the `input`
                    # schema and takes one image per request, so this arm
                    # cannot batch.
                    fallback = await asyncio.gather(
                        *[
                            self._embed_via_messages(client, endpoint, headers, index, uri)
                            for index, uri in valid
                        ]
                    )
                    if self.logger and any(r.embedding is None for r in fallback):
                        self.logger.warning(
                            f"{self._provider_label} image embed batch {batch_start} failed "
                            f"(endpoint may not support multimodal embedding input): "
                            f"input-format error={describe_request_error(standard_err)}"
                        )
                    return list(fallback) + invalid_results
                return map_embedding_response(data, request_indices) + invalid_results

        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            batches = [
                (start, image_base64s[start:start + _BATCH_SIZE])
                for start in range(0, len(image_base64s), _BATCH_SIZE)
            ]
            results = await asyncio.gather(
                *[process_batch(client, s, imgs) for s, imgs in batches]
            )
        flattened: list[ImageEmbeddingResult] = []
        for r in results:
            flattened.extend(r)
        return flattened

    async def _post_input_format(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        headers: dict[str, str],
        image_uris: list[str],
    ) -> object:
        resp = await client.post(
            endpoint,
            headers=headers,
            json={
                "model": self.model_name,
                "input": image_uris,
                "encoding_format": "float",
            },
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    async def _embed_via_messages(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        headers: dict[str, str],
        index: int,
        image_uri: str,
    ) -> ImageEmbeddingResult:
        try:
            resp = await client.post(
                endpoint,
                headers=headers,
                json={
                    "model": self.model_name,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": image_uri}}
                            ],
                        }
                    ],
                    "encoding_format": "float",
                },
            )
            resp.raise_for_status()
            return map_embedding_response(resp.json().get("data", []), [index])[0]
        except Exception as vllm_err:
            return ImageEmbeddingResult(index=index, error=describe_request_error(vllm_err))


def _as_data_uri(original: str, normalized: str) -> str:
    """Image-capable servers expect a data URI, not a bare base64 payload."""
    stripped = original.strip()
    if stripped.startswith("data:"):
        return stripped
    return f"data:image/jpeg;base64,{normalized}"
