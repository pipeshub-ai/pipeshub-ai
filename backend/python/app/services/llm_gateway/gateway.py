"""LLMGateway: the one path an indexing-time LLM call takes.

Every call holds a permit of one process-wide cap (across event loops), passes a circuit breaker
kept per model and endpoint, and is bounded by a ceiling on hangs. A provider that cannot serve
the call (unreachable, timing out, overloaded, still rate limiting after retries) raises
``ProviderUnavailableError``; an error that is the request's fault is re-raised unchanged and
does not count against the provider.
"""

import asyncio
import functools
import logging
import random
import threading
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Protocol, TypedDict, cast

from app.services.base_client import (
    DEFAULT_CIRCUIT_BREAKER_COOLDOWN,
    DEFAULT_CIRCUIT_BREAKER_THRESHOLD,
    CircuitBreaker,
)
from app.services.llm_gateway.limiter import CrossLoopSemaphore
from app.telemetry.modules.llm_gateway_metrics import (
    LLM_BREAKER_OPEN,
    LLM_CALLS,
    LLM_TOKENS,
)
from app.utils.indexing_metrics import note_llm_call, note_rate_limit_retry
from app.utils.llm import LLMUnavailableError, is_provider_unavailable

# A ceiling on hangs, not a latency target: self-hosted models legitimately take minutes on long
# inputs, and a tight bound would turn slowness into an outage. Below the classify stage budget.
CALL_TIMEOUT_S = 600.0
RATE_LIMIT_ATTEMPTS = 3
_TOO_MANY_REQUESTS = 429
_RATE_LIMIT_MARKERS = ("rate limit", "rate_limit", "429", "too many requests", "quota exceeded")

_call_site: ContextVar[str] = ContextVar("llm_call_site", default="indexing")


class ProviderUnavailableError(LLMUnavailableError):
    """The provider could not serve the call, or its circuit is open. Wait and try again."""

    def __init__(self, message: str, *, provider: str, retry_after: float | None = None) -> None:
        super().__init__(message, retry_after=retry_after)
        self.provider = provider


class Invocable(Protocol):
    async def ainvoke(self, value: Any, /, **kwargs: Any) -> Any: ...  # noqa: ANN401 - LangChain runnables take and return Any


class ProviderHealth(TypedDict):
    state: str
    retry_after: float | None


class GatewayHealth(TypedDict):
    limit: int
    in_use: int
    waiting: int
    providers: dict[str, ProviderHealth]


def provider_key(llm: object) -> str:
    """The model and endpoint a call goes to, for its breaker and metrics (never its credentials).

    Per model, not per endpoint: an overloaded model, one deployment's rate limit or a retired
    model fails that model while the others on the endpoint, and other orgs' models, still serve.
    """
    model = getattr(llm, "model_name", None) or getattr(llm, "model", None) or getattr(llm, "model_id", None) or "?"
    endpoint = (
        getattr(llm, "openai_api_base", None) or getattr(llm, "base_url", None)
        or getattr(llm, "azure_endpoint", None) or getattr(llm, "endpoint_url", None) or ""
    )
    key = f"{type(llm).__name__}:{model}"
    return f"{key}@{endpoint}" if endpoint else key


@contextmanager
def llm_call_site(name: str) -> Generator[None, None, None]:
    """Name the calls made inside this block (metrics label)."""
    token = _call_site.set(name)
    try:
        yield
    finally:
        _call_site.reset(token)


def current_call_site() -> str:
    return _call_site.get()


def _is_rate_limited(exc: BaseException) -> bool:
    if getattr(exc, "status_code", None) == _TOO_MANY_REQUESTS:
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _RATE_LIMIT_MARKERS)


def _record_tokens(call_site: str, result: object) -> None:
    usage: object = getattr(result, "usage_metadata", None)
    if not isinstance(usage, dict):
        return
    counts = cast("dict[str, object]", usage)
    for direction, field in (("input", "input_tokens"), ("output", "output_tokens")):
        count = counts.get(field)
        if isinstance(count, int) and count > 0:
            LLM_TOKENS.inc(call_site, direction, value=float(count))


