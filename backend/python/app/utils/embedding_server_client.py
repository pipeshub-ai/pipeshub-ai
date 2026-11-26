"""HTTP client for the local embedding server with retries."""

from __future__ import annotations

import os
import time
from contextvars import ContextVar
from typing import TYPE_CHECKING

import httpx
import openai
from langchain_core.embeddings import Embeddings
from langchain_openai.embeddings import OpenAIEmbeddings

from app.config.constants.ai_models import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_SERVER_URL,
    EMBEDDING_PRIORITY_HEADER,
    EMBEDDING_PRIORITY_QUERY,
    EMBEDDING_SERVER_MAX_RETRIES,
    EMBEDDING_SERVER_REQUEST_TIMEOUT_SECONDS,
    PARSE_CLASS_HEADER,
)
from app.exceptions.indexing_exceptions import EmbeddingPrecisionUnknownError
from app.modules.parse_engine.pools import current_class
from app.services.messaging.backpressure import get_default_backpressure_coordinator
from app.utils.embedding_gate import BulkGate, get_bulk_gate
from app.utils.embedding_retry import await_with_retry, call_with_retry
from app.utils.logger import create_logger
from app.utils.precision_witness import get_precision_witness
from harness_core.deadline import CONNECT_TIMEOUT_S, bounded_read

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


PRECISION_HEADER = "x-embedding-precision"
PRECISION_FALLBACK_HEADER = "x-embedding-precision-fallback"
_ANNOUNCEMENT_TTL_SECONDS = 30.0
# The precision headers of every response of the call being made; a list so tasks the call spawns share it.
_responses: ContextVar[list[tuple[str | None, bool]] | None] = ContextVar("embedding_responses", default=None)


def _note_response(response: httpx.Response) -> None:
    # Hooks run for error responses too. Only a response that carries vectors can say what made them; a 503 from
    # a proxy or a restarting server names nothing, and the retry that follows is the call's real answer.
    if not response.is_success:
        return
    seen = _responses.get()
    if seen is not None:
        seen.append((response.headers.get(PRECISION_HEADER), response.headers.get(PRECISION_FALLBACK_HEADER) == "1"))


async def _note_response_async(response: httpx.Response) -> None:
    _note_response(response)


