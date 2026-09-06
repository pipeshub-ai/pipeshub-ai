"""HTTP client for the local embedding server with retries."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from langchain_core.embeddings import Embeddings
from langchain_openai.embeddings import OpenAIEmbeddings

from app.config.constants.ai_models import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_SERVER_URL,
    EMBEDDING_SERVER_MAX_RETRIES,
    EMBEDDING_SERVER_REQUEST_TIMEOUT_SECONDS,
)
from app.services.messaging.backpressure import get_default_backpressure_coordinator
from app.utils.embedding_retry import await_with_retry, call_with_retry
from app.utils.logger import create_logger

if TYPE_CHECKING:
    from app.services.messaging.backpressure import BackpressureCoordinator

logger = create_logger("embedding_server_client")

_EMBEDDING_SERVER_API_KEY = "not-needed"
_EMBEDDING_SERVER_SERVICE_NAME = "EmbeddingServer"


def _embedding_server_base_url() -> str:
    base = os.getenv("EMBEDDING_SERVER_URL", DEFAULT_EMBEDDING_SERVER_URL).rstrip("/")
    if not base.endswith("/v1"):
        return f"{base}/v1"
    return base


def _embedding_server_max_retries() -> int:
    raw = os.getenv("EMBEDDING_SERVER_MAX_RETRIES")
    if raw is None:
        return EMBEDDING_SERVER_MAX_RETRIES
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("Invalid EMBEDDING_SERVER_MAX_RETRIES=%r; using default", raw)
        return EMBEDDING_SERVER_MAX_RETRIES


def _embedding_server_timeout() -> float:
    raw = os.getenv("EMBEDDING_SERVER_TIMEOUT")
    if raw is None:
        return EMBEDDING_SERVER_REQUEST_TIMEOUT_SECONDS
    try:
        return max(1.0, float(raw))
    except ValueError:
        logger.warning("Invalid EMBEDDING_SERVER_TIMEOUT=%r; using default", raw)
        return EMBEDDING_SERVER_REQUEST_TIMEOUT_SECONDS


class EmbeddingServerEmbeddings(Embeddings):
    """LangChain embeddings client for the local embedding server with retries."""

    def __init__(
        self,
        *,
        model: str | None = None,
        max_retries: int | None = None,
        timeout: float | None = None,
        trust_remote_code: bool = False,
        backpressure_coordinator: "BackpressureCoordinator | None" = None,
    ) -> None:
        self.model = model or DEFAULT_EMBEDDING_MODEL
        self.max_retries = max_retries if max_retries is not None else _embedding_server_max_retries()
        self.timeout = timeout if timeout is not None else _embedding_server_timeout()
        self.trust_remote_code = trust_remote_code
        # Falls back to the process-wide default so this shares a pause
        # signal with ParsingClient/DoclingClient in the same indexing
        # worker without every construction site needing to plumb one in.
        self._backpressure_coordinator = backpressure_coordinator or get_default_backpressure_coordinator()
        extra_body = {"trust_remote_code": True} if trust_remote_code else None
        self._inner = OpenAIEmbeddings(
            model=self.model,
            api_key=_EMBEDDING_SERVER_API_KEY,
            base_url=_embedding_server_base_url(),
            check_embedding_ctx_length=False,
            max_retries=0,
            timeout=self.timeout,
            extra_body=extra_body,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return call_with_retry(
            lambda: self._inner.embed_documents(texts),
            max_retries=self.max_retries,
            operation="embed_documents",
            service_name=_EMBEDDING_SERVER_SERVICE_NAME,
            backpressure_coordinator=self._backpressure_coordinator,
        )

    def embed_query(self, text: str) -> list[float]:
        return call_with_retry(
            lambda: self._inner.embed_query(text),
            max_retries=self.max_retries,
            operation="embed_query",
            service_name=_EMBEDDING_SERVER_SERVICE_NAME,
            backpressure_coordinator=self._backpressure_coordinator,
        )

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await await_with_retry(
            lambda: self._inner.aembed_documents(texts),
            max_retries=self.max_retries,
            operation="aembed_documents",
            service_name=_EMBEDDING_SERVER_SERVICE_NAME,
            backpressure_coordinator=self._backpressure_coordinator,
        )

    async def aembed_query(self, text: str) -> list[float]:
        return await await_with_retry(
            lambda: self._inner.aembed_query(text),
            max_retries=self.max_retries,
            operation="aembed_query",
            service_name=_EMBEDDING_SERVER_SERVICE_NAME,
            backpressure_coordinator=self._backpressure_coordinator,
        )


def get_embedding_server_embeddings(
    model_name: str | None = None,
    *,
    trust_remote_code: bool = False,
) -> Embeddings:
    return EmbeddingServerEmbeddings(
        model=model_name,
        trust_remote_code=trust_remote_code,
    )
