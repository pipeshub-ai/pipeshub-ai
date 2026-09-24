"""Unit tests for app.sources.client.resilience.ResiliencePolicy."""

import asyncio

import httpx
import pytest

from app.sources.client.resilience import ResiliencePolicy, parse_retry_after


def _policy(**kwargs) -> ResiliencePolicy:
    kwargs.setdefault("rate_limit", 1000)  # effectively unthrottled unless overridden
    kwargs.setdefault("base_delay", 0.0)   # no wake jitter, keeps timings crisp
    kwargs.setdefault("max_delay", 10.0)
    return ResiliencePolicy(**kwargs)


class TestFromConfig:
    def test_returns_none_when_unconfigured(self):
        assert ResiliencePolicy.from_config(None) is None
        assert ResiliencePolicy.from_config({}) is None

    def test_returns_none_when_disabled(self):
        config = {"enabled": False, "rate_limit": 3, "max_retries": 3}
        assert ResiliencePolicy.from_config(config) is None

    def test_reads_all_knobs(self):
        policy = ResiliencePolicy.from_config(
            {"enabled": True, "rate_limit": 3, "max_retries": 2, "base_delay": 0.5, "max_delay": 30.0},
            name="Notion",
        )
        assert policy is not None
        assert (policy.rate_limit, policy.max_retries) == (3, 2)
        assert (policy.base_delay, policy.max_delay) == (0.5, 30.0)
        assert policy.name == "Notion"


class TestValidation:
    @pytest.mark.parametrize("rate_limit", [0, -1, "3"])
    def test_rejects_bad_rate_limit(self, rate_limit):
        with pytest.raises(ValueError):
            ResiliencePolicy(rate_limit=rate_limit)

    @pytest.mark.parametrize("max_retries", [-1, 1.5, True])
    def test_rejects_bad_max_retries(self, max_retries):
        with pytest.raises(ValueError):
            ResiliencePolicy(rate_limit=1, max_retries=max_retries)

    def test_rejects_max_delay_below_base_delay(self):
        with pytest.raises(ValueError):
            ResiliencePolicy(rate_limit=1, base_delay=10.0, max_delay=1.0)

    def test_allows_no_rate_limit(self):
        assert ResiliencePolicy(rate_limit=None).rate_limit is None


class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_sub_one_rate_limit_is_usable(self):
        """aiolimiter refuses acquire(1) when max_rate < 1, so a connector
        declaring e.g. 0.5 req/s used to blow up on its first request."""
        policy = ResiliencePolicy(rate_limit=0.5, base_delay=0.0, max_delay=10.0)
        loop = asyncio.get_running_loop()

        await policy.acquire()  # first token is free
        start = loop.time()
        await policy.acquire()  # second must wait ~2s
        assert loop.time() - start >= 1.5

    @pytest.mark.asyncio
    async def test_limiter_paces_acquisitions(self):
        policy = _policy(rate_limit=5)  # bucket of 5, refills at 5/s
        loop = asyncio.get_running_loop()

        start = loop.time()
        for _ in range(7):  # 5 burst through, the last 2 must wait for refill
            await policy.acquire()
        assert loop.time() - start >= 0.3

    def test_limiter_is_per_event_loop(self):
        """AsyncLimiter binds to a loop on first acquire; this process runs several."""
        policy = _policy()

        async def grab():
            await policy.acquire()
            return policy._limiter()

        first = asyncio.run(grab())
        second = asyncio.run(grab())
        assert first is not second

    def test_closed_loops_do_not_accumulate(self):
        """AsyncLimiter caches its loop, so each value strongly references its own
        weak key — without pruning, the map grows once per loop, forever."""
        policy = _policy()

        async def grab():
            await policy.acquire()

        for _ in range(6):
            asyncio.run(grab())

        assert len(policy._limiters) <= 2


