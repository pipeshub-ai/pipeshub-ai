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
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Protocol, TypedDict, TypeVar, cast

from app.exceptions.non_retryable import NonRetryableError
from app.services.base_client import (
    DEFAULT_CIRCUIT_BREAKER_COOLDOWN,
    DEFAULT_CIRCUIT_BREAKER_THRESHOLD,
    CircuitBreaker,
)
from app.services.llm_gateway.limiter import CrossLoopSemaphore
from app.services.model_identity.call_context import peek_call_context
from app.services.model_identity.errors import (
    GatewayPolicyError,
    policy_error_from_exception,
    report_background_refusal,
)
from app.services.model_identity.workload import WorkloadClass
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
# A gateway that says when its limit resets is believed, up to this long.
MAX_GATEWAY_RETRY_AFTER_SECONDS = 60.0
_TOO_MANY_REQUESTS = 429
_RATE_LIMIT_MARKERS = ("rate limit", "rate_limit", "429", "too many requests", "quota exceeded")

_R = TypeVar("_R")

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


class SlotHealth(TypedDict):
    """The /health ``indexing_llm`` block: numbers only, so no exception text can reach it."""

    slots: int
    in_use: int
    waiting: int
    acquired: int
    rate_limit_retries: int
    wait_s_total: float
    hold_s_total: float
    wait_s_mean: float
    hold_s_mean: float


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


def _is_background_call() -> bool:
    """An unbound call goes out as the service (``Workload.SYSTEM``), so it counts as background."""
    ctx = peek_call_context()
    return ctx is None or ctx.workload_class is WorkloadClass.BACKGROUND


def _is_rate_limited(exc: BaseException, policy: GatewayPolicyError | None = None) -> bool:
    # A budget or model-access refusal from the gateway looks like a 429 or 4xx but
    # comes back unchanged however often it is retried.
    if policy is not None:
        return policy.retryable
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
        self._rate_limit_retries = 0

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

    def slot_stats(self) -> SlotHealth:
        """Use of the cap: long waits with steady hold times mean the cap binds; rising hold times
        or rate-limit retries mean the provider does."""
        timings = self._limiter.timings()
        with self._lock:
            retries = self._rate_limit_retries
        return {
            "slots": self._limiter.limit,
            "in_use": self._limiter.in_use,
            "waiting": self._limiter.waiting,
            "acquired": timings.acquired,
            "rate_limit_retries": retries,
            "wait_s_total": round(timings.wait_s, 1),
            "hold_s_total": round(timings.hold_s, 1),
            "wait_s_mean": round(timings.wait_s / timings.acquired, 3) if timings.acquired else 0.0,
            # In-flight calls have not added their hold time yet, so they must not dilute the mean.
            "hold_s_mean": round(timings.hold_s / timings.released, 3) if timings.released else 0.0,
        }

    def note_rate_limit_retry(self) -> None:
        with self._lock:
            self._rate_limit_retries += 1
        note_rate_limit_retry()

    async def invoke(
        self,
        runnable: Invocable,
        messages: Any,  # noqa: ANN401
        *,
        provider: str,
        call_site: str | None = None,
        timeout: float | None = None,
        **invoke_kwargs: Any,  # noqa: ANN401 - forwarded to the model (config, max_tokens, ...)
    ) -> Any:  # noqa: ANN401
        """``timeout`` is the caller's own budget per call, once the permit is held (waiting for
        the permit is backpressure, not a hung call). It is raised as ``TimeoutError`` and not
        counted against the provider; only the hang ceiling (``CALL_TIMEOUT_S``) is."""
        return await self.call(
            lambda: runnable.ainvoke(messages, **invoke_kwargs),
            provider=provider,
            call_site=call_site,
            timeout=timeout,
        )

    async def call(
        self,
        make_call: Callable[[], Awaitable[_R]],
        *,
        provider: str,
        call_site: str | None = None,
        timeout: float | None = None,
    ) -> _R:
        """``invoke`` for a call that is not a LangChain runnable (an agent-loop transport turn).

        ``make_call`` starts one attempt; it is called again for a rate-limit retry.
        """
        site = call_site or current_call_site()
        caller_budget = timeout is not None and 0 < timeout < CALL_TIMEOUT_S
        call_timeout = timeout if caller_budget else CALL_TIMEOUT_S
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
            policy: GatewayPolicyError | None = None
            async with self._limiter.slot():
                try:
                    result = await asyncio.wait_for(make_call(), call_timeout)
                except NonRetryableError:
                    # Neither the provider's failure nor one to retry: the caller must not fall back.
                    LLM_CALLS.inc(site, "error")
                    raise
                except TimeoutError as exc:
                    if not caller_budget:
                        self._settle_failure(breaker, provider, site, exc)
                    # The caller's budget ran out: a slow call, not evidence the provider is down.
                    LLM_CALLS.inc(site, "timeout")
                    raise
                except Exception as exc:
                    policy = policy_error_from_exception(exc)
                    if policy is not None and _is_background_call():
                        report_background_refusal(policy)
                    retry = _is_rate_limited(exc, policy) and attempt < RATE_LIMIT_ATTEMPTS - 1
                    if not retry:
                        self._settle_failure(breaker, provider, site, exc, policy)
                        raise
                else:
                    breaker.record_success()
                    LLM_BREAKER_OPEN.set(provider, value=0.0)
                    note_llm_call()
                    LLM_CALLS.inc(site, "ok")
                    _record_tokens(site, result)
                    return result
            self.note_rate_limit_retry()
            LLM_CALLS.inc(site, "rate_limited")
            # Jittered, and outside the permit: lock-step retries are what keep a provider at its limit.
            delay = 2**attempt + random.uniform(0, 1)
            if policy is not None and policy.retry_after is not None:
                # The gateway said when; waiting less only earns another refusal.
                delay = max(delay, min(policy.retry_after, MAX_GATEWAY_RETRY_AFTER_SECONDS))
            await asyncio.sleep(delay)
        raise AssertionError("unreachable: the last attempt returns or raises")

    def _settle_failure(
        self,
        breaker: CircuitBreaker,
        provider: str,
        site: str,
        exc: Exception,
        policy: GatewayPolicyError | None = None,
    ) -> None:
        # A spent budget or a model the key may not use is the gateway answering, not the provider failing.
        refused = policy is not None and not policy.retryable
        if not refused and (isinstance(exc, TimeoutError) or is_provider_unavailable(exc)):
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
