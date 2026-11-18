"""The one retry layer: the retry policy table, its budget, and the LLM gateway that applies it.

Pure policy cases first (no clock, no network), then the gateway driven by models that put real request events on the wire
hook, so what the test counts is what a provider would have received.
"""

from __future__ import annotations

import asyncio
import random

import httpx
import pytest

from app.services.llm_gateway import gateway as gateway_module
from app.services.llm_gateway import wire
from app.services.llm_gateway.gateway import (
    LLMGateway,
    ProviderUnavailableError,
    RateLimited,
    get_llm_gateway,
)
from app.services.spend_guard.retry_policy import (
    ATTEMPTS_EXHAUSTED,
    BUDGET_EXHAUSTED,
    MAX_ATTEMPTS,
    MAX_TOTAL_SLEEP_S,
    NOT_RETRYABLE,
    RETRY,
    RETRY_AFTER_BEYOND_BUDGET,
    RetryBudget,
    RetryKind,
    decide_retry,
)

# ---- the policy ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(20))
@pytest.mark.parametrize(("kind", "base"), [(RetryKind.THROTTLE, 1.0), (RetryKind.TRANSIENT, 0.25)])
def test_retry_policy_table(kind: RetryKind, base: float, seed: int) -> None:
    """Full jitter: the wait before the next attempt is uniform in [0, base * 2**attempt]; a retry is decided for attempts
    0 and 1 and never for the third, whatever the failure."""
    rng = random.Random(seed)
    for attempt in range(MAX_ATTEMPTS + 2):
        decision = decide_retry(attempt=attempt, kind=kind, budget=RetryBudget(), rng=rng)
        if attempt + 1 >= MAX_ATTEMPTS:
            assert (decision.retry, decision.reason) == (False, ATTEMPTS_EXHAUSTED)
            continue
        assert decision.retry and decision.reason == RETRY and decision.kind is kind
        assert 0.0 <= decision.delay_s <= base * 2**attempt


def test_the_jitter_is_spread_not_a_constant() -> None:
    """Lock-step retries are what keep a provider at its limit: the waits of 200 calls differ."""
    rng = random.Random(7)
    delays = {decide_retry(attempt=1, kind=RetryKind.THROTTLE, budget=RetryBudget(), rng=rng).delay_s for _ in range(200)}
    assert len(delays) > 150 and max(delays) <= 2.0 and min(delays) < 0.2 and max(delays) > 1.8


def test_a_failure_that_is_not_worth_repeating_is_never_retried() -> None:
    decision = decide_retry(attempt=0, kind=None, budget=RetryBudget())
    assert (decision.retry, decision.reason) == (False, NOT_RETRYABLE)


def test_a_retry_after_is_waited_out_when_it_fits_and_refused_when_it_does_not() -> None:
    budget = RetryBudget()
    fits = decide_retry(attempt=0, kind=RetryKind.THROTTLE, budget=budget, retry_after_s=20.0, rng=random.Random(1))
    assert fits.retry and fits.delay_s == 20.0
    # 45 s already slept leaves 15: a Retry-After of 20 s is beyond what the call may wait. Nothing is spent.
    before = budget.tokens
    refused = decide_retry(
        attempt=1, kind=RetryKind.THROTTLE, budget=budget, retry_after_s=20.0, slept_s=45.0, rng=random.Random(1)
    )
    assert (refused.retry, refused.reason, refused.delay_s) == (False, RETRY_AFTER_BEYOND_BUDGET, 0.0)
    assert budget.tokens == before


def test_a_call_never_sleeps_more_than_sixty_seconds_in_all() -> None:
    budget = RetryBudget()
    slept = 0.0
    for attempt in range(MAX_ATTEMPTS - 1):
        decision = decide_retry(
            attempt=attempt, kind=RetryKind.THROTTLE, budget=budget, retry_after_s=29.0, slept_s=slept
        )
        assert decision.retry
        slept += decision.delay_s
    assert slept <= MAX_TOTAL_SLEEP_S
    # A single wait is clamped to what is left.
    clamped = decide_retry(attempt=0, kind=RetryKind.THROTTLE, budget=budget, retry_after_s=10.0, slept_s=55.0)
    assert clamped.reason == RETRY_AFTER_BEYOND_BUDGET


def test_the_retry_budget_prices_each_kind_and_refunds_a_success() -> None:
    budget = RetryBudget()
    assert budget.tokens == 500
    assert budget.try_spend(RetryKind.THROTTLE) and budget.tokens == 495
    assert budget.try_spend(RetryKind.TRANSIENT) and budget.tokens == 481
    budget.refund()
    assert budget.tokens == 482
    for _ in range(600):
        budget.refund()
    assert budget.tokens == 500, "a refund never lifts the budget over its capacity"