def _begin_attempt() -> None:
    """A new attempt of the call being made: what earlier attempts saw produced vectors that were thrown away."""
    seen = _responses.get()
    if seen is not None:
        seen.clear()


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
        self.base_url = _embedding_server_base_url()
        # Tokens the server's model adds to every input; set when the limit is read.
        self.prompt_tokens = 0
        # How the server stores the model's weights (fp32 or int8-w), as of the last read_precision().
        self.precision: str | None = None
        # What the server's setting asks for (fp32 or int8-w), as of the last read; None until read or for an older server.
        self.intent: str | None = None
        # Set when a call lost its connection: the server may have restarted with another precision.
        self._precision_stale = False
        # When /v1/models last showed whether this server announces its precision, and the answer.
        self._announcement: tuple[float, bool] | None = None
        self._http = httpx.Client(timeout=self.timeout, event_hooks={"response": [_note_response]})
        self._http_async = httpx.AsyncClient(timeout=self.timeout, event_hooks={"response": [_note_response_async]})
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
            # A short connect: an unroutable embedding server fails in seconds, not after the read bound (ERR-UX).
            timeout=httpx.Timeout(self.timeout, connect=CONNECT_TIMEOUT_S),
            extra_body=extra_body,
            http_client=self._http,
            http_async_client=self._http_async,
        )
        # Search queries are marked so the server encodes them ahead of
        # indexing batches (embedding_main.EncodeGate). Documents go
        # unmarked, which the server treats as bulk.
        self._class_inners: dict[str, OpenAIEmbeddings] = {}
        self._query_inner = OpenAIEmbeddings(
            model=self.model,
            api_key=_EMBEDDING_SERVER_API_KEY,
            base_url=_embedding_server_base_url(),
            check_embedding_ctx_length=False,
            max_retries=0,
            timeout=httpx.Timeout(self.timeout, connect=CONNECT_TIMEOUT_S),
            extra_body=extra_body,
            default_headers={EMBEDDING_PRIORITY_HEADER: EMBEDDING_PRIORITY_QUERY},
            http_client=self._http,
            http_async_client=self._http_async,
        )

    def _documents_inner(self) -> OpenAIEmbeddings:
        """The client for a document batch. Under ``PARSE_ADMISSION=classes`` a batch of a classified record
        carries its class, so the server's gate serves small records ahead of large and huge ones."""
        cls = current_class()
        if cls is None:
            return self._inner
        inner = self._class_inners.get(cls.value)
        if inner is None:
            inner = self._class_inners[cls.value] = OpenAIEmbeddings(
                model=self.model,
                api_key=_EMBEDDING_SERVER_API_KEY,
                base_url=_embedding_server_base_url(),
                check_embedding_ctx_length=False,
                max_retries=0,
                timeout=self.timeout,
                extra_body={"trust_remote_code": True} if self.trust_remote_code else None,
                default_headers={PARSE_CLASS_HEADER: cls.value},
            )
        return inner

    def bulk_gate(self) -> BulkGate:
        """The process-wide gate for this server's bulk slots."""
        return get_bulk_gate(self.base_url)

    async def _listed_model(self) -> object | None:
        client = openai.AsyncOpenAI(
            base_url=_embedding_server_base_url(), api_key=_EMBEDDING_SERVER_API_KEY,
            max_retries=0, timeout=self.timeout,
        )
        try:
            page = await client.models.list()
        finally:
            await client.close()
        return next((item for item in page.data if item.id == self.model), None)

    async def max_input_tokens(self) -> int | None:
        """The model's longest input as the server reports it, or None."""
        try:
            item = await self._listed_model()
        except openai.OpenAIError:
            logger.error("Embedding server did not report the model's input limit", exc_info=True)
            return None
        if item is None:
            return None
        overhead = getattr(item, "prompt_tokens", 0)
        self.prompt_tokens = overhead if isinstance(overhead, int) and overhead > 0 else 0
        limit = getattr(item, "max_seq_length", None)
        return limit if isinstance(limit, int) and limit > 0 else None

    async def _read_precision_once(self) -> bool:
        """One read of the server's answer; False when it could not be had (the last known value stays)."""
        try:
            item = await self._listed_model()
        except openai.OpenAIError:
            logger.warning("Embedding server did not report its weight precision", exc_info=True)
            return False
        self._announcement = (time.monotonic(), self._announces(item))
        precision = getattr(item, "precision", None)
        if isinstance(precision, str) and precision:
            self.precision = precision
        intent = getattr(item, "precision_intent", None)
        if isinstance(intent, str) and intent:
            self.intent = intent
        return True

    async def read_precision(self) -> str | None:
        """Ask the server how the loaded weights are stored; keeps the last known answer when it cannot say."""
        await self._read_precision_once()
        return self.precision

    @staticmethod
    def _announces(item: object | None) -> bool:
        """Whether the server lists the precision fields at all (an older server does not)."""
        extra = getattr(item, "model_extra", None) or {}
        return "precision_intent" in extra or hasattr(item, "precision_intent")

    def _fresh_announcement(self) -> bool | None:
        cached = self._announcement
        if cached is not None and time.monotonic() - cached[0] < _ANNOUNCEMENT_TTL_SECONDS:
            return cached[1]
        return None

    def _listed_model_sync(self) -> object | None:
        client = openai.OpenAI(
            base_url=_embedding_server_base_url(), api_key=_EMBEDDING_SERVER_API_KEY,
            max_retries=0, timeout=self.timeout,
        )
        try:
            page = client.models.list()
        finally:
            client.close()
        return next((item for item in page.data if item.id == self.model), None)

    async def announces_precision(self) -> bool:
        """Cached 30 s: a server that lists ``precision_intent`` stamps every response; an older one stamps none."""
        fresh = self._fresh_announcement()
        if fresh is not None:
            return fresh
        try:
            item = await self._listed_model()
        except openai.OpenAIError:
            # An unreachable server cannot answer the embedding either; fail closed.
            return True
        announced = self._announces(item)
        self._announcement = (time.monotonic(), announced)
        return announced

    def announces_precision_sync(self) -> bool:
        """The synchronous twin, for a worker thread: the same cache, the same discovery, the same fail-closed answer."""
        fresh = self._fresh_announcement()
        if fresh is not None:
            return fresh
        try:
            item = self._listed_model_sync()
        except openai.OpenAIError:
            return True
        announced = self._announces(item)
        self._announcement = (time.monotonic(), announced)
        return announced

    def _settle_witness(self, seen: list[tuple[str | None, bool]], announced: bool) -> tuple[str, bool]:  # noqa: FBT001
        """The one precision (and fallback flag) of a call, from the headers of its responses."""
        unnamed = [precision is None for precision, _ in seen]
        if any(unnamed) and not all(unnamed):
            # One call is split into several requests: some answered with a precision and some without (a rolling
            # upgrade). Whichever the writer is told, part of the vectors are of the other kind.
            raise EmbeddingPrecisionUnknownError(
                "Some responses of one call named their weight precision and some did not",
            )
        if not seen or all(unnamed):
            if announced:
                raise EmbeddingPrecisionUnknownError(
                    "The embedding server announces its weight precision but did not name it on a response",
                )
            return "fp32", False  # an older server: int8 never shipped without the header
        precisions = {precision for precision, _ in seen}
        if len(precisions) > 1:
            raise EmbeddingPrecisionUnknownError("Responses of one call came from weights of different precision")
        precision = next(iter(precisions))
        fallback = any(flag for _, flag in seen)
        get_precision_witness().record(self.model, str(precision), fallback)
        return str(precision), fallback

    async def aembed_documents_witnessed(
        self, texts: list[str], *, single_attempt: bool = False,
    ) -> tuple[list[list[float]], str, bool]:
        """Vectors and the precision of the weights that made them, from the response headers."""
        seen: list[tuple[str | None, bool]] = []
        token = _responses.set(seen)
        try:
            vectors = await self.aembed_documents(texts, single_attempt=single_attempt)
        finally:
            _responses.reset(token)
        announced = True
        if not seen or any(p is None for p, _ in seen):
            announced = await self.announces_precision()
        precision, fallback = self._settle_witness(seen, announced)
        return vectors, precision, fallback

    def embed_documents_witnessed(self, texts: list[str]) -> tuple[list[list[float]], str, bool]:
        """The synchronous twin, for callers that run in a worker thread."""
        seen: list[tuple[str | None, bool]] = []
        token = _responses.set(seen)
        try:
            vectors = self.embed_documents(texts)
        finally:
            _responses.reset(token)
        announced = True
        if not seen or any(p is None for p, _ in seen):
            announced = self.announces_precision_sync()
        precision, fallback = self._settle_witness(seen, announced)
        return vectors, precision, fallback

    def count_tokens(self, texts: list[str]) -> list[int]:
        """Tokens the server's model reads for each text. Raises when the server cannot say."""
        client = openai.OpenAI(
            base_url=_embedding_server_base_url(), api_key=_EMBEDDING_SERVER_API_KEY,
            max_retries=0, timeout=self.timeout,
        )
        try:
            body = {"model": self.model, "input": texts, "trust_remote_code": self.trust_remote_code}
            return call_with_retry(
                lambda: client.post("/tokenize", cast_to=object, body=body)["counts"],
                max_retries=self.max_retries,
                operation="count_tokens",
                service_name=_EMBEDDING_SERVER_SERVICE_NAME,
                backpressure_coordinator=self._backpressure_coordinator,
            )
        finally:
            client.close()

    def _embed_documents_attempt(self, texts: list[str]) -> list[list[float]]:
        _begin_attempt()
        return self._documents_inner().embed_documents(texts)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return call_with_retry(
            lambda: self._embed_documents_attempt(texts),
            max_retries=self.max_retries,
            operation="embed_documents",
            service_name=_EMBEDDING_SERVER_SERVICE_NAME,
            backpressure_coordinator=self._backpressure_coordinator,
        )

    def embed_query(self, text: str) -> list[float]:
        return call_with_retry(
            lambda: self._query_inner.embed_query(text),
            max_retries=self.max_retries,
            operation="embed_query",
            service_name=_EMBEDDING_SERVER_SERVICE_NAME,
            backpressure_coordinator=self._backpressure_coordinator,
        )

    async def _aembed_noting_lost_connection(self, texts: list[str]) -> list[list[float]]:
        _begin_attempt()
        try:
            return await self._documents_inner().aembed_documents(texts)
        except openai.APIConnectionError:
            self._precision_stale = True
            raise

    # On a run's tool call the retries together are bounded by the call's remaining time (ERR-UX).
    async def aembed_documents(self, texts: list[str], *, single_attempt: bool = False) -> list[list[float]]:
        # single_attempt: the caller retries, so no backoff sleep here (it may hold a bulk permit).
        vectors = await bounded_read("embedding", await_with_retry(
            lambda: self._aembed_noting_lost_connection(texts),
            max_retries=1 if single_attempt else self.max_retries,
            operation="aembed_documents",
            service_name=_EMBEDDING_SERVER_SERVICE_NAME,
            backpressure_coordinator=self._backpressure_coordinator,
        ))
        # Re-read only after a lost connection, never per batch. If the read fails
        # the flag stays set and the next batch tries again.
        if self._precision_stale and await self._read_precision_once():
            self._precision_stale = False
        return vectors

    async def aembed_query(self, text: str) -> list[float]:
        return await bounded_read("embedding", await_with_retry(
            lambda: self._query_inner.aembed_query(text),
            max_retries=self.max_retries,
            operation="aembed_query",
            service_name=_EMBEDDING_SERVER_SERVICE_NAME,
            backpressure_coordinator=self._backpressure_coordinator,
        ))


async def list_embedding_server_models() -> list[object]:
    """The in-cluster embedding server's ``/v1/models`` entries (what it serves and at which weight precision). A listing,
    not a model call: no org content goes out, so it is not behind an org's region pin (MG-15)."""
    client = openai.AsyncOpenAI(base_url=_embedding_server_base_url(), api_key=_EMBEDDING_SERVER_API_KEY, max_retries=0, timeout=5.0)
    try:
        page = await client.models.list()
    finally:
        await client.close()
    return list(page.data)


def get_embedding_server_embeddings(
    model_name: str | None = None,
    *,
    trust_remote_code: bool = False,
) -> Embeddings:
    return EmbeddingServerEmbeddings(
        model=model_name,
        trust_remote_code=trust_remote_code,
    )
