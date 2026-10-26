"""The Redis leader lease shared by the indexing background loops: only the
holder renews it, and a replica that does not hold it leaves it alone."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from app.modules.indexing.vector_membership_backfill import (
    VectorMembershipBackfillLeaderLock,
)
from app.services.messaging.config import RedisConfig
from tests.support.redis_provider_matrix import PROVIDERS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider

KEY = "test:leader"
TTL = 600


@pytest.fixture(params=PROVIDERS)
def provider(request: pytest.FixtureRequest) -> Iterator[FakeRedisConnectionProvider]:
    make: Callable[[], FakeRedisConnectionProvider] = request.param
    with patch(
        "app.services.redis.connection_provider_factory.get_redis_provider",
        return_value=(fake := make()),
    ):
        yield fake


def _lock(owner: str) -> VectorMembershipBackfillLeaderLock:
    return VectorMembershipBackfillLeaderLock(
        logging.getLogger("test"), RedisConfig(host="localhost", port=6379), owner, ttl_seconds=TTL, key=KEY,
    )


async def _holder(provider: FakeRedisConnectionProvider) -> str | None:
    return await provider.get_client().get(KEY)


async def test_a_replica_that_does_not_hold_the_lease_leaves_it_alone(provider: FakeRedisConnectionProvider) -> None:
    a, b = _lock("a"), _lock("b")
    assert await a.try_acquire()
    assert not await b.try_acquire()
    assert not await b.refresh()
    assert await _holder(provider) == "a"
    assert await a.refresh()
    assert await a.try_acquire()


async def test_replicas_taking_turns_keep_one_leader(provider: FakeRedisConnectionProvider) -> None:
    locks = [_lock(f"r{i}") for i in range(6)]
    won = [[await lock.try_acquire() for lock in locks] for _ in range(5)]
    assert won == [[True] + [False] * 5] * 5
    assert await _holder(provider) == "r0"


async def test_a_lease_that_moved_is_not_taken_back(provider: FakeRedisConnectionProvider) -> None:
    a, b = _lock("a"), _lock("b")
    assert await a.try_acquire()
    await provider.get_client().delete(KEY)  # expired
    assert await b.try_acquire()
    assert not await a.refresh()
    assert not await a.try_acquire()
    assert await _holder(provider) == "b"
    assert await b.refresh()


async def test_the_holder_renews_the_ttl(provider: FakeRedisConnectionProvider) -> None:
    a = _lock("a")
    client = provider.get_client()
    assert await a.try_acquire()
    await client.expire(KEY, 5)
    assert await a.refresh()
    assert await client.ttl(KEY) > TTL - 5
    await client.expire(KEY, 5)
    assert await a.try_acquire()
    assert await client.ttl(KEY) > TTL - 5


async def test_a_refresh_does_not_bring_back_an_expired_lease(provider: FakeRedisConnectionProvider) -> None:
    a, b = _lock("a"), _lock("b")
    assert await a.try_acquire()
    await provider.get_client().delete(KEY)
    assert not await a.refresh()
    assert await _holder(provider) is None
    assert await b.try_acquire()


async def test_a_released_lease_goes_to_the_next_replica(provider: FakeRedisConnectionProvider) -> None:
    a, b = _lock("a"), _lock("b")
    assert await a.try_acquire()
    await b.release()
    assert await _holder(provider) == "a"
    await a.release()
    assert await b.try_acquire()
