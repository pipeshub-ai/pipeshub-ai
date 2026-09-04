"""One contract, every `IRetryTracker` transport (T2).

`IRetryTracker` (the interface `RetryManager` implements) is the only
Phase-4 abstraction without a transport-parametrised contract suite before
this file: `TestCrossSlotSafety` in `test_retry_manager.py` covers
`has_pending_retries`/`clear_batch` under `FakeClusterRedis` only, so a
regression to a multi-key `MGET`/`DEL` that happens to still pass under
standalone would slip through. Running the same assertions under both
transports here closes that gap, and mirrors the streams/KV-store/lease/
cache contract suites in this directory.
"""
from __future__ import annotations

from logging import Logger, getLogger

import pytest

from app.services.messaging.retry_manager import RetryManager
from tests.support.redis_provider_matrix import PROVIDERS

pytest.importorskip("fakeredis.aioredis")

# `PROVIDERS` (standalone/cluster) comes from
# `tests/support/redis_provider_matrix.py` (T4) so an EE `conftest.py` can
# append a `memorydb` entry and run this same suite against it with no
# changes here.


def _logger() -> Logger:
    return getLogger("test-retry-tracker-contract")


def _ids_spanning_multiple_slots() -> list[str]:
    """Message ids picked to hash to at least two distinct Redis Cluster
    slots -- the exact slot values do not matter, only that they differ, so
    `clear_batch`/`has_pending_retries` are exercised across slot
    boundaries rather than happening to land on one node."""
    from redis.crc import key_slot

    candidates = [f"msg-{i}" for i in range(50)]
    keys = [f"{RetryManager.KEY_PREFIX}:{mid}" for mid in candidates]
    slots = {key_slot(k.encode()) for k in keys}
    assert len(slots) > 1, "test fixture must exercise more than one hash slot"
    return candidates


async def _make_manager(make_provider) -> RetryManager:
    provider = make_provider()
    manager = RetryManager(_logger(), redis_client=provider.get_client())
    await manager.initialize()
    return manager


@pytest.mark.asyncio
@pytest.mark.parametrize("make_provider", PROVIDERS)
class TestRetryTrackerContract:
    async def test_increment_and_check_counts_up_and_dead_letters_at_max_attempts(
        self, make_provider
    ) -> None:
        manager = await _make_manager(make_provider)
        try:
            count, dead_letter = await manager.increment_and_check(
                "msg-1", max_attempts=3
            )
            assert (count, dead_letter) == (1, False)

            count, dead_letter = await manager.increment_and_check(
                "msg-1", max_attempts=3
            )
            assert (count, dead_letter) == (2, False)

            count, dead_letter = await manager.increment_and_check(
                "msg-1", max_attempts=3
            )
            assert (count, dead_letter) == (3, True)

            assert await manager.get_count("msg-1") == 3
        finally:
            await manager.cleanup()

    async def test_increment_and_check_sets_a_ttl_so_abandoned_keys_expire(
        self, make_provider
    ) -> None:
        manager = await _make_manager(make_provider)
        try:
            await manager.increment_and_check("msg-ttl", max_attempts=5)
            key = manager._build_key("msg-ttl")  # noqa: SLF001
            ttl = await manager._client().ttl(key)  # noqa: SLF001
            assert 0 < ttl <= manager.ttl_seconds
        finally:
            await manager.cleanup()

    async def test_clear_removes_both_the_retry_and_delivery_counters(
        self, make_provider
    ) -> None:
        manager = await _make_manager(make_provider)
        try:
            await manager.increment_and_check("msg-clear", max_attempts=5)
            await manager.record_delivery("msg-clear")

            await manager.clear("msg-clear")

            assert await manager.get_count("msg-clear") == 0
            assert await manager.has_pending_retries(["msg-clear"]) is False
        finally:
            await manager.cleanup()

    async def test_clear_batch_survives_cross_slot_ids(self, make_provider) -> None:
        manager = await _make_manager(make_provider)
        try:
            message_ids = _ids_spanning_multiple_slots()
            for mid in message_ids:
                await manager.increment_and_check(mid, max_attempts=5)

            deleted = await manager.clear_batch(message_ids)

            assert deleted == len(message_ids)
            counts = [await manager.get_count(mid) for mid in message_ids]
            assert all(count == 0 for count in counts)
        finally:
            await manager.cleanup()

    async def test_clear_batch_of_empty_list_is_a_no_op(self, make_provider) -> None:
        manager = await _make_manager(make_provider)
        try:
            assert await manager.clear_batch([]) == 0
        finally:
            await manager.cleanup()

    async def test_has_pending_retries_survives_cross_slot_ids(
        self, make_provider
    ) -> None:
        manager = await _make_manager(make_provider)
        try:
            message_ids = _ids_spanning_multiple_slots()
            assert await manager.has_pending_retries(message_ids) is False

            await manager.increment_and_check(message_ids[0], max_attempts=5)

            assert await manager.has_pending_retries(message_ids) is True
        finally:
            await manager.cleanup()

    async def test_record_delivery_counts_independently_of_the_retry_counter(
        self, make_provider
    ) -> None:
        manager = await _make_manager(make_provider)
        try:
            assert await manager.record_delivery("msg-delivery") == 1
            assert await manager.record_delivery("msg-delivery") == 2

            # `record_delivery` never dead-letters and shares no counter
            # with `increment_and_check` -- delivery count is housekeeping,
            # not the retry-attempt count.
            assert await manager.get_count("msg-delivery") == 0
        finally:
            await manager.cleanup()
