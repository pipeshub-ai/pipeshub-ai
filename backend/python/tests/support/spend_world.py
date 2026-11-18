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


def _worker_index() -> int:
    """0 for a plain run, N for the pytest-xdist worker ``gwN``."""
    name = os.environ.get("PYTEST_XDIST_WORKER", "")
    return int(name[2:]) if name.startswith("gw") and name[2:].isdigit() else 0


def worker_redis_url(url: str) -> str:
    """The Redis URL with the logical database moved up by this xdist worker's index, so parallel workers never share a
    keyspace (and one worker's cleanup scan never walks another's keys). A plain run keeps the URL as given. Redis has 16
    databases by default; a run with more workers than that fails here instead of sharing one."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    from redis.asyncio.connection import parse_url

    index = _worker_index()
    if not index:
        return url
    # The effective database is whatever redis-py will select (``?db=`` over the path, the first non-empty value of a
    # repeated parameter), read with its own parser so the two can never disagree.
    base = int(parse_url(url).get("db") or 0)
    if base + index > 15:
        raise RuntimeError(f"worker gw{index} needs Redis database {base + index}; use fewer workers or a lower base database")
    database = str(base + index)
    parts = urlsplit(url)
    others = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "db"]
    if parts.scheme == "unix":
        # the path is the socket, so the database can only go in the query
        return urlunsplit(parts._replace(query=urlencode([*others, ("db", database)])))
    return urlunsplit(parts._replace(path=f"/{database}", query=urlencode(others)))


async def _client():  # noqa: ANN202
    url = os.environ.get("SPEND_IT_REDIS_URL")
    if url:
        url = worker_redis_url(url)
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
        return worker_redis_url(url)
    if os.environ.get("SPEND_IT_REQUIRE_REDIS") == "1":
        pytest.fail("SPEND_IT_REDIS_URL is not set, and this run requires the real Redis")
    pytest.skip("needs a real Redis: set SPEND_IT_REDIS_URL")