def test_an_exhausted_budget_stops_retries_and_a_success_reopens_them() -> None:
    budget = RetryBudget()
    spent = 0
    while budget.try_spend(RetryKind.TRANSIENT):
        spent += 1
    assert spent == 500 // 14 and budget.tokens == 500 - spent * 14
    refused = decide_retry(attempt=0, kind=RetryKind.TRANSIENT, budget=budget)
    assert (refused.retry, refused.reason) == (False, BUDGET_EXHAUSTED)
    # A throttle costs 5: the leftover (6) still pays for one.
    assert decide_retry(attempt=0, kind=RetryKind.THROTTLE, budget=budget).retry
    for _ in range(14):
        budget.refund()
    assert decide_retry(attempt=0, kind=RetryKind.TRANSIENT, budget=budget).retry


def test_the_budget_is_safe_under_threads() -> None:
    import threading

    budget = RetryBudget()
    taken = []

    def worker() -> None:
        taken.append(sum(budget.try_spend(RetryKind.THROTTLE) for _ in range(200)))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(taken) == 100 and budget.tokens == 0


# ---- the gateway applies it ---------------------------------------------------------------------------------------------


class _Wire:
    """A model whose every call puts one request on the wire (through the real request hook) and answers the next scripted
    ``(status, retry_after)``; any status of 400 or more raises, as an SDK with its retries off does."""

    def __init__(self, *script: int | tuple[int, float | None]) -> None:
        self.script = list(script)
        self.requests = 0

    async def ainvoke(self, value: object, /, **kwargs: object) -> str:
        wire._request_hook(httpx.Request("POST", "http://host/v1/chat"))
        self.requests += 1
        status = self.script.pop(0) if self.script else 200
        retry_after = None
        if isinstance(status, tuple):
            status, retry_after = status
        wire.note_response(status, retry_after)
        if status >= 400:
            raise RuntimeError(f"HTTP {status}")
        return "answer"


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(gateway_module.asyncio, "sleep", record)
    return waits


async def test_a_logical_call_makes_at_most_three_requests(slept: list[float]) -> None:
    for status in (500, 503, 429):
        model = _Wire(status, status, status, status, status)
        with pytest.raises(ProviderUnavailableError):
            await LLMGateway(2).invoke(model, "hi", provider="p")
        assert model.requests == MAX_ATTEMPTS, f"HTTP {status}: {model.requests} requests for one logical call"


async def test_a_transient_failure_is_retried_with_a_short_jitter(slept: list[float]) -> None:
    model = _Wire(503)
    assert await LLMGateway(2).invoke(model, "hi", provider="p") == "answer"
    assert model.requests == 2 and len(slept) == 1 and slept[0] <= 0.25


async def test_a_throttle_waits_the_providers_retry_after(slept: list[float]) -> None:
    model = _Wire((429, 12.0))
    assert await LLMGateway(2).invoke(model, "hi", provider="p") == "answer"
    assert slept == [12.0]


async def test_a_retry_after_beyond_the_allowance_raises_rate_limited_without_sleeping(slept: list[float]) -> None:
    model = _Wire((429, 90.0), 200)
    with pytest.raises(RateLimited) as raised:
        await LLMGateway(2).invoke(model, "hi", provider="p")
    assert raised.value.retry_after == 90.0 and isinstance(raised.value, ProviderUnavailableError)
    assert model.requests == 1 and slept == [], "the call neither slept nor sent again"


@pytest.mark.parametrize("status", [400, 401, 404, 422])
async def test_a_request_fault_is_not_retried(slept: list[float], status: int) -> None:
    model = _Wire(status, 200)
    with pytest.raises(RuntimeError, match=str(status)):
        await LLMGateway(2).invoke(model, "hi", provider="p")
    assert model.requests == 1 and slept == []


async def test_a_hang_is_not_retried(slept: list[float], monkeypatch: pytest.MonkeyPatch) -> None:
    """A call that ran to its ceiling is not sent again: that would triple the time a stalled model holds a permit."""
    monkeypatch.setattr(gateway_module, "CALL_TIMEOUT_S", 0.05)

    class Hanging:
        requests = 0

        async def ainvoke(self, value: object, /, **kwargs: object) -> str:
            wire._request_hook(httpx.Request("POST", "http://host/v1/chat"))
            Hanging.requests += 1
            await asyncio.sleep(5)  # the patched sleep records and returns at once, so wait on an event instead
            raise AssertionError("unreachable")

    async def hang(_seconds: float) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(gateway_module.asyncio, "sleep", hang)
    with pytest.raises(ProviderUnavailableError):
        await LLMGateway(1).invoke(Hanging(), "hi", provider="p")
    assert Hanging.requests == 1


