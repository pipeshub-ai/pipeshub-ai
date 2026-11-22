"""`seeded_incr`: a generation counter that never comes back to a value it had, even when lost."""

from __future__ import annotations

import time
from typing import Any

import fakeredis
import pytest

from app.services.redis.generation import seeded_incr


@pytest.fixture
def client() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)


async def test_a_missing_counter_is_seeded_from_the_server_clock(client: fakeredis.FakeAsyncRedis) -> None:
    before_us = time.time_ns() // 1000
    seeded = await seeded_incr(client, "c", 0)
    assert seeded >= before_us
    assert await seeded_incr(client, "c", 0) == seeded  # by=0 only reads
    assert await seeded_incr(client, "c", 1) == seeded + 1
    assert await client.ttl("c") == -1  # no TTL: nothing on the main Redis may evict it


async def test_a_lost_counter_restarts_above_every_value_it_had(client: fakeredis.FakeAsyncRedis) -> None:
    values = [await seeded_incr(client, "c", 1) for _ in range(3)]
    await client.delete("c")
    assert await seeded_incr(client, "c", 1) > max(values)


class _EvictAfterEveryCommand:
    """Loses the key after each command the client sends: an eviction or failover between any two of them."""

    def __init__(self, inner: fakeredis.FakeAsyncRedis, key: str) -> None:
        self._inner, self._key = inner, key

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        command = getattr(self._inner, name)

        async def call(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            result = await command(*args, **kwargs)
            await self._inner.delete(self._key)
            return result

        return call


async def test_a_loss_between_seed_and_increment_cannot_restart_the_counter_at_1(client: fakeredis.FakeAsyncRedis) -> None:
    # Seed and increment run in one script, so there is no gap between them. Two commands (SET NX, then INCR) let a
    # loss in between make INCR recreate the counter at 1, a value a cache entry may already carry.
    evicting = _EvictAfterEveryCommand(client, "c")
    assert await seeded_incr(evicting, "c", 1) > 1_000_000

    await evicting.set("c", "1700000000000000", nx=True)
    assert await evicting.incr("c") == 1  # what the two-command form gave
