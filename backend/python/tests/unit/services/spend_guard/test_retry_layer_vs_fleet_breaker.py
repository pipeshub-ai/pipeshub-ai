# ruff: noqa: F811 (the spend-world fixtures are imported for pytest, then named as arguments)
"""The gateway's retries against the fleet breaker and the pause budget (the retry layer must not break either).

The fake model behaves like a hooked SDK client: each request calls the wire hooks (admit_request, note_request_sent,
note_response), so the send meter, the wire judgement and the breaker's Lua (fakeredis) are the real ones. The gateway's
sleeps are recorded, not slept. The seed of the random cases is printed on failure.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import threading

import pytest

from app.services.llm_gateway import gateway as gateway_module
from app.services.llm_gateway import wire
from app.services.llm_gateway.gateway import (
    LLMGateway,
    ProviderUnavailableError,
    RateLimited,
    llm_job,
)
from app.services.spend_guard.retry_policy import (
    MAX_TOTAL_SLEEP_S,
    RETRY_BUDGET_CAPACITY,
    TRANSIENT_COST,
    RetryBudget,
    RetryKind,
    decide_retry,
)
from tests.support.spend_world import fleet, sim_clock  # noqa: F401 - fixtures

PROVIDER = "FakeChat:fake-model@host"
SEED = random.SystemRandom().randrange(1, 2**31)


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    log: list[float] = []

    async def record(seconds: float) -> None:
        log.append(seconds)

    monkeypatch.setattr(gateway_module.asyncio, "sleep", record)
    return log


class Model:
    """Answers each request with the next scripted reply: an int status, (status, retry_after), or 'ok'."""

    def __init__(self, script=None, default=(503, None)) -> None:
        self.script = list(script or [])
        self.default = default
        self.requests = 0

    async def ainvoke(self, value: object, /, **kwargs: object) -> str:
        wire.admit_request()
        wire.note_request_sent()
        self.requests += 1
        reply = self.script.pop(0) if self.script else self.default
        if reply == "ok":
            wire.note_response(200)
            return "answer"
        status, retry_after = reply if isinstance(reply, tuple) else (reply, None)
        wire.note_response(status, retry_after)
        raise RuntimeError(f"HTTP {status}")


# ---- 1. a 5xx storm: the breaker still opens, and the retries do not outrun it --------------------------------------


async def test_a_5xx_storm_opens_the_breaker_and_retries_stop_at_it(fleet, sim_clock, slept) -> None:
    gateway = LLMGateway(8, retry_budget=RetryBudget())
    model = Model()
    errors = []
    for i in range(12):  # 12 logical calls of 12 jobs, one after another, against a provider down for good
        with llm_job(f"job-{i}", allowance=20):
            try:
                await gateway.invoke(model, f"DOC:{i}", provider=PROVIDER)
            except ProviderUnavailableError as exc:
                errors.append(exc)
    # 5 consecutive failures open it (the S0b rule); once open, nobody sends: no attempt is retried past it.
    assert model.requests == 5, f"the fleet breaker must cap the storm at 5 requests, saw {model.requests}"
    assert all(not e.sent for e in errors[2:]), "calls after the breaker opened must send nothing"
    assert sum(e.requests for e in errors) == 5, "every failed request is counted in the errors' `requests`"


async def test_concurrent_jobs_in_a_5xx_storm(fleet, sim_clock, slept) -> None:
    """Many jobs at once: requests stay within 5 + the attempts already in flight when it opened (<= the slots)."""
    gateway = LLMGateway(8, retry_budget=RetryBudget())

    class Slow(Model):
        async def ainvoke(self, value, /, **kwargs):  # noqa: ANN202
            await asyncio.sleep(0)  # interleave: several attempts are in flight together
            return await super().ainvoke(value, **kwargs)

    model = Slow()

    async def one(i: int) -> None:
        with llm_job(f"job-{i}", allowance=20):
            with contextlib.suppress(ProviderUnavailableError):
                await gateway.invoke(model, f"DOC:{i}", provider=PROVIDER)

    await asyncio.gather(*(one(i) for i in range(40)))
    assert model.requests <= 5 + 8, f"{model.requests} requests: more than 5 + the 8 in flight"


# ---- 2. the job's send allowance bounds the retries, and failed sends are counted ----------------------------------


@pytest.mark.parametrize("allowance", [0, 1, 2, 3, 5])
async def test_the_send_allowance_bounds_the_gateway_retries(slept, allowance: int) -> None:
    gateway = LLMGateway(8, retry_budget=RetryBudget())  # no fleet: only the meter can stop it
    model = Model()
    with llm_job("job", allowance=allowance) as trace, pytest.raises(ProviderUnavailableError) as raised:
        await gateway.invoke(model, "DOC:x", provider=PROVIDER)
    assert model.requests == min(allowance, 3), f"allowance {allowance}: {model.requests} requests"
    assert trace.meter.failed == model.requests, "each failed request is a counted send"
    assert raised.value.requests == model.requests


async def test_failed_sends_are_counted_across_the_attempts(fleet, sim_clock, slept) -> None:
    gateway = LLMGateway(8, retry_budget=RetryBudget())
    model = Model(script=[503, 502, 500])
    with llm_job("job", allowance=20) as trace, pytest.raises(ProviderUnavailableError) as raised:
        await gateway.invoke(model, "DOC:x", provider=PROVIDER)
    assert model.requests == 3 and trace.meter.failed == 3 and raised.value.requests == 3


# ---- 3. a half-open trial is one request ----------------------------------------------------------------------------


@pytest.mark.parametrize("trial_reply", [503, (429, None), (429, 5.0)])
async def test_a_half_open_trial_is_not_repeated(fleet, sim_clock, slept, trial_reply) -> None:
    gateway = LLMGateway(8, retry_budget=RetryBudget())
    model = Model()
    for i in range(5):
        with llm_job(f"open-{i}", allowance=20):
            with pytest.raises(ProviderUnavailableError):
                await gateway.invoke(model, f"DOC:{i}", provider=PROVIDER)
    assert model.requests == 5
    sim_clock.advance_s(31)  # past the first cooldown: the next caller carries the trial
    model.script = [trial_reply]
    slept.clear()
    with llm_job("carrier", allowance=20), pytest.raises(ProviderUnavailableError):
        await gateway.invoke(model, "DOC:carrier", provider=PROVIDER)
    assert model.requests == 6, f"the trial must be one request, saw {model.requests - 5}"
    # Report (not a gate): does a throttled trial make the carrier sleep before the breaker refuses it?
    print(f"trial={trial_reply} slept={slept}")


# ---- 4. Retry-After and the 60 s allowance (fresh seeds) ------------------------------------------------------------


async def test_retry_after_sequences_never_exceed_the_allowance(slept) -> None:
    rng = random.Random(SEED)
    print(f"seed={SEED}")
    for _ in range(400):
        script = []
        for _ in range(3):
            status = rng.choice([429, 503, 500, 529])
            ra = rng.choice([None, 0.0, 0.5, 5.0, 20.0, 29.9, 30.0, 45.0, 59.9, 60.0, 60.1, 120.0])
            script.append((status, ra))
        model = Model(script=list(script))
        gateway = LLMGateway(8, retry_budget=RetryBudget())
        slept.clear()
        with pytest.raises(ProviderUnavailableError) as raised:
            await gateway.invoke(model, "DOC:x", provider=PROVIDER)
        assert model.requests <= 3
        assert sum(slept) <= MAX_TOTAL_SLEEP_S + 1e-9, (script, slept)
        # the oracle: walk the script as the policy says
        spent, expected_requests, rate_limited, beyond = 0.0, 0, False, False
        for attempt, (_status, ra) in enumerate(script):
            expected_requests += 1
            if attempt == 2:
                # The attempts are used: a throttle that said how long to wait is still handed to the caller.
                beyond = ra is not None and ra > MAX_TOTAL_SLEEP_S - spent
                rate_limited = beyond or (_status == 429 and ra is not None and ra > 0)
                break
            if ra is not None and ra > MAX_TOTAL_SLEEP_S - spent:
                rate_limited = beyond = True
                break
            spent += min(max(ra or 0.0, slept[attempt]), MAX_TOTAL_SLEEP_S - spent)
        assert model.requests == expected_requests, (script, slept, model.requests)
        assert isinstance(raised.value, RateLimited) == rate_limited, (script, slept, type(raised.value))
        if beyond:
            assert raised.value.retry_after is not None and raised.value.retry_after > MAX_TOTAL_SLEEP_S - sum(slept)
        for attempt, delay in enumerate(slept):
            status, ra = script[attempt]
            base = 1.0 if status == 429 else 0.25
            if ra is None:
                assert 0.0 <= delay <= base * 2**attempt, (script, slept)  # full jitter
            else:
                assert delay >= min(ra, MAX_TOTAL_SLEEP_S), (script, slept)


def test_decide_retry_properties_fresh_seed() -> None:
    rng = random.Random(SEED + 1)
    for _ in range(20000):
        kind = rng.choice([None, RetryKind.THROTTLE, RetryKind.TRANSIENT])
        attempt = rng.randrange(0, 4)
        slept = rng.uniform(0, 70)
        ra = rng.choice([None, rng.uniform(0, 120)])
        budget = RetryBudget(rng.choice([0, 4, 5, 13, 14, 500]))
        before = budget.tokens
        d = decide_retry(attempt=attempt, kind=kind, budget=budget, retry_after_s=ra, slept_s=slept,
                         rng=random.Random(rng.random()))
        remaining = max(0.0, MAX_TOTAL_SLEEP_S - slept)
        if d.retry:
            assert kind is not None and attempt + 1 < 3
            assert 0.0 <= d.delay_s <= remaining + 1e-9
            assert budget.tokens == before - (5 if kind is RetryKind.THROTTLE else 14)
            if ra is not None:
                assert d.delay_s >= ra - 1e-9
        else:
            assert d.delay_s == 0.0 and budget.tokens == before


# ---- 5. the retry budget is the process's: shared, thread-safe, and bounding ----------------------------------------


def test_the_default_gateways_share_one_process_budget() -> None:
    from app.services.llm_gateway.embedding_gateway import get_embedding_gateway
    from app.services.llm_gateway.gateway import get_llm_gateway
    from app.services.spend_guard.retry_policy import process_retry_budget

    assert get_llm_gateway()._retry_budget is process_retry_budget()
    assert get_embedding_gateway()._retry_budget is process_retry_budget()


def test_the_budget_is_exact_under_threads() -> None:
    budget = RetryBudget()
    wins = []

    def spend() -> None:
        n = 0
        for _ in range(1000):
            n += budget.try_spend(RetryKind.TRANSIENT)
        wins.append(n)

    threads = [threading.Thread(target=spend) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(wins) == RETRY_BUDGET_CAPACITY // TRANSIENT_COST
    assert budget.tokens == RETRY_BUDGET_CAPACITY % TRANSIENT_COST


async def test_an_outage_drains_the_budget_then_no_call_is_repeated(slept) -> None:
    budget = RetryBudget()
    # no fleet, and a per-process breaker that never opens: only the budget can stop the retries
    gateway = LLMGateway(8, retry_budget=budget, failure_threshold=10**6)
    model = Model()
    calls = 60
    for i in range(calls):
        with pytest.raises(ProviderUnavailableError):
            await gateway.invoke(model, f"DOC:{i}", provider=PROVIDER)
    assert model.requests == calls + RETRY_BUDGET_CAPACITY // TRANSIENT_COST
