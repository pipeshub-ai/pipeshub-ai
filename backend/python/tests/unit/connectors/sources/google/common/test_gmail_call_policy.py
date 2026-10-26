"""GmailCallPolicy: waits through rate limits and brief outages, within its budgets."""

import logging
from collections.abc import Awaitable, Callable

import pytest
from _gmail_errors_fixtures import http_error
from googleapiclient.errors import HttpError

from app.connectors.sources.google.common.gmail_call_policy import GmailCallPolicy


class Clock:
    def __init__(self) -> None:
        self.now = 1792749600.0
        self.slept: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def time(self) -> float:
        return self.now


def policy(clock: Clock, **kwargs) -> GmailCallPolicy:
    return GmailCallPolicy(logging.getLogger("t"), sleep=clock.sleep, clock=clock.time, jitter=lambda: 1.0, **kwargs)


def failing(
    errors: list[BaseException], result: object = "ok"
) -> tuple[Callable[[], Awaitable[object]], dict[str, int]]:
    calls = {"n": 0}

    async def call() -> object:
        calls["n"] += 1
        if errors:
            raise errors.pop(0)
        return result

    return call, calls


@pytest.mark.asyncio
async def test_waits_until_the_time_a_rate_limit_names() -> None:
    clock = Clock()
    call, calls = failing([http_error(429, "rateLimitExceeded",
                                      "User-rate limit exceeded.  Retry after 2026-10-23T10:00:40.000Z")])
    assert await policy(clock).run(call, "threads.get") == "ok"
    assert calls["n"] == 2
    assert clock.slept == [pytest.approx(41.0)]


@pytest.mark.asyncio
async def test_retries_an_outage_with_backoff() -> None:
    clock = Clock()
    call, calls = failing([http_error(503), http_error(503)])
    assert await policy(clock).run(call, "threads.get") == "ok"
    assert calls["n"] == 3 and len(clock.slept) == 2


@pytest.mark.asyncio
async def test_does_not_retry_what_waiting_cannot_fix() -> None:
    clock = Clock()
    call, calls = failing([http_error(404, "notFound")])
    with pytest.raises(Exception) as raised:
        await policy(clock).run(call, "messages.get")
    assert getattr(raised.value.resp, "status", None) == 404
    assert calls["n"] == 1 and clock.slept == []


@pytest.mark.asyncio
async def test_gives_up_on_an_outage_after_its_cap() -> None:
    clock = Clock()
    call, _ = failing([http_error(503)] * 50)
    with pytest.raises(HttpError):
        await policy(clock, max_outage_wait_s=10).run(call, "threads.get")
    assert sum(clock.slept) <= 10


@pytest.mark.asyncio
async def test_total_budget_stops_all_waiting() -> None:
    clock = Clock()
    # Backoff 2, 4, 8 s without jitter: 2 + 4 fit a 7 s budget, the 8 s wait does not.
    p = policy(clock, total_wait_s=7, max_outage_wait_s=100)
    call, _ = failing([http_error(503)] * 50)
    with pytest.raises(HttpError):
        await p.run(call, "a")
    second, calls = failing([http_error(503)])
    with pytest.raises(HttpError):
        await p.run(second, "b")
    assert calls["n"] == 1, "a spent budget is not waited on again"
    assert p.waited_s == 6
