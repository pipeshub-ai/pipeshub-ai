"""LLMGateway: the one path an indexing-time LLM call takes.

Every call holds a permit of one process-wide cap (across event loops), passes a circuit breaker
kept per model and endpoint, and is bounded by a ceiling on hangs. A provider that cannot serve
the call (unreachable, timing out, overloaded, still rate limiting after retries) raises
``ProviderUnavailableError``; an error that is the request's fault is re-raised unchanged and
does not count against the provider.

Where the process has bound a fleet Redis (``fleet_redis``; the indexing service does), the breaker is
the fleet's (``fleet_breaker``): every process shares one state per provider key and size class, a
half-open trial is a real request of a waiting job, and an unreadable Redis pauses the call. The outcome
of a call is judged from the wire (``wire``), not from the exception a wrapper raised. Without a bound
fleet Redis the per-process breaker below is used.
"""

import asyncio
import functools
import logging
import random
import threading
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Protocol, TypedDict, TypeVar, cast

from app.core.org_context import spawn_in_request
from app.exceptions.non_retryable import NonRetryableError
from app.services.base_client import (
    DEFAULT_CIRCUIT_BREAKER_COOLDOWN,
    DEFAULT_CIRCUIT_BREAKER_THRESHOLD,
    CircuitBreaker,
)
from app.services.llm_gateway import wire
from app.services.llm_gateway.fleet_breaker import (
    Admission,
    FleetBreaker,
    FleetBreakerUnavailable,
    Settlement,
    credential_of,
    provider_pk,
    size_class,
)
from app.services.llm_gateway.fleet_redis import get_fleet_redis
from app.services.llm_gateway.limiter import CrossLoopSemaphore
from app.services.llm_gateway.trial_lease import get_trial_renewer
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
# How long a paused job waits when the breaker's Redis cannot be read, and how often a trial renews its lease.
CONTROLS_UNAVAILABLE_RETRY_S = 30.0
TRIAL_LEASE_RENEW_S = 20.0  # the fallback renewal task's interval (the renewer has its own, in trial_lease)
# A gateway that says when its limit resets is believed, up to this long.
MAX_GATEWAY_RETRY_AFTER_SECONDS = 60.0
_TOO_MANY_REQUESTS = 429
_RATE_LIMIT_MARKERS = ("rate limit", "rate_limit", "429", "too many requests", "quota exceeded")

_R = TypeVar("_R")

_call_site: ContextVar[str] = ContextVar("llm_call_site", default="indexing")
# The stage job the calls inside a block belong to (its state key): the fleet breaker's waiting set
# and trial fairness are per job. Calls made outside any job carry none.
_job_key: ContextVar[str] = ContextVar("llm_job_key", default="")
_IMAGE_TOKENS = 1500


