"""How each rerank HTTP API spells the same request and response.

Most rerank APIs (Cohere v2, Jina, LiteLLM, vLLM, Infinity and PipesHub's own
model server) share one shape; Voyage differs only in field names. A new
provider is a new ``RerankWireFormat``, not a change to the client.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from app.modules.reranker.interface import RerankerError, RerankHit

if TYPE_CHECKING:
    from collections.abc import Sequence


class RerankWireFormat(ABC):
    @abstractmethod
    def request_body(
        self, model: str, query: str, documents: Sequence[str], top_n: int | None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def parse_hits(self, response: Any) -> list[RerankHit]:  # noqa: ANN401 - decoded JSON
        raise NotImplementedError


class CohereWireFormat(RerankWireFormat):
    """``{model, query, documents, top_n}`` → ``{results: [{index, relevance_score}]}``."""

    def __init__(self, extra_body: dict[str, Any] | None = None) -> None:
        self._extra_body = dict(extra_body or {})

    def request_body(
        self, model: str, query: str, documents: Sequence[str], top_n: int | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "query": query,
            "documents": list(documents),
            **self._extra_body,
        }
        if top_n is not None:
            body["top_n"] = top_n
        return body

    def parse_hits(self, response: Any) -> list[RerankHit]:  # noqa: ANN401
        return _hits(response, "results")


class VoyageWireFormat(RerankWireFormat):
    """``{model, query, documents, top_k}`` → ``{data: [{index, relevance_score}]}``."""

    def request_body(
        self, model: str, query: str, documents: Sequence[str], top_n: int | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "query": query,
            "documents": list(documents),
            # Voyage rejects over-long inputs unless told to truncate them.
            "truncation": True,
        }
        if top_n is not None:
            body["top_k"] = top_n
        return body

    def parse_hits(self, response: Any) -> list[RerankHit]:  # noqa: ANN401
        return _hits(response, "data")


def _hits(response: Any, key: str) -> list[RerankHit]:  # noqa: ANN401
    items = response.get(key) if isinstance(response, dict) else None
    if not isinstance(items, list):
        raise RerankerError(f"Rerank response has no '{key}' list")
    try:
        return [
            RerankHit(index=int(item["index"]), score=float(item["relevance_score"]))
            for item in items
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise RerankerError(f"Malformed rerank result: {exc}") from exc