class LLMGateway:
    def __init__(
        self,
        limit: int,
        *,
        logger: logging.Logger | None = None,
        failure_threshold: int = DEFAULT_CIRCUIT_BREAKER_THRESHOLD,
        cooldown_seconds: float = DEFAULT_CIRCUIT_BREAKER_COOLDOWN,
    ) -> None:
        super().__init__()
        self._limiter = CrossLoopSemaphore(limit)
        self._logger = logger or logging.getLogger(__name__)
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    @property
    def limiter(self) -> CrossLoopSemaphore:
        return self._limiter

    def breaker(self, provider: str) -> CircuitBreaker:
        with self._lock:
            breaker = self._breakers.get(provider)
            if breaker is None:
                breaker = CircuitBreaker(
                    f"llm:{provider}", self._failure_threshold, self._cooldown_seconds, logger=self._logger
                )
                self._breakers[provider] = breaker
            return breaker

    def stats(self) -> GatewayHealth:
        with self._lock:
            breakers = dict(self._breakers)
        return {
            "limit": self._limiter.limit,
            "in_use": self._limiter.in_use,
            "waiting": self._limiter.waiting,
            "providers": {key: {"state": b.state, "retry_after": b.retry_after()} for key, b in breakers.items()},
        }

    async def invoke(
        self,
        runnable: Invocable,
        messages: Any,  # noqa: ANN401
        *,
        provider: str,
        call_site: str | None = None,
        **invoke_kwargs: Any,  # noqa: ANN401 - forwarded to the model (max_tokens, temperature, ...)
    ) -> Any:  # noqa: ANN401
        site = call_site or current_call_site()
        breaker = self.breaker(provider)
        # The one caller allowed through after a cooldown makes the real call as the probe:
        # a health endpoint cannot tell whether the provider serves completions.
        if not breaker.should_attempt_probe() and breaker.is_open:
            LLM_CALLS.inc(site, "circuit_open")
            raise ProviderUnavailableError(
                f"{provider} is failing; not calling it until its cooldown ends",
                provider=provider,
                retry_after=breaker.retry_after(),
            )
        for attempt in range(RATE_LIMIT_ATTEMPTS):
            async with self._limiter.slot():
                try:
                    result = await asyncio.wait_for(runnable.ainvoke(messages, **invoke_kwargs), CALL_TIMEOUT_S)
                except Exception as exc:
                    retry = _is_rate_limited(exc) and attempt < RATE_LIMIT_ATTEMPTS - 1
                    if not retry:
                        self._settle_failure(breaker, provider, site, exc)
                        raise
                else:
                    breaker.record_success()
                    LLM_BREAKER_OPEN.set(provider, value=0.0)
                    note_llm_call()
                    LLM_CALLS.inc(site, "ok")
                    _record_tokens(site, result)
                    return result
            note_rate_limit_retry()
            LLM_CALLS.inc(site, "rate_limited")
            # Jittered, and outside the permit: lock-step retries are what keep a provider at its limit.
            await asyncio.sleep(2**attempt + random.uniform(0, 1))
        raise AssertionError("unreachable: the last attempt returns or raises")

    def _settle_failure(self, breaker: CircuitBreaker, provider: str, site: str, exc: Exception) -> None:
        if isinstance(exc, TimeoutError) or is_provider_unavailable(exc):
            breaker.record_failure()
            if breaker.is_open:
                LLM_BREAKER_OPEN.set(provider, value=1.0)
            LLM_CALLS.inc(site, "unavailable")
            raise ProviderUnavailableError(
                f"{provider} could not serve the call: {type(exc).__name__}: {exc}",
                provider=provider,
                retry_after=breaker.retry_after(),
            ) from exc
        # The provider answered; the request was at fault. Not the provider's failure.
        breaker.record_success()
        LLM_CALLS.inc(site, "error")


_default_lock = threading.Lock()


@functools.cache
def _build_default() -> LLMGateway:
    # Imported here: app.utils.concurrency hands out this gateway's permits.
    from app.utils.concurrency import MAX_CONCURRENT_INDEXING_LLM_CALLS
    from app.utils.worker_scaling import scaled

    return LLMGateway(scaled(MAX_CONCURRENT_INDEXING_LLM_CALLS))


def get_llm_gateway() -> LLMGateway:
    """This process's gateway, sized by MAX_CONCURRENT_INDEXING_LLM_CALLS."""
    with _default_lock:
        return _build_default()
