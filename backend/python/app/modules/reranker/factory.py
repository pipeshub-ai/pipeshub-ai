"""Builds an ``IReranker`` from a stored reranker model config.

Each provider is one entry in ``_BUILDERS``; adding a provider adds an entry
and never edits the others.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.config.constants.ai_models import (
    COHERE_RERANK_URL,
    DEFAULT_RERANKER_MODEL,
    EMBEDDING_SERVER_REQUEST_TIMEOUT_SECONDS,
    JINA_RERANK_URL,
    REMOTE_RERANK_REQUEST_TIMEOUT_SECONDS,
    VOYAGE_RERANK_URL,
)
from app.modules.reranker.http_reranker import HttpReranker
from app.modules.reranker.interface import IReranker
from app.modules.reranker.wire_formats import (
    CohereWireFormat,
    RerankWireFormat,
    VoyageWireFormat,
)
from app.utils.aimodels import RerankerProvider
from app.utils.embedding_server_client import model_server_v1_url

_Builder = Callable[[dict[str, Any], float | None], IReranker]


def create_reranker(config: dict[str, Any], *, timeout_seconds: float | None = None) -> IReranker:
    """``config`` is one entry of the ``reranker`` bucket of the AI models config.

    ``timeout_seconds`` bounds one HTTP call; each provider has a sensible
    default (long for the local server, whose first call may download the
    model). Raises ``ValueError`` for an unknown provider or missing field.
    """
    provider = config.get("provider")
    try:
        builder = _BUILDERS[RerankerProvider(provider)]
    except ValueError as exc:
        raise ValueError(f"Unsupported reranker provider: {provider!r}") from exc
    return builder(config.get("configuration") or {}, timeout_seconds)


def _model(configuration: dict[str, Any]) -> str:
    """First name of a comma-separated model field, as for embedding models."""
    names = [n.strip() for n in str(configuration.get("model") or "").split(",") if n.strip()]
    if not names:
        raise ValueError("Reranker configuration has no model name")
    return names[0]


def _endpoint(configuration: dict[str, Any]) -> str:
    endpoint = str(configuration.get("endpoint") or "").strip().rstrip("/")
    if not endpoint:
        raise ValueError("Reranker configuration has no endpoint")
    return endpoint


def _local(model: str, configuration: dict[str, Any], timeout: float | None) -> IReranker:
    extra = {"trust_remote_code": True} if configuration.get("trustRemoteCode") else None
    return HttpReranker(
        url=f"{model_server_v1_url()}/rerank",
        model=model,
        wire_format=CohereWireFormat(extra_body=extra),
        timeout_seconds=timeout or EMBEDDING_SERVER_REQUEST_TIMEOUT_SECONDS,
    )


def _hosted(
    url: str, wire_format: RerankWireFormat, configuration: dict[str, Any], timeout: float | None,
) -> IReranker:
    return HttpReranker(
        url=url,
        model=_model(configuration),
        wire_format=wire_format,
        timeout_seconds=timeout or REMOTE_RERANK_REQUEST_TIMEOUT_SECONDS,
        api_key=configuration.get("apiKey") or None,
    )


_BUILDERS: dict[RerankerProvider, _Builder] = {
    RerankerProvider.DEFAULT: lambda c, t: _local(DEFAULT_RERANKER_MODEL, c, t),
    RerankerProvider.SENTENCE_TRANSFORMERS: lambda c, t: _local(_model(c), c, t),
    RerankerProvider.HUGGING_FACE: lambda c, t: _local(_model(c), c, t),
    RerankerProvider.COHERE: lambda c, t: _hosted(COHERE_RERANK_URL, CohereWireFormat(), c, t),
    RerankerProvider.JINA_AI: lambda c, t: _hosted(JINA_RERANK_URL, CohereWireFormat(), c, t),
    RerankerProvider.VOYAGE: lambda c, t: _hosted(VOYAGE_RERANK_URL, VoyageWireFormat(), c, t),
    RerankerProvider.OPENAI_COMPATIBLE: lambda c, t: _hosted(
        f"{_endpoint(c)}/rerank", CohereWireFormat(), c, t,
    ),
    RerankerProvider.LITELLM_PROXY: lambda c, t: _hosted(
        f"{_endpoint(c)}/rerank", CohereWireFormat(), c, t,
    ),
}