async def test_the_retry_budget_bounds_the_retries_of_a_struggling_provider(slept: list[float]) -> None:
    """With the budget spent, a failing call is sent once and fails: a provider that is down costs the process 500 tokens
    of retries and no more, however many calls arrive."""
    budget = RetryBudget(capacity=28)  # two transient retries
    gateway = LLMGateway(2, retry_budget=budget, failure_threshold=1000)  # the breaker stays out of it
    outcomes = []
    for _ in range(4):
        model = _Wire(*([503] * 10))
        with pytest.raises(ProviderUnavailableError):
            await gateway.invoke(model, "hi", provider="p")
        outcomes.append(model.requests)
    # The first call retries twice (3 requests, 28 tokens spent); then there is nothing to pay a retry with.
    assert outcomes == [3, 1, 1, 1]
    assert budget.tokens == 0


async def test_a_success_pays_a_token_back(slept: list[float]) -> None:
    budget = RetryBudget(capacity=500)
    budget.try_spend(RetryKind.TRANSIENT)
    gateway = LLMGateway(2, retry_budget=budget)
    await gateway.invoke(_Wire(), "hi", provider="p")
    assert budget.tokens == 500 - 14 + 1


# ---- the per-process breaker's half-open probe is one request ------------------------------------------------------------


class _Down:
    requests = 0

    def __init__(self, status: int = 503, retry_after: float | None = None) -> None:
        self.status, self.retry_after = status, retry_after

    async def ainvoke(self, value: object, /, **kwargs: object) -> str:
        wire._request_hook(httpx.Request("POST", "http://host/v1/chat"))
        self.requests += 1
        wire.note_response(self.status, self.retry_after)
        raise RuntimeError(f"HTTP {self.status}")


@pytest.mark.parametrize(("status", "retry_after"), [(503, None), (429, None), (429, 5.0)])
async def test_the_process_breaker_probe_is_one_request_and_sleeps_nothing(
    monkeypatch: pytest.MonkeyPatch, status: int, retry_after: float | None
) -> None:
    """With no fleet Redis bound, the first call after the cooldown is the half-open probe: one request decides the breaker,
    so a failed probe is neither repeated nor waited on before the breaker refuses."""
    real_sleep = asyncio.sleep
    waits: list[float] = []

    async def fast(seconds: float) -> None:
        waits.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(gateway_module.asyncio, "sleep", fast)
    gateway = LLMGateway(4, retry_budget=RetryBudget(), failure_threshold=3, cooldown_seconds=0.05)
    model = _Down(503)
    for _ in range(3):
        with pytest.raises(ProviderUnavailableError):
            await gateway.invoke(model, "x", provider="P:m@h")
    opened_at, waited_before = model.requests, len(waits)
    await real_sleep(0.1)  # past the cooldown: the next call is the probe
    probe = _Down(status, retry_after)
    with pytest.raises(ProviderUnavailableError):
        await gateway.invoke(probe, "x", provider="P:m@h")
    assert probe.requests == 1, f"the half-open probe sent {probe.requests} requests (the breaker opened after {opened_at})"
    assert len(waits) == waited_before, "a failed probe must not sleep before the breaker refuses"


def test_the_default_gateways_share_one_process_retry_budget() -> None:
    """One process, one budget: the LLM and embeddings gateways draw on the same 500 tokens, so a struggling provider cannot
    cost each of them its own."""
    from app.services.llm_gateway.embedding_gateway import get_embedding_gateway
    from app.services.spend_guard.retry_policy import process_retry_budget

    assert get_llm_gateway()._retry_budget is process_retry_budget()
    assert get_embedding_gateway()._retry_budget is process_retry_budget()


@pytest.mark.parametrize("slept", [50.0, 59.0, 59.99])
def test_a_wait_never_carries_the_call_past_its_sleep_allowance(slept: float) -> None:
    """Even the jitter is cut to what is left of the 60 s: a full-jitter draw at its top must not overrun it."""

    class _Top:
        def uniform(self, _low: float, high: float) -> float:
            return high

    for attempt in (0, 1):
        decision = decide_retry(
            attempt=attempt, kind=RetryKind.THROTTLE, budget=RetryBudget(), slept_s=slept, rng=_Top()  # type: ignore[arg-type]
        )
        assert decision.retry and slept + decision.delay_s <= MAX_TOTAL_SLEEP_S + 1e-9
