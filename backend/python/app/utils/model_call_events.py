"""Announces every model call as the ``llm.call.completed`` platform event.

Installed where every model is built (``aimodels._decorate_chat_model`` and
``_decorate_embeddings``), so a call is announced whoever makes it: a chat turn, an
agent, an indexing step. The facts are the provider and the model as configured, the
kind of call, how long it took, how it ended and the token counts the provider
reported. Never a prompt, a completion, a tool argument or an embedded text.

* A chat model gets a LangChain callback on the instance, which LangChain calls for
  every invocation of it (``invoke``, ``astream``, a structured-output chain).
* An embeddings client is wrapped by :class:`AnnouncedEmbeddings`. The local embedding
  server's client announces its own calls with :func:`embedding_call`, and so does the
  vector store for images sent through a provider's own SDK.
* An embeddings client returns vectors, not the provider's response. Code that does see
  the response hands the token count in it to :func:`note_usage` (or :func:`note_tokens`),
  and the call being announced carries it as ``tokens_in``. A provider whose response is
  never seen here is announced without one: nothing is estimated.
"""

from __future__ import annotations

import contextlib
import time
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.embeddings import Embeddings

from app import platform_events
from app.core import org_context
from app.utils.error_class import classify_error

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

    from langchain_core.language_models import BaseChatModel
    from langchain_core.outputs import LLMResult

COMPLETED = "llm.call.completed"
OTHER_MODEL = "other"
# Calls whose end LangChain never reported (it reports every one; this only bounds a bug).
_MAX_OPEN_CALLS = 10_000
# Token counts the provider reported for the embeddings call being made; a list so tasks and threads the call starts share it.
_reported_tokens: ContextVar[list[int] | None] = ContextVar("embedding_call_tokens", default=None)
# Where an OpenAI-compatible embeddings response says how many tokens it read, in the order they are tried.
_USAGE_KEYS = ("prompt_tokens", "input_tokens", "total_tokens")


def configured_model(model_name: str | None, config: Mapping[str, Any] | None) -> str:
    """The model's name as an administrator configured it: ``model_name`` when the configuration lists it, the
    first configured name when none was asked for, else ``other`` (a name that came from a request is not kept)."""
    configuration = (config or {}).get("configuration")
    raw = configuration.get("model") if isinstance(configuration, dict) else None
    names = [name.strip() for name in str(raw).split(",") if name.strip()] if raw is not None else []
    if model_name is None:
        return names[0] if names else OTHER_MODEL
    return model_name if model_name in names else OTHER_MODEL


def _call_site() -> str | None:
    """Which indexing step is calling, inside a record's indexing. Anywhere else, what the call is for when the
    caller bound a model call context (``chat``, ``agent``, ``search``, ...: the ``Workload`` names); else None."""
    from app.services.llm_gateway.gateway import current_call_site
    from app.services.model_identity.call_context import peek_call_context
    from app.utils.indexing_timing import current_timeline

    if current_timeline() is not None:
        return current_call_site()
    bound = peek_call_context()
    return bound.workload.value if bound is not None else None


def note_tokens(count: object) -> None:
    """The provider's response to the embeddings call being made counted ``count`` input tokens. Counts of several
    requests of one call add up. Outside an announced call, or for anything but a count, this does nothing."""
    reported = _reported_tokens.get()
    if reported is not None and isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        reported.append(count)


def awaits_tokens() -> bool:
    """True inside an embeddings call that will be announced: reading a token count out of its response is worth it."""
    return _reported_tokens.get() is not None


def note_usage(payload: object) -> None:
    """``note_tokens`` for an OpenAI-compatible embeddings response body: its ``usage`` object, when it has one."""
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return
    for key in _USAGE_KEYS:
        if key in usage:
            note_tokens(usage[key])
            return


def announce(
    provider: str,
    model: str,
    operation: str,
    started: float,
    *,
    error: BaseException | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    count: int | None = None,
) -> None:
    """One model call ended. ``started``: ``time.perf_counter()`` before it. Nothing is built when nobody listens."""
    if not platform_events.has_listeners(COMPLETED):
        return
    org = org_context.peek()
    platform_events.emit(
        "llm.call.completed",
        provider=provider,
        model=model,
        operation=operation,
        call_site=_call_site(),
        org_id=org.org_id if org is not None else None,
        duration_seconds=time.perf_counter() - started,
        outcome="success" if error is None else "failure",
        error_class=None if error is None else classify_error(error),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        count=count,
    )


def _tokens(response: LLMResult) -> tuple[int | None, int | None]:
    """The token counts the provider reported: from the messages' ``usage_metadata``, else the result's ``token_usage``."""
    tokens_in = tokens_out = None
    for generations in response.generations or ():
        for generation in generations:
            usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
            if isinstance(usage, dict):
                tokens_in = (tokens_in or 0) + int(usage.get("input_tokens") or 0)
                tokens_out = (tokens_out or 0) + int(usage.get("output_tokens") or 0)
    if tokens_in is None:
        usage = (response.llm_output or {}).get("token_usage") if isinstance(response.llm_output, dict) else None
        if isinstance(usage, dict):
            tokens_in = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            tokens_out = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    return tokens_in, tokens_out


