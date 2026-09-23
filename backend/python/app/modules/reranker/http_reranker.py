"""The one ``IReranker`` implementation: an HTTP rerank API in a given wire format.

Hosted providers and the local model server differ only in URL, credentials
and wire format, so they share this client.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from app.modules.reranker.interface import IReranker, RerankerError, RerankHit

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.modules.reranker.wire_formats import RerankWireFormat


class HttpReranker(IReranker):
    def __init__(
        self,
        *,
        url: str,
        model: str,
        wire_format: RerankWireFormat,
        timeout_seconds: float,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = url
        self._model = model
        self._wire_format = wire_format
        self._timeout_seconds = timeout_seconds
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._transport = transport

    @property
    def model_name(self) -> str:
        return self._model

    async def rerank(
        self,
        query: str,
        documents: Sequence[str],
        top_n: int | None = None,
    ) -> list[RerankHit]:
        if not documents:
            return []
        body = self._wire_format.request_body(self._model, query, documents, top_n)
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds, transport=self._transport,
            ) as client:
                response = await client.post(self._url, json=body, headers=self._headers)
        except httpx.HTTPError as exc:
            raise RerankerError(f"Rerank request to {self._url} failed: {exc!r}") from exc
        if response.status_code >= 400:
            raise RerankerError(
                f"Rerank request to {self._url} returned HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RerankerError(f"Rerank response from {self._url} is not JSON") from exc

        hits = [
            hit for hit in self._wire_format.parse_hits(payload)
            if 0 <= hit.index < len(documents)
        ]
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits if top_n is None else hits[:top_n]
