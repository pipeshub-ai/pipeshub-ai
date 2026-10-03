"""Image embeddings for OpenAI-compatible endpoints.

Auto recognizes the advertised messages extension in the server's OpenAPI
schema. Data-URI input requires an explicit override: text-only servers can
successfully embed the URI string without decoding an image. Messages are
sent individually for compatibility with older servers.
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
_REQUEST_FORMATS = {"auto", "input", "vllm_messages"}


class OpenAICompatMultimodalProvider(IMultimodalEmbeddingProvider):
    def __init__(
        self,
        base_url: str | None,
        api_key: str | None,
        model_name: str | None,
        provider_label: str = "openAICompatible",
        request_format: str = "auto",
        normalize_fn: Callable[[str], Any] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url (endpoint) is required for OpenAI-compatible embeddings")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self._provider_label = provider_label
        if request_format not in _REQUEST_FORMATS:
            raise ValueError(f"Unsupported multimodal request format: {request_format}")
        self._request_format = request_format
        # Consumed by IMultimodalEmbeddingProvider.normalize().
        self._normalize_fn = normalize_fn
        self.logger = logger

    @property
    def provider_name(self) -> str:
        return self._provider_label

    async def embed_images(self, image_base64s: list[str]) -> list[ImageEmbeddingResult]:
        if not image_base64s:
            return []
        semaphore = asyncio.Semaphore(_CONCURRENCY_LIMIT)
        endpoint = f"{self.base_url}/embeddings"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async def process_batch(
            client: httpx.AsyncClient, batch_start: int, batch_imgs: list[str]
        ) -> list[ImageEmbeddingResult]:
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

            if request_format == "vllm_messages":
                results = await asyncio.gather(
                    *[
                        self._embed_via_messages(
                            client, endpoint, headers, index, uri, semaphore
                        )
                        for index, uri in valid
                    ]
                )
                return list(results) + invalid_results

            request_indices = [index for index, _ in valid]
            try:
                async with semaphore:
                    data = await self._post_input_format(
                        client, endpoint, headers, [uri for _, uri in valid]
                    )
            except Exception as standard_err:
                error = describe_request_error(standard_err)
                return [
                    ImageEmbeddingResult(index=index, error=error)
                    for index in request_indices
                ] + invalid_results
            return map_embedding_response(data, request_indices) + invalid_results

        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            request_format = self._request_format
            if request_format == "auto":
                request_format = await self._detect_request_format(client, headers)
                if request_format is None:
                    error = (
                        "Image embedding support could not be verified. "
                        "Choose the endpoint's image format in advanced settings "
                        "or disable Multimodal to use text embeddings."
                    )
                    return [
                        ImageEmbeddingResult(index=i, error=error)
                        for i in range(len(image_base64s))
                    ]
            batches = [
                (start, image_base64s[start:start + _BATCH_SIZE])
                for start in range(0, len(image_base64s), _BATCH_SIZE)
            ]
            results = await asyncio.gather(
                *[process_batch(client, s, imgs) for s, imgs in batches]
            )
        return [result for batch in results for result in batch]

    async def _detect_request_format(
        self, client: httpx.AsyncClient, headers: dict[str, str],
    ) -> str | None:
        """Recognize an advertised messages extension; never guess from a model name."""
        base = httpx.URL(self.base_url)
        prefix = base.path.rstrip("/")
        schema_path = prefix[:-3] if prefix.endswith("/v1") else prefix
        try:
            response = await client.get(
                str(base.copy_with(path=f"{schema_path}/openapi.json")),
                headers=headers, timeout=10.0,
            )
            response.raise_for_status()
            document = response.json()
            schema = document["paths"][f"{prefix}/embeddings"]["post"][
                "requestBody"]["content"]["application/json"]["schema"]
            pending = [schema]
            seen: set[str] = set()
            while pending:
                schema = pending.pop()
                if "messages" in schema.get("properties", {}):
                    return "vllm_messages"
                ref = schema.get("$ref", "")
                if ref.startswith("#/components/schemas/") and ref not in seen:
                    seen.add(ref)
                    pending.append(document["components"]["schemas"][ref.rsplit("/", 1)[-1]])
                for key in ("anyOf", "oneOf", "allOf"):
                    pending.extend(schema.get(key, []))
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            pass
        return None

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
        semaphore: asyncio.Semaphore,
    ) -> ImageEmbeddingResult:
        try:
            async with semaphore:
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