class TestBackoffGate:
    @pytest.mark.asyncio
    async def test_pause_blocks_a_concurrent_caller(self):
        policy = _policy()
        loop = asyncio.get_running_loop()

        policy.pause(0.3)
        start = loop.time()
        await policy.acquire()
        assert loop.time() - start >= 0.25

    @pytest.mark.asyncio
    async def test_pause_holds_back_every_caller_on_the_connector(self):
        """One 429 must not become N concurrent 429s across a fan-out."""
        policy = _policy()
        loop = asyncio.get_running_loop()

        policy.pause(0.3)
        start = loop.time()
        await asyncio.gather(*(policy.acquire() for _ in range(10)))
        assert loop.time() - start >= 0.25

    @pytest.mark.asyncio
    async def test_callers_queued_for_a_token_do_not_drain_into_a_pause(self):
        """The gate must hold back callers already waiting on the limiter.

        Checking it only before the queue let every queued request fire into a
        throttled API, so one 429 produced one more per queued request.
        """
        policy = _policy(rate_limit=5)
        released: list[int] = []

        async def caller(i: int) -> None:
            await policy.acquire()
            released.append(i)

        tasks = [asyncio.create_task(caller(i)) for i in range(30)]
        try:
            await asyncio.sleep(0.05)
            burst = len(released)  # limiter lets its bucket through immediately
            assert burst > 0

            policy.pause(1.0)
            await asyncio.sleep(0.6)
            assert len(released) == burst  # nothing escaped during the pause

            await asyncio.sleep(0.8)  # gate reopens; queued work resumes
            assert len(released) > burst
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_pause_never_shortens_an_armed_deadline(self):
        policy = _policy()
        loop = asyncio.get_running_loop()

        policy.pause(0.3)
        policy.pause(0.01)  # a later, shorter backoff must not undo the long one
        start = loop.time()
        await policy.acquire()
        assert loop.time() - start >= 0.25

    @pytest.mark.asyncio
    async def test_pause_is_capped_at_max_delay(self):
        policy = _policy(max_delay=0.05)
        loop = asyncio.get_running_loop()

        policy.pause(3600)
        start = loop.time()
        await policy.acquire()
        assert loop.time() - start < 1.0

    @pytest.mark.asyncio
    async def test_pausing_one_connector_does_not_delay_another(self):
        """The non-blocking guarantee: policies are per connector instance."""
        throttled = _policy(name="Notion")
        other = _policy(name="Slack")
        loop = asyncio.get_running_loop()

        throttled.pause(0.3)
        start = loop.time()
        await other.acquire()
        # Generous bound: this asserts "not blocked by the 0.3s pause", not a
        # precise timing, so scheduler jitter must not make it flaky.
        assert loop.time() - start < 0.25


class TestBackoffCalculation:
    def test_retry_after_wins_and_is_capped(self):
        """Jitter is added on top, so the wait is never shorter than requested."""
        policy = _policy(base_delay=1.0, max_delay=5.0)
        assert 2.0 <= policy.backoff(0, retry_after=2.0) <= 2.25
        assert 5.0 <= policy.backoff(0, retry_after=3600) <= 5.25

    def test_retry_after_jitter_spreads_simultaneous_throttles(self):
        policy = _policy(base_delay=1.0, max_delay=30.0)
        delays = {policy.backoff(0, retry_after=2.0) for _ in range(20)}
        assert len(delays) > 1

    def test_http_date_retry_after_falls_back_to_exponential(self):
        policy = _policy(base_delay=1.0, max_delay=5.0)
        # parse_retry_after yields None for the date form; backoff must not stall.
        assert 0 <= policy.backoff(0, retry_after=None) <= 1.0

    def test_exponential_growth_is_bounded_by_max_delay(self):
        policy = _policy(base_delay=1.0, max_delay=4.0)
        for attempt in range(6):
            assert 0 <= policy.backoff(attempt) <= 4.0

    def test_note_retry_arms_the_gate(self):
        policy = _policy(base_delay=1.0, max_delay=5.0)
        assert policy._resume_at == 0.0

        async def run():
            delay = policy.note_retry(0, retry_after=2.0)
            assert 2.0 <= delay <= 2.25
            assert policy._resume_at > 0.0

        asyncio.run(run())


class TestRun:
    @pytest.mark.asyncio
    async def test_returns_result_without_retrying_on_success(self):
        policy = _policy(max_retries=3)
        calls = []

        async def op():
            calls.append(1)
            return "ok"

        assert await policy.run(op) == "ok"
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_retries_network_errors_then_succeeds(self):
        policy = _policy(max_retries=3, base_delay=0.0)
        calls = []

        async def op():
            calls.append(1)
            if len(calls) < 3:
                raise httpx.ConnectError("boom")
            return "ok"

        assert await policy.run(op) == "ok"
        assert len(calls) == 3

    @pytest.mark.asyncio
    async def test_reraises_after_exhausting_retries(self):
        policy = _policy(max_retries=2, base_delay=0.0)
        calls = []

        async def op():
            calls.append(1)
            raise httpx.ReadError("boom")

        with pytest.raises(httpx.ReadError):
            await policy.run(op)
        assert len(calls) == 3  # max_retries + 1

    @pytest.mark.asyncio
    async def test_does_not_retry_non_retryable_status(self):
        policy = _policy(max_retries=3, base_delay=0.0)
        calls = []
        request = httpx.Request("GET", "https://api.notion.com/v1/users/me")

        async def op():
            calls.append(1)
            raise httpx.HTTPStatusError(
                "nope", request=request, response=httpx.Response(404, request=request)
            )

        with pytest.raises(httpx.HTTPStatusError):
            await policy.run(op)
        assert len(calls) == 1


class TestParseRetryAfter:
    def test_reads_seconds_case_insensitively(self):
        assert parse_retry_after(httpx.Headers({"Retry-After": "12"})) == 12.0
        assert parse_retry_after(httpx.Headers({"retry-after": "12"})) == 12.0

    def test_returns_none_for_absent_or_http_date(self):
        assert parse_retry_after(httpx.Headers({})) is None
        assert parse_retry_after(httpx.Headers({"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})) is None

    def test_returns_none_for_negative(self):
        assert parse_retry_after(httpx.Headers({"Retry-After": "-5"})) is None