class ModelCallAnnouncer(BaseCallbackHandler):
    """The callback on a chat model: one ``llm.call.completed`` per call of that model."""

    raise_error = False
    # Called on the caller's own task, so the org of the request is the one read.
    run_inline = True

    def __init__(self, provider: str, model: str) -> None:
        self.provider = provider
        self.model = model
        self._started: dict[UUID, float] = {}

    def _begin(self, run_id: UUID) -> None:
        if len(self._started) >= _MAX_OPEN_CALLS:
            self._started.clear()
        self._started[run_id] = time.perf_counter()

    def on_chat_model_start(self, serialized: Any, messages: Any, *, run_id: UUID, **kwargs: Any) -> None:  # noqa: ANN401 - LangChain's signature
        self._begin(run_id)

    def on_llm_start(self, serialized: Any, prompts: Any, *, run_id: UUID, **kwargs: Any) -> None:  # noqa: ANN401 - LangChain's signature
        self._begin(run_id)

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:  # noqa: ANN401 - LangChain's signature
        started = self._started.pop(run_id, None)
        if started is None or not platform_events.has_listeners(COMPLETED):
            return
        tokens_in, tokens_out = _tokens(response)
        announce(self.provider, self.model, "chat", started, tokens_in=tokens_in, tokens_out=tokens_out)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:  # noqa: ANN401 - LangChain's signature
        started = self._started.pop(run_id, None)
        if started is not None:
            announce(self.provider, self.model, "chat", started, error=error)


def announce_chat_model(model: BaseChatModel, provider: str, model_name: str | None, config: Mapping[str, Any] | None) -> BaseChatModel:
    """Put the announcing callback on a chat model, once. Returns ``model`` so it can sit in a decorator chain."""
    callbacks = list(getattr(model, "callbacks", None) or [])
    if not any(isinstance(callback, ModelCallAnnouncer) for callback in callbacks):
        callbacks.append(ModelCallAnnouncer(provider, configured_model(model_name, config)))
        model.callbacks = callbacks
    return model


class embedding_call:
    """Times the enclosed embeddings call and announces it, failed or not. ``count``: texts sent."""

    __slots__ = ("provider", "model", "count", "started", "_reported", "_token")

    def __init__(self, provider: str, model: str, count: int) -> None:
        self.provider = provider
        self.model = model
        self.count = count

    def __enter__(self) -> None:
        self._reported: list[int] | None = [] if platform_events.has_listeners(COMPLETED) else None
        self._token = _reported_tokens.set(self._reported)
        self.started = time.perf_counter()

    def __exit__(self, _kind: object, error: BaseException | None, _trace: object) -> None:
        with contextlib.suppress(ValueError):  # left in another context than it was entered in: nothing to restore there
            _reported_tokens.reset(self._token)
        tokens = sum(self._reported) if self._reported else None
        announce(self.provider, self.model, "embedding", self.started, error=error, tokens_in=tokens, count=self.count)


class AnnouncedEmbeddings(Embeddings):
    """An embeddings client whose calls are announced. Everything else is the wrapped client's."""

    def __init__(self, inner: Embeddings, provider: str, model: str) -> None:
        self._inner = inner
        self._provider = provider
        self._model = model

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - whatever the wrapped client has
        return getattr(self._inner, name)

    def embed_documents(self, texts: list[str], **kwargs: Any) -> list[list[float]]:  # noqa: ANN401
        with embedding_call(self._provider, self._model, len(texts)):
            return self._inner.embed_documents(texts, **kwargs)

    def embed_query(self, text: str, **kwargs: Any) -> list[float]:  # noqa: ANN401
        with embedding_call(self._provider, self._model, 1):
            return self._inner.embed_query(text, **kwargs)

    async def aembed_documents(self, texts: list[str], **kwargs: Any) -> list[list[float]]:  # noqa: ANN401
        with embedding_call(self._provider, self._model, len(texts)):
            return await self._inner.aembed_documents(texts, **kwargs)

    async def aembed_query(self, text: str, **kwargs: Any) -> list[float]:  # noqa: ANN401
        with embedding_call(self._provider, self._model, 1):
            return await self._inner.aembed_query(text, **kwargs)


def announce_embeddings(embeddings: Embeddings, provider: str, model_name: str | None, config: Mapping[str, Any] | None) -> Embeddings:
    """Wrap an embeddings client so its calls are announced. A client that announces its own calls
    (``announces_model_calls``) or is already wrapped is returned as it is.

    An embeddings client has no callback to hang this on, and a wrapper is another type than the client it wraps.
    So it is wrapped only in a process where somebody listens when the client is built (plug-ins subscribe at
    start, before any model is built); elsewhere the client is returned untouched."""
    if (
        not platform_events.has_listeners(COMPLETED)
        or isinstance(embeddings, AnnouncedEmbeddings)
        or getattr(embeddings, "announces_model_calls", False) is True
    ):
        return embeddings
    return AnnouncedEmbeddings(embeddings, provider, configured_model(model_name, config))