class ProviderUnavailableError(LLMUnavailableError):
    """The provider could not serve the call, or its circuit is open. Wait and try again.

    ``sent`` says whether a request reached the wire (an open circuit is a refusal, not a send);
    ``trial`` and ``chargeable`` say how a pause budget counts it (see ``LLMUnavailableError``).
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        retry_after: float | None = None,
        sent: bool = True,
        trial: bool = False,
        chargeable: bool = False,
        pk: str | None = None,
        size_class: str | None = None,
        requests: int | None = None,
        charged: int | None = None,
    ) -> None:
        super().__init__(
            message, retry_after=retry_after, sent=sent, trial=trial, chargeable=chargeable, pk=pk,
            size_class=size_class, requests=requests, charged=charged,
        )
        self.provider = provider
        trace = _job_trace.get()
        if trace is not None:
            trace.note_unavailable(pk, size_class, provider)


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


@dataclass
class ScopeTally:
    """One provider key and size class's share of a stage run's failures."""

    charged: int = 0
    trials: int = 0
    unavailable: int = 0
    label: str | None = None


# What a remote service reports of the calls it made for a job (the Extraction service's ``usage``).
JobUsage = dict[str, Any]


@dataclass
class JobTrace:
    """What the model calls of one stage run cost, for its pause budget, counted where each thing happens.

    * Failed sends (the budget's sends): by the run's ``SendMeter`` in the wire hooks, plus what a remote service
      reports it made for this job (``note_remote``). Successful requests are not sends of a pause.
    * Charged sends and trials: one per failed attempt, kept per ``(pk, size class)`` (``note_attempt``), so a later
      attempt or a call on another key never overwrites them.
    * Which key the pause is about: the one charged most (the latest to be unavailable on a tie, or when none is).
    """

    meter: wire.SendMeter = field(default_factory=wire.SendMeter)
    tallies: dict[tuple[str, str], ScopeTally] = field(default_factory=dict)
    unavailable: int = 0
    _latest: tuple[str, str] | None = None

    def _tally(self, pk: str | None, cls: str | None, label: str | None) -> ScopeTally | None:
        if not pk or not cls:
            return None
        tally = self.tallies.setdefault((pk, cls), ScopeTally())
        tally.label = label or tally.label
        return tally

    def note_attempt(self, pk: str | None, cls: str | None, label: str | None, *, chargeable: bool, trial: bool) -> None:
        """A failed attempt on the wire: a trial is never charged."""
        tally = self._tally(pk, cls, label)
        if tally is None:
            return
        tally.trials += 1 if trial else 0
        tally.charged += 1 if chargeable and not trial else 0

    def note_unavailable(self, pk: str | None, cls: str | None, label: str | None) -> None:
        """A call ended because the provider could not serve it (refused, failed or the sends were used)."""
        self.unavailable += 1
        tally = self._tally(pk, cls, label)
        if tally is not None:
            tally.unavailable += 1
            self._latest = (pk, cls)  # type: ignore[assignment]

    def note_remote(self, usage: JobUsage | None) -> None:
        """Add what a remote service reports it spent for this job (its own trace's ``usage``)."""
        if not isinstance(usage, dict):
            return

        def count(name: str) -> int:
            value = usage.get(name)
            return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0

        self.meter.failed += count("failed")
        scopes = usage.get("scopes")
        if isinstance(scopes, list) and scopes:
            # Per key: a request whose calls fell on two keys or size classes charges each its own.
            before = self.unavailable
            for entry in scopes:
                if isinstance(entry, dict):
                    self._note_remote_scope(entry)
            # Unavailability no key accounts for (a call with no key, or the caller's own 424 rule) still counts.
            for _ in range(max(0, count("unavailable") - (self.unavailable - before))):
                self.note_unavailable(None, None, None)
            return
        self._note_remote_scope(usage)

    def _note_remote_scope(self, entry: dict[str, Any]) -> None:
        def count(name: str) -> int:
            value = entry.get(name)
            return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0

        tally = self._tally(entry.get("pk"), entry.get("size_class"), entry.get("provider"))
        if tally is not None:
            tally.charged += count("charged")
            tally.trials += count("trials")
        for _ in range(count("unavailable")):
            self.note_unavailable(entry.get("pk"), entry.get("size_class"), entry.get("provider"))

    @property
    def requests(self) -> int:
        return self.meter.failed

    @property
    def charged(self) -> int:
        return sum(t.charged for t in self.tallies.values())

    @property
    def trials(self) -> int:
        return sum(t.trials for t in self.tallies.values())

    def scope(self) -> tuple[str, str] | None:
        if not self.tallies:
            return None
        return max(self.tallies, key=lambda key: (self.tallies[key].charged, key == self._latest))

    def usage(self) -> JobUsage:
        """This trace as a remote caller adds it to its own (``note_remote``)."""
        scope = self.scope()
        tally = self.tallies.get(scope) if scope else None
        return {
            "failed": self.requests, "charged": self.charged, "trials": self.trials, "unavailable": self.unavailable,
            "pk": scope[0] if scope else None, "size_class": scope[1] if scope else None,
            "provider": tally.label if tally else None,
            # Each key's own share: a caller adds these, not the totals above, to its own trace.
            "scopes": [
                {"pk": pk, "size_class": cls, "provider": t.label, "charged": t.charged, "trials": t.trials,
                 "unavailable": t.unavailable}
                for (pk, cls), t in self.tallies.items()
            ],
        }

    def pause_fields(self) -> dict[str, Any]:
        """``StageResult`` fields of a PAUSED outcome; empty when no call of the run was unavailable."""
        if not self.unavailable:
            return {}
        scope = self.scope()
        tally = self.tallies.get(scope) if scope else None
        return {
            "sent": self.requests > 0,
            "requests": self.requests,
            "trial": self.trials > 0,
            "chargeable": self.charged > 0,
            "charged": self.charged,
            "provider_pk": scope[0] if scope else None,
            "size_class": scope[1] if scope else None,
            "provider_label": tally.label if tally else None,
        }


_job_trace: ContextVar[JobTrace | None] = ContextVar("llm_job_trace", default=None)


@contextmanager
def llm_job(job_key: str, *, allowance: int | None = None) -> Generator[JobTrace, None, None]:
    """Attribute the calls made inside this block to one stage job (its state key). ``allowance``: the failed sends
    the job's pause budget has left; a request beyond it is refused before it is sent (None: no bound)."""
    trace = JobTrace(meter=wire.SendMeter(allowance=allowance))
    key_token = _job_key.set(job_key)
    trace_token = _job_trace.set(trace)
    try:
        with wire.metered(trace.meter):
            yield trace
    finally:
        _job_trace.reset(trace_token)
        _job_key.reset(key_token)


def _note_failed_sends(count: int) -> None:
    meter = wire.current_meter()
    if meter is not None and count > 0:
        meter.failed += count


def current_job_key() -> str:
    return _job_key.get()


def current_job_trace() -> JobTrace | None:
    return _job_trace.get()


def current_pause_fields() -> dict[str, Any]:
    """The run's pause fields (``JobTrace.pause_fields``); empty outside a stage job or when nothing was unavailable."""
    trace = _job_trace.get()
    return trace.pause_fields() if trace is not None else {}


def estimate_tokens(messages: object) -> int:
    """A rough input size of a call, for its size class: a quarter of a character per token, an image a fixed cost."""

    def calls(value: object) -> int:
        # Tool calls a message carries (arguments included) are sent with it, whatever its content.
        return len(str(value)) // 4 if value else 0

    def size(part: object) -> int:
        if isinstance(part, str):
            return len(part) // 4
        if isinstance(part, dict):
            kind = part.get("type")
            if kind in ("image", "image_url", "input_image"):
                return _IMAGE_TOKENS
            text = part.get("text")
            if isinstance(text, str):
                return len(text) // 4 + calls(part.get("tool_calls"))
            return len(str(part)) // 4
        content = getattr(part, "content", None)
        tool_calls = getattr(part, "tool_calls", None)
        if content is not None or tool_calls:
            return (size(content) if content is not None else 0) + calls(tool_calls)
        if isinstance(part, (list, tuple)):
            return sum(size(p) for p in part)
        if part is None or isinstance(part, (int, float, bool)):
            return 0
        # a tool schema, a structured-output schema, any other object the model is shown
        return len(str(part)) // 4

    try:
        return size(messages)
    except Exception:
        return 0


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
    for direction, usage_key in (("input", "input_tokens"), ("output", "output_tokens")):
        count = counts.get(usage_key)
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
            est_tokens=estimate_tokens(messages),
            credential=credential_of(runnable),
        )

    async def call(
        self,
        make_call: Callable[[], Awaitable[_R]],
        *,
        provider: str,
        call_site: str | None = None,
        timeout: float | None = None,
        est_tokens: int = 0,
        credential: str = "",
    ) -> _R:
        """``invoke`` for a call that is not a LangChain runnable (an agent-loop transport turn).

        ``make_call`` starts one attempt; it is called again for a rate-limit retry.
        ``est_tokens`` is the call's input size, for its size class (small up to 2k tokens, else large).
        """
        site = call_site or current_call_site()
        caller_budget = timeout is not None and 0 < timeout < CALL_TIMEOUT_S
        call_timeout = timeout if caller_budget else CALL_TIMEOUT_S
        fleet = self._fleet()
        pk = provider_pk(provider, credential)
        cls = size_class(est_tokens)
        job = current_job_key()
        trace = _job_trace.get()
        sent_any = False
        requests_total = 0
        charged_total = 0
        breaker = self.breaker(provider)
        # Per-process breaker, only where no fleet Redis is bound. The one caller allowed through after a cooldown
        # makes the real call as the probe: a health endpoint cannot tell whether the provider serves completions.
        if fleet is None and not breaker.should_attempt_probe() and breaker.is_open:
            LLM_CALLS.inc(site, "circuit_open")
            raise ProviderUnavailableError(
                f"{provider} is failing; not calling it until its cooldown ends",
                provider=provider,
                retry_after=breaker.retry_after(),
                sent=False,
            )
        for attempt in range(RATE_LIMIT_ATTEMPTS):
            self._refuse_if_sends_used(provider, site, pk, cls, sent_any=sent_any, requests=requests_total, charged=charged_total)
            policy: GatewayPolicyError | None = None
            admission = None
            lease = None
            try:
                async with self._limiter.slot():
                    # Admitted with the slot held, right before the request: a call that waited for a slot while others
                    # opened the breaker must see the breaker as it is now.
                    if fleet is not None:
                        admission = await self._admit(
                            fleet, provider, pk, cls, job, site, sent_any=sent_any, requests=requests_total
                        )
                    trial = admission is not None and admission.trial
                    lease = self._start_lease(fleet, pk, cls, admission) if trial and fleet and admission else None
                    with wire.wire_scope() as seen:
                        try:
                            started = asyncio.get_running_loop().time()
                            result = await asyncio.wait_for(make_call(), call_timeout)
                        except NonRetryableError:
                            # Neither the provider's failure nor one to retry: the caller must not fall back.
                            LLM_CALLS.inc(site, "error")
                            sent_any = sent_any or seen.sent
                            _note_failed_sends(max(0, seen.requests - 1))
                            await self._settle_outcome(
                                fleet, breaker, pk, cls, admission, wire.OK_UNRATED if seen.sent else wire.UNSENT
                            )
                            raise
                        except asyncio.CancelledError:
                            # Cut off (the stage's deadline): the requests it made brought no usable answer, and they
                            # count before the cancellation goes on, so the run that commits them pays for them.
                            _note_failed_sends(seen.requests)
                            if trial and fleet is not None and admission is not None:
                                await self._release_trial(fleet, pk, cls, admission.token)
                            raise
                        except Exception as exc:
                            if seen.allowance_refused:
                                sent_any = sent_any or seen.sent
                                requests_total += seen.requests
                                _note_failed_sends(seen.requests)
                                charged_total += await self._end_refused_attempt(
                                    fleet, breaker, pk, cls, admission, seen, trace, provider
                                )
                                LLM_CALLS.inc(site, "sends_used")
                                raise self._sends_used(
                                    provider, pk, cls, sent_any=sent_any, requests=requests_total, charged=charged_total
                                ) from exc
                            # The caller's own budget ran out only when it actually elapsed: a provider's timeout
                            # raised with budget left is the provider's failure, not a slow call.
                            caller_expired = (
                                caller_budget and isinstance(exc, TimeoutError)
                                and asyncio.get_running_loop().time() - started >= call_timeout * 0.99
                            )
                            policy = policy_error_from_exception(exc)
                            if policy is not None and _is_background_call():
                                report_background_refusal(policy)
                            judged = self._judge(seen, exc, policy, caller_expired=caller_expired)
                            sent_any = sent_any or seen.sent or judged == wire.FAIL
                            failed_here = seen.requests or (1 if judged == wire.FAIL else 0)
                            requests_total += failed_here
                            provider_failed = judged in (wire.FAIL, wire.NEUTRAL)
                            _note_failed_sends(failed_here if provider_failed else max(0, seen.requests - 1))
                            retry = (
                                _is_rate_limited(exc, policy) or seen.status == _TOO_MANY_REQUESTS
                            ) and attempt < RATE_LIMIT_ATTEMPTS - 1 and (policy is None or policy.retryable)
                            # Requests an SDK retried inside this attempt failed too: each is settled and charged
                            # on its own, before the attempt's own outcome.
                            charged_total += await self._settle_retried(
                                fleet, breaker, pk, cls, admission, seen, trace, provider
                            )
                            settlement = await self._settle_outcome(fleet, breaker, pk, cls, admission, judged)
                            if judged == wire.FAIL:
                                # Each failed attempt is charged on its own settlement: a later, unchargeable one
                                # does not take back the charge of an earlier one.
                                chargeable = bool(settlement and settlement.chargeable and not trial)
                                charged_total += 1 if chargeable else 0
                                if trace is not None:
                                    trace.note_attempt(pk, cls, provider, chargeable=chargeable, trial=trial)
                            if not retry:
                                self._raise_for(
                                    exc, judged, provider, site, breaker, admission, settlement, pk, cls,
                                    sent_any=sent_any, requests=requests_total, charged=charged_total,
                                )
                        else:
                            sent_any = sent_any or seen.sent
                            _note_failed_sends(max(0, seen.requests - 1))
                            charged_total += await self._settle_retried(
                                fleet, breaker, pk, cls, admission, seen, trace, provider
                            )
                            await self._settle_outcome(fleet, breaker, pk, cls, admission, wire.OK)
                            LLM_BREAKER_OPEN.set(provider, value=0.0)
                            note_llm_call()
                            LLM_CALLS.inc(site, "ok")
                            _record_tokens(site, result)
                            return result
            finally:
                if lease is not None:
                    lease()
            self.note_rate_limit_retry()
            LLM_CALLS.inc(site, "rate_limited")
            # Jittered, and outside the permit: lock-step retries are what keep a provider at its limit.
            delay = 2**attempt + random.uniform(0, 1)
            if policy is not None and policy.retry_after is not None:
                # The gateway said when; waiting less only earns another refusal.
                delay = max(delay, min(policy.retry_after, MAX_GATEWAY_RETRY_AFTER_SECONDS))
            await asyncio.sleep(delay)
        raise AssertionError("unreachable: the last attempt returns or raises")

    # ---- the job's sends -----------------------------------------------------------------------

    def _refuse_if_sends_used(
        self, provider: str, site: str, pk: str, cls: str, *, sent_any: bool, requests: int, charged: int
    ) -> None:
        """Before an attempt: a job whose pause budget has no failed sends left sends nothing more."""
        meter = wire.current_meter()
        if meter is not None and meter.remaining() == 0:
            LLM_CALLS.inc(site, "sends_used")
            raise self._sends_used(provider, pk, cls, sent_any=sent_any, requests=requests, charged=charged)

    @staticmethod
    def _sends_used(provider: str, pk: str, cls: str, *, sent_any: bool, requests: int, charged: int) -> "ProviderUnavailableError":
        return ProviderUnavailableError(
            f"{provider}: this job has used the sends its pause budget allows; not calling the model",
            provider=provider,
            sent=sent_any,
            chargeable=charged > 0,
            charged=charged,
            pk=pk,
            size_class=cls,
            requests=requests,
        )

    async def _settle_retried(
        self, fleet: FleetBreaker | None, breaker: CircuitBreaker, pk: str, cls: str, admission: Admission | None,
        seen: wire.WireOutcome, trace: JobTrace | None, provider: str,
    ) -> int:
        """Settle as failures the requests an SDK retried inside one attempt (every request but the last: an SDK retries
        only a failure), each on its own, and return how many were chargeable. A half-open trial carries one outcome for
        the fleet, so its retried requests are counted as sends but not settled again."""
        retried = max(0, seen.requests - 1)
        if retried == 0 or (admission is not None and admission.trial):
            return 0
        charged = 0
        for _ in range(retried):
            settlement = await self._settle_outcome(fleet, breaker, pk, cls, admission, wire.FAIL)
            chargeable = bool(settlement and settlement.chargeable)
            charged += 1 if chargeable else 0
            if trace is not None:
                trace.note_attempt(pk, cls, provider, chargeable=chargeable, trial=False)
        return charged

    async def _end_refused_attempt(
        self, fleet: FleetBreaker | None, breaker: CircuitBreaker, pk: str, cls: str, admission: Admission | None,
        seen: wire.WireOutcome, trace: JobTrace | None, provider: str,
    ) -> int:
        """An attempt whose next request the send meter refused: the requests it did send were retried, so they all failed,
        each settled and charged on its own like any failed attempt; a trial that never got its answer is handed back.
        Returns how many of them were chargeable."""
        if admission is not None and admission.trial and fleet is not None:
            await self._release_trial(fleet, pk, cls, admission.token)
            return 0
        if not seen.sent:
            return 0
        charged = await self._settle_retried(fleet, breaker, pk, cls, admission, seen, trace, provider)
        settlement = await self._settle_outcome(fleet, breaker, pk, cls, admission, wire.FAIL)
        chargeable = bool(settlement and settlement.chargeable)
        if trace is not None:
            trace.note_attempt(pk, cls, provider, chargeable=chargeable, trial=False)
        return charged + (1 if chargeable else 0)

    # ---- the fleet breaker ---------------------------------------------------------------------

    def _fleet(self) -> FleetBreaker | None:
        handle = get_fleet_redis()
        return FleetBreaker(handle) if handle is not None else None

    async def _admit(
        self, fleet: FleetBreaker, provider: str, pk: str, cls: str, job: str, site: str, *, sent_any: bool, requests: int
    ) -> Admission:
        try:
            admission = await fleet.admit(pk, cls, job)
        except FleetBreakerUnavailable as exc:
            # The control plane cannot be read: pause rather than spend unobserved.
            LLM_CALLS.inc(site, "controls_unavailable")
            raise ProviderUnavailableError(
                f"{provider}: the circuit breaker state could not be read ({exc}); not calling the model",
                provider=provider,
                retry_after=CONTROLS_UNAVAILABLE_RETRY_S,
                sent=sent_any,
                pk=pk,
                size_class=cls,
                requests=requests,
            ) from exc
        if not admission.allowed:
            LLM_CALLS.inc(site, "circuit_open")
            LLM_BREAKER_OPEN.set(provider, value=1.0)
            raise ProviderUnavailableError(
                f"{provider} is failing; not calling it until its cooldown ends",
                provider=provider,
                retry_after=admission.retry_after_s,
                sent=sent_any,
                pk=pk,
                size_class=cls,
                requests=requests,
            )
        return admission

    def _start_lease(self, fleet: FleetBreaker, pk: str, cls: str, admission: Admission) -> Callable[[], None]:
        """Keep a trial's lease alive; returns the function that ends it. The process's renewer (on its main loop) does it
        when one runs; otherwise a task of this call's loop."""
        renewer = get_trial_renewer()
        if renewer.running:
            renewer.register(pk, cls, admission.token)
            return lambda: renewer.unregister(admission.token)
        task = spawn_in_request(self._keep_trial_lease(fleet, pk, cls, admission.token), name="llm_trial_lease")
        return task.cancel

    async def _keep_trial_lease(self, fleet: FleetBreaker, pk: str, cls: str, token: str) -> None:
        """A trial can take minutes (a hang runs to the ceiling): renew its lease until it ends."""
        while True:
            await asyncio.sleep(TRIAL_LEASE_RENEW_S)
            try:
                if not await fleet.renew(pk, cls, token):
                    return
            except FleetBreakerUnavailable:
                continue

    async def _release_trial(self, fleet: FleetBreaker, pk: str, cls: str, token: str) -> None:
        try:
            await asyncio.shield(fleet.settle(pk, cls, wire.NEUTRAL, token=token, was_closed=False))
        except Exception:
            self._logger.debug("could not release a cancelled trial", exc_info=True)

    @staticmethod
    def _judge(
        seen: wire.WireOutcome, exc: BaseException, policy: GatewayPolicyError | None, *, caller_expired: bool
    ) -> str:
        """The call's outcome for the breaker. The wire decides when it saw the request; the exception only
        when no hook did (a model that could not be hooked)."""
        # A spent budget or a model the key may not use is the gateway answering, not the provider failing.
        if policy is not None and not policy.retryable:
            return wire.OK_UNRATED
        judged = seen.judge(raised=exc, caller_budget_expired=caller_expired)
        if judged != wire.UNSENT:
            return judged
        if caller_expired:
            return wire.NEUTRAL
        if isinstance(exc, TimeoutError) or is_provider_unavailable(exc):
            return wire.FAIL
        return wire.OK_UNRATED

    async def _settle_outcome(
        self,
        fleet: FleetBreaker | None,
        breaker: CircuitBreaker,
        pk: str,
        cls: str,
        admission: Admission | None,
        judged: str,
    ) -> Settlement | None:
        """Record a call's outcome on the fleet breaker, or on the per-process one."""
        if fleet is None or admission is None:
            if judged == wire.FAIL:
                breaker.record_failure()
            elif judged in (wire.OK, wire.OK_UNRATED):
                breaker.record_success()
            return None
        if judged == wire.UNSENT:
            return None
        try:
            return await fleet.settle(
                pk, cls, judged, token=admission.token if admission.trial else "", was_closed=not admission.trial
            )
        except FleetBreakerUnavailable:
            # The call already happened; the next admission finds Redis unreadable and pauses.
            self._logger.warning("could not record a model call's outcome on the fleet breaker")
            return None

    def _raise_for(
        self,
        exc: Exception,
        judged: str,
        provider: str,
        site: str,
        breaker: CircuitBreaker,
        admission: Admission | None,
        settlement: Settlement | None,
        pk: str,
        cls: str,
        *,
        sent_any: bool,
        requests: int,
        charged: int = 0,
    ) -> None:
        if isinstance(exc, TimeoutError) and judged == wire.NEUTRAL:
            # The caller's budget ran out: a slow call, not evidence the provider is down.
            LLM_CALLS.inc(site, "timeout")
            raise exc
        if judged == wire.FAIL:
            if breaker.is_open:
                LLM_BREAKER_OPEN.set(provider, value=1.0)
            if settlement is not None and settlement.state == "open":
                LLM_BREAKER_OPEN.set(provider, value=1.0)
            LLM_CALLS.inc(site, "unavailable")
            trial = admission is not None and admission.trial
            raise ProviderUnavailableError(
                f"{provider} could not serve the call: {type(exc).__name__}: {exc}",
                provider=provider,
                retry_after=breaker.retry_after() if admission is None else None,
                sent=True,
                trial=trial,
                chargeable=charged > 0,
                charged=charged,
                pk=pk,
                size_class=cls,
                requests=requests,
            ) from exc
        # The provider answered; the request was at fault. Not the provider's failure.
        LLM_CALLS.inc(site, "error")
        raise exc


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
