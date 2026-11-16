"""Shared fixtures of the spend-safety tests: a Redis for the fleet breaker and the pause budget, a
simulated clock, and a bound fleet handle.

By default the Redis is ``fakeredis`` with its Lua engine, so the tests run in CI without a service. Set
``SPEND_IT_REDIS_URL`` (for example ``redis://127.0.0.1:18200/0``) to run the very same tests against a real
Redis. Each test gets its own key namespace, removed at the end.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest

from app.services.llm_gateway.fleet_redis import FleetRedis, bind_fleet_redis

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class SimClock:
    """A clock the tests move. ``advance`` takes milliseconds."""

    def __init__(self, now_ms: int = 1_700_000_000_000) -> None:
        self.now_ms = now_ms

    def __call__(self) -> int:
        return self.now_ms

    def advance(self, ms: float) -> None:
        self.now_ms += int(ms)

    def advance_s(self, seconds: float) -> None:
        self.now_ms += int(seconds * 1000)


async def _client():  # noqa: ANN202
    url = os.environ.get("SPEND_IT_REDIS_URL")
    if url:
        import redis.asyncio as aioredis

        return aioredis.Redis.from_url(url, decode_responses=True), True
    if os.environ.get("SPEND_IT_REQUIRE_REDIS") == "1":
        # A run that requires the real Redis must not quietly fall back to fakeredis.
        pytest.fail("SPEND_IT_REDIS_URL is not set, and this run requires the real Redis")
    import fakeredis

    return fakeredis.FakeAsyncRedis(decode_responses=True), False


@pytest.fixture
async def sim_clock() -> SimClock:
    return SimClock()


@pytest.fixture
async def fleet(sim_clock: SimClock) -> AsyncIterator[FleetRedis]:
    """A fleet handle on its own namespace, on the simulated clock, and bound for the process."""
    client, real = await _client()
    namespace = f"t{uuid.uuid4().hex[:8]}"
    handle = FleetRedis(lambda: client, namespace, clock_ms=sim_clock)
    bind_fleet_redis(handle)
    try:
        yield handle
    finally:
        bind_fleet_redis(None)
        if real:
            keys = [k async for k in client.scan_iter(match=f"{namespace}:*", count=500)]
            if keys:
                await client.delete(*keys)
        await client.aclose()


def require_spend_redis() -> str:
    """The real Redis a test needs (``SPEND_IT_REDIS_URL``). Without it the test is skipped, so a plain ``pytest`` run
    passes; where the run must have it (``SPEND_IT_REQUIRE_REDIS=1``, set by the real-Redis CI job), it fails instead,
    so a missing variable there cannot pass silently."""
    import pytest

    url = os.environ.get("SPEND_IT_REDIS_URL")
    if url:
        return url
    if os.environ.get("SPEND_IT_REQUIRE_REDIS") == "1":
        pytest.fail("SPEND_IT_REDIS_URL is not set, and this run requires the real Redis")
    pytest.skip("needs a real Redis: set SPEND_IT_REDIS_URL")
