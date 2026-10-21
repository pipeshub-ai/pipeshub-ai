"""The retry budget holds while the retry store does not answer."""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.services.distributed.interface import IRetryTracker
from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.fail_safe_retry_tracker import FailSafeRetryTracker
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.retry_manager import RetryManager

pytest.importorskip("fakeredis.aioredis")
from fakeredis.aioredis import FakeRedis

LOGGER = logging.getLogger("test-fail-safe-retry-tracker")
MAX_ATTEMPTS = 3


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class _Store(IRetryTracker):
    """An in-memory store that can be taken down and brought back."""

    def __init__(self) -> None:
        self.down = False
        self.failures: dict[str, int] = {}
        self.deliveries: dict[str, int] = {}
        self.handbacks: dict[str, int] = {}

    def _answer(self) -> None:
        if self.down:
            raise TimeoutError("Timeout connecting to server")

    async def initialize(self) -> None:
        self._answer()

    async def increment_and_check(self, message_id: str, max_attempts: int) -> tuple[int, bool]:
        self._answer()
        self.failures[message_id] = self.failures.get(message_id, 0) + 1
        return self.failures[message_id], self.failures[message_id] >= max_attempts

    async def get_count(self, message_id: str) -> int:
        self._answer()
        return self.failures.get(message_id, 0)

    async def record_delivery(self, message_id: str) -> int:
        self._answer()
        self.deliveries[message_id] = self.deliveries.get(message_id, 0) + 1
        return self.deliveries[message_id]

    async def record_handback(self, message_id: str) -> int:
        self._answer()
        self.handbacks[message_id] = self.handbacks.get(message_id, 0) + 1
        return self.handbacks[message_id]

    async def get_handbacks(self, message_id: str) -> int:
        self._answer()
        return self.handbacks.get(message_id, 0)

    async def clear(self, message_id: str) -> None:
        self._answer()
        self.failures.pop(message_id, None)
        self.deliveries.pop(message_id, None)
        self.handbacks.pop(message_id, None)

    async def clear_batch(self, message_ids: list[str]) -> int:
        self._answer()
        return sum(self.failures.pop(m, None) is not None for m in message_ids)

    async def has_pending_retries(self, message_ids: list[str]) -> bool:
        self._answer()
        return any(self.failures.get(m) for m in message_ids)

    async def cleanup(self) -> None:
        pass


@pytest.fixture
def store() -> _Store:
    return _Store()


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def tracker(store: _Store, clock: _Clock) -> FailSafeRetryTracker:
    return FailSafeRetryTracker(store, LOGGER, ttl_seconds=60, max_entries=100, clock=clock)


class TestStoreAnswering:
    async def test_the_store_count_is_the_answer(self, tracker, store) -> None:
        store.failures["m"] = 1

        assert await tracker.increment_and_check("m", MAX_ATTEMPTS) == (2, False)
        assert await tracker.get_count("m") == 2

    async def test_failures_counted_during_an_outage_are_not_lost_when_it_ends(self, tracker, store) -> None:
        store.down = True
        await tracker.increment_and_check("m", MAX_ATTEMPTS)
        await tracker.increment_and_check("m", MAX_ATTEMPTS)
        store.down = False

        # The store saw only this one; the process saw all three.
        assert await tracker.increment_and_check("m", MAX_ATTEMPTS) == (3, True)

    async def test_clear_forgets_both_counts_everywhere(self, tracker, store) -> None:
        await tracker.increment_and_check("m", MAX_ATTEMPTS)
        await tracker.record_delivery("m")

        await tracker.clear("m")

        assert await tracker.get_count("m") == 0
        assert store.failures == {} and store.deliveries == {}
        assert await tracker.record_delivery("m") == 1

    async def test_clear_batch_forgets_each_message(self, tracker, store) -> None:
        await tracker.increment_and_check("a", MAX_ATTEMPTS)
        await tracker.increment_and_check("b", MAX_ATTEMPTS)

        assert await tracker.clear_batch(["a", "b"]) == 2
        store.down = True
        assert await tracker.get_count("a") == 0
        assert await tracker.has_pending_retries(["a", "b"]) is False


class TestStoreDown:
    async def test_the_budget_still_runs_out(self, tracker, store) -> None:
        store.down = True

        outcomes = [await tracker.increment_and_check("m", MAX_ATTEMPTS) for _ in range(MAX_ATTEMPTS)]

        assert outcomes == [(1, False), (2, False), (3, True)]

    async def test_reads_answer_from_the_process(self, tracker, store) -> None:
        store.down = True
        await tracker.increment_and_check("m", MAX_ATTEMPTS)

        assert await tracker.get_count("m") == 1
        assert await tracker.has_pending_retries(["m", "other"]) is True
        assert await tracker.get_count("other") == 0

    async def test_deliveries_are_counted_for_the_backstop(self, tracker, store) -> None:
        store.down = True

        assert [await tracker.record_delivery("m") for _ in range(3)] == [1, 2, 3]

    async def test_a_store_that_comes_back_and_goes_again_keeps_one_budget(self, tracker, store) -> None:
        store.down = True
        await tracker.increment_and_check("m", MAX_ATTEMPTS)
        store.down = False
        await tracker.increment_and_check("m", MAX_ATTEMPTS)
        store.down = True

        assert await tracker.increment_and_check("m", MAX_ATTEMPTS) == (3, True)

    async def test_clear_still_raises_so_the_caller_knows(self, tracker, store) -> None:
        store.down = True
        await tracker.increment_and_check("m", MAX_ATTEMPTS)

        with pytest.raises(TimeoutError):
            await tracker.clear("m")
        assert await tracker.get_count("m") == 0

    async def test_one_warning_per_operation_per_interval(self, store, clock, caplog) -> None:
        tracker = FailSafeRetryTracker(store, LOGGER, ttl_seconds=60, clock=clock)
        store.down = True

        with caplog.at_level(logging.WARNING, logger=LOGGER.name):
            for _ in range(5):
                await tracker.increment_and_check("m", 99)
            clock.now += 31
            await tracker.increment_and_check("m", 99)

        assert len([r for r in caplog.records if "count a failure" in r.getMessage()]) == 2

    async def test_a_cancelled_call_is_not_counted_as_the_store_failing(self, tracker, store) -> None:
        store.increment_and_check = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await tracker.increment_and_check("m", MAX_ATTEMPTS)


class TestProcessCounts:
    async def test_they_expire_with_the_store_ttl(self, tracker, store, clock) -> None:
        store.down = True
        await tracker.increment_and_check("m", MAX_ATTEMPTS)

        clock.now += 61

        assert await tracker.get_count("m") == 0

    async def test_each_failure_restarts_the_ttl(self, tracker, store, clock) -> None:
        store.down = True
        await tracker.increment_and_check("m", MAX_ATTEMPTS)
        clock.now += 50
        await tracker.increment_and_check("m", MAX_ATTEMPTS)
        clock.now += 50

        assert await tracker.get_count("m") == 2

    async def test_memory_is_bounded_and_the_oldest_go_first(self, store, clock) -> None:
        tracker = FailSafeRetryTracker(store, LOGGER, ttl_seconds=60, max_entries=2, clock=clock)
        store.down = True
        for message_id in ("a", "b", "c"):
            await tracker.increment_and_check(message_id, MAX_ATTEMPTS)

        assert [await tracker.get_count(m) for m in ("a", "b", "c")] == [0, 1, 1]


class TestWithRedis:
    """Wrapped around the real RetryManager, nothing changes while Redis answers."""

    async def test_counts_dead_letters_and_clears_like_the_retry_manager(self) -> None:
        manager = RetryManager(LOGGER, redis_client=FakeRedis())
        tracker = FailSafeRetryTracker(manager, LOGGER, ttl_seconds=manager.ttl_seconds)
        await tracker.initialize()

        outcomes = [await tracker.increment_and_check("m", MAX_ATTEMPTS) for _ in range(MAX_ATTEMPTS)]
        assert outcomes == [(1, False), (2, False), (3, True)]
        assert await manager.get_count("m") == 3
        assert await tracker.record_delivery("m") == 1

        await tracker.clear("m")
        assert await manager.get_count("m") == 0
        assert await tracker.has_pending_retries(["m"]) is False

    def test_the_factory_hands_out_the_fail_safe_tracker(self) -> None:
        tracker = MessagingFactory.create_retry_manager(LOGGER, redis_config=AsyncMock())

        assert isinstance(tracker, FailSafeRetryTracker)


class TestConsumerDuringAnOutage:
    async def test_a_failing_record_is_dead_lettered_instead_of_run_again(self, store) -> None:
        """The outage loop: failures could not be counted, so recovery re-ran the record forever."""
        consumer = IndexingRedisStreamsConsumer(
            LOGGER,
            RedisStreamsConfig(host="localhost", port=6379, client_id="c", group_id="g", topics=["t"]),
            retry_manager=FailSafeRetryTracker(store, LOGGER, ttl_seconds=60),
            producer=None,
        )
        consumer.redis = AsyncMock()
        consumer.redis.xpending_range = AsyncMock(return_value=[{"times_delivered": 1}])
        store.down = True

        with patch("app.services.messaging.redis_streams.indexing_consumer.messaging_env") as env:
            env.max_delivery_attempts = MAX_ATTEMPTS
            env.redis_max_deliveries = 10
            for _ in range(MAX_ATTEMPTS):
                await consumer._increment_retry_and_check("stable-1")
            dead_lettered = await consumer._should_dead_letter("t", "5-0", stable_message_id="stable-1")

        assert dead_lettered is True
        consumer.redis.xack.assert_awaited_once_with("t", "g", "5-0")


class TestHandbacks:
    async def test_they_are_counted_through_an_outage(self, tracker, store) -> None:
        store.down = True

        assert [await tracker.record_handback("m") for _ in range(3)] == [1, 2, 3]
        assert await tracker.get_handbacks("m") == 3

    async def test_clear_forgets_them(self, tracker, store) -> None:
        store.down = True
        await tracker.record_handback("m")
        store.down = False

        await tracker.clear("m")

        assert await tracker.get_handbacks("m") == 0

    async def test_deliveries_do_not_count_as_hand_backs(self, tracker, store) -> None:
        await tracker.record_delivery("m")
        await tracker.record_delivery("m")

        assert await tracker.get_handbacks("m") == 0


class TestStoreStaysAuthoritative:
    """While the store answers, its count is the count; the process only adds what it missed."""

    async def test_a_clear_by_another_replica_wins_over_this_process(self, tracker, store) -> None:
        await tracker.increment_and_check("m", MAX_ATTEMPTS)
        await tracker.increment_and_check("m", MAX_ATTEMPTS)
        store.failures.pop("m")  # the record succeeded on another replica

        assert await tracker.get_count("m") == 0
        assert await tracker.increment_and_check("m", MAX_ATTEMPTS) == (1, False)

    async def test_a_clear_elsewhere_drops_increments_the_store_missed_on_the_next_read(self, tracker, store) -> None:
        await tracker.increment_and_check("m", MAX_ATTEMPTS)
        store.down = True
        await tracker.increment_and_check("m", MAX_ATTEMPTS)
        await tracker.increment_and_check("m", MAX_ATTEMPTS)
        store.down = False
        store.failures.pop("m")  # the record succeeded on another replica

        assert await tracker.get_count("m") == 0
        assert await tracker.increment_and_check("m", MAX_ATTEMPTS) == (1, False)
        assert store.failures["m"] == 1

    async def test_a_clear_elsewhere_drops_increments_the_store_missed_on_the_next_write(self, tracker, store) -> None:
        await tracker.increment_and_check("m", MAX_ATTEMPTS)
        store.down = True
        await tracker.increment_and_check("m", MAX_ATTEMPTS)
        await tracker.increment_and_check("m", MAX_ATTEMPTS)
        store.down = False
        store.failures.pop("m")

        assert await tracker.increment_and_check("m", MAX_ATTEMPTS) == (1, False)
        assert store.failures["m"] == 1

    async def test_a_read_while_a_write_is_in_flight_does_not_drop_what_the_store_missed(
        self, tracker, store
    ) -> None:
        """The other loop reads the count this write produced before the write returns."""
        await tracker.increment_and_check("m", 10)
        store.down = True
        await tracker.increment_and_check("m", 10)
        await tracker.increment_and_check("m", 10)
        store.down = False
        applied, resume = asyncio.Event(), asyncio.Event()
        real = store.increment_and_check
        paused_once = False

        async def pauses_after_applying(message_id: str, max_attempts: int) -> tuple[int, bool]:
            nonlocal paused_once
            result = await real(message_id, max_attempts)
            if not paused_once:
                paused_once = True
                applied.set()
                await resume.wait()
            return result

        store.increment_and_check = pauses_after_applying
        write = asyncio.create_task(tracker.increment_and_check("m", 10))
        await applied.wait()
        await tracker.get_count("m")
        resume.set()

        assert await write == (4, False)
        assert store.failures["m"] == 4

    async def test_a_read_answered_after_a_newer_write_does_not_rewind_the_count(self, tracker, store) -> None:
        """The loops have their own store clients: a read sent before a write can answer after it."""
        await tracker.increment_and_check("m", 10)
        store.down = True
        await tracker.increment_and_check("m", 10)
        await tracker.increment_and_check("m", 10)
        store.down = False
        captured, resume = asyncio.Event(), asyncio.Event()
        real_get = store.get_count

        async def answers_late(message_id: str) -> int:
            value = await real_get(message_id)
            captured.set()
            await resume.wait()
            return value

        store.get_count = answers_late
        read = asyncio.create_task(tracker.get_count("m"))
        await captured.wait()
        assert await tracker.increment_and_check("m", 10) == (4, False)
        resume.set()

        assert await read == 4
        store.get_count = real_get
        assert await tracker.get_count("m") == 4
        assert store.failures["m"] == 4

    async def test_a_read_answered_after_a_clear_does_not_bring_the_old_count_back(self, tracker, store) -> None:
        for _ in range(5):
            await tracker.increment_and_check("m", 10)
        captured, resume = asyncio.Event(), asyncio.Event()
        real_get = store.get_count

        async def answers_late(message_id: str) -> int:
            value = await real_get(message_id)
            captured.set()
            await resume.wait()
            return value

        store.get_count = answers_late
        read = asyncio.create_task(tracker.get_count("m"))
        await captured.wait()
        store.failures.pop("m")  # the record succeeded on another replica
        assert await tracker.increment_and_check("m", 10) == (1, False)
        resume.set()

        assert await read == 1

    async def test_a_write_answered_after_a_clear_was_read_does_not_dead_letter(self, tracker, store) -> None:
        for _ in range(5):
            await tracker.increment_and_check("m", 6)
        applied, resume = asyncio.Event(), asyncio.Event()
        real = store.increment_and_check

        async def pauses_after_applying(message_id: str, max_attempts: int) -> tuple[int, bool]:
            result = await real(message_id, max_attempts)
            applied.set()
            await resume.wait()
            return result

        store.increment_and_check = pauses_after_applying
        write = asyncio.create_task(tracker.increment_and_check("m", 6))
        await applied.wait()
        store.failures.pop("m")
        assert await tracker.get_count("m") == 0
        resume.set()

        assert await write == (0, False)

    async def test_a_write_that_lands_after_a_clear_another_read_saw_keeps_its_count(self, tracker, store) -> None:
        for _ in range(5):
            await tracker.increment_and_check("m", 6)
        sent, resume = asyncio.Event(), asyncio.Event()
        real = store.increment_and_check

        async def waits_before_applying(message_id: str, max_attempts: int) -> tuple[int, bool]:
            sent.set()
            await resume.wait()
            return await real(message_id, max_attempts)

        store.increment_and_check = waits_before_applying
        write = asyncio.create_task(tracker.increment_and_check("m", 6))
        await sent.wait()
        store.failures.pop("m")
        assert await tracker.get_count("m") == 0
        resume.set()

        assert await write == (1, False)
        store.increment_and_check = real
        assert await tracker.get_count("m") == 1

    async def test_a_clear_during_the_hand_over_drops_the_rest(self, tracker, store) -> None:
        store.down = True
        for _ in range(3):
            await tracker.increment_and_check("m", 10)
        store.down = False
        calls = 0
        real = store.increment_and_check

        async def cleared_after_the_second_write(message_id: str, max_attempts: int) -> tuple[int, bool]:
            nonlocal calls
            calls += 1
            if calls == 3:
                store.failures.pop(message_id)  # the record succeeded on another replica
            return await real(message_id, max_attempts)

        store.increment_and_check = cleared_after_the_second_write

        assert await tracker.increment_and_check("m", 10) == (1, False)
        assert store.failures["m"] == 1
        assert calls == 3

    async def test_a_read_in_flight_when_this_process_clears_returns_the_clear(self, tracker, store) -> None:
        """With nothing cached, the cleared entry still looks unchanged by value alone."""
        store.failures["m"] = 5
        captured, resume = asyncio.Event(), asyncio.Event()
        real_get = store.get_count

        async def answers_late(message_id: str) -> int:
            value = await real_get(message_id)
            if not captured.is_set():
                captured.set()
                await resume.wait()
            return value

        store.get_count = answers_late
        read = asyncio.create_task(tracker.get_count("m"))
        await captured.wait()
        await tracker.clear("m")
        resume.set()

        assert await read == 0

    async def test_a_read_in_flight_during_a_hand_over_that_meets_a_clear_returns_the_new_count(
        self, tracker, store
    ) -> None:
        """The hand-over brings the cache back to the very value the read saw."""
        await tracker.increment_and_check("m", 10)
        store.down = True
        for _ in range(4):
            await tracker.increment_and_check("m", 10)
        store.down = False
        read_captured, read_resume = asyncio.Event(), asyncio.Event()
        real_get = store.get_count
        real_increment = store.increment_and_check
        calls = 0

        async def answers_late(message_id: str) -> int:
            value = await real_get(message_id)
            if not read_captured.is_set():
                read_captured.set()
                await read_resume.wait()
            return value

        async def deleted_before_the_fourth_write(message_id: str, max_attempts: int) -> tuple[int, bool]:
            nonlocal calls
            calls += 1
            if calls == 4:
                read = asyncio.create_task(tracker.get_count(message_id))
                await read_captured.wait()
                store.failures.pop(message_id)
                pending.append(read)
            return await real_increment(message_id, max_attempts)

        pending: list[asyncio.Task] = []
        store.get_count = answers_late
        store.increment_and_check = deleted_before_the_fourth_write

        assert await tracker.increment_and_check("m", 10) == (1, False)
        read_resume.set()

        assert await pending[0] == 1
        assert store.failures["m"] == 1

    async def test_a_write_the_store_applied_before_a_clear_here_deletes_it_does_not_dead_letter(
        self, tracker, store
    ) -> None:
        """The clear has zeroed the count here and its delete has not landed yet."""
        for _ in range(5):
            await tracker.increment_and_check("m", MAX_ATTEMPTS + 10)
        applied, resume_write = asyncio.Event(), asyncio.Event()
        deleting, resume_delete = asyncio.Event(), asyncio.Event()
        real_increment, real_clear = store.increment_and_check, store.clear

        async def pauses_after_applying(message_id: str, max_attempts: int) -> tuple[int, bool]:
            result = await real_increment(message_id, max_attempts)
            applied.set()
            await resume_write.wait()
            return result

        async def deletes_late(message_id: str) -> None:
            deleting.set()
            await resume_delete.wait()
            await real_clear(message_id)

        store.increment_and_check, store.clear = pauses_after_applying, deletes_late
        write = asyncio.create_task(tracker.increment_and_check("m", MAX_ATTEMPTS))
        await applied.wait()
        clear = asyncio.create_task(tracker.clear("m"))
        await deleting.wait()
        resume_write.set()

        assert await write == (0, False)
        resume_delete.set()
        await clear
        store.increment_and_check = real_increment
        assert await tracker.get_count("m") == 0

    async def test_a_cold_read_from_before_a_clear_here_stays_cleared_after_a_later_read(
        self, tracker, store
    ) -> None:
        """The later read publishes 0; the earlier one must still not put its 5 back."""
        store.failures["m"] = 5
        captured, resume = asyncio.Event(), asyncio.Event()
        real_get = store.get_count

        async def first_answers_late(message_id: str) -> int:
            value = await real_get(message_id)
            if not captured.is_set():
                captured.set()
                await resume.wait()
            return value

        store.get_count = first_answers_late
        first = asyncio.create_task(tracker.get_count("m"))
        await captured.wait()
        await tracker.clear("m")
        assert await tracker.get_count("m") == 0
        resume.set()

        assert await first == 0
        store.down = True
        assert await tracker.get_count("m") == 0

    async def test_a_read_sent_while_a_clear_here_is_deleting_does_not_return_the_old_count(
        self, tracker, store
    ) -> None:
        """Another store client can answer with the count the delete has not removed yet."""
        for _ in range(5):
            await tracker.increment_and_check("m", 10)
        deleting, resume_delete = asyncio.Event(), asyncio.Event()
        real_clear = store.clear

        async def deletes_late(message_id: str) -> None:
            deleting.set()
            await resume_delete.wait()
            await real_clear(message_id)

        store.clear = deletes_late
        clear = asyncio.create_task(tracker.clear("m"))
        await deleting.wait()

        assert await tracker.get_count("m") == 0
        resume_delete.set()
        await clear
        store.down = True
        assert await tracker.get_count("m") == 0

    async def test_a_confirming_read_that_overlaps_a_clear_here_returns_the_clear(self, tracker, store) -> None:
        for _ in range(5):
            await tracker.increment_and_check("m", 10)
        applied, resume_write = asyncio.Event(), asyncio.Event()
        confirming, resume_confirm = asyncio.Event(), asyncio.Event()
        real_increment, real_get = store.increment_and_check, store.get_count

        async def pauses_after_applying(message_id: str, max_attempts: int) -> tuple[int, bool]:
            result = await real_increment(message_id, max_attempts)
            applied.set()
            await resume_write.wait()
            return result

        reads = 0

        async def second_read_answers_late(message_id: str) -> int:
            nonlocal reads
            reads += 1
            value = await real_get(message_id)
            if reads == 2:
                confirming.set()
                await resume_confirm.wait()
            return value

        store.increment_and_check, store.get_count = pauses_after_applying, second_read_answers_late
        write = asyncio.create_task(tracker.increment_and_check("m", 10))
        await applied.wait()
        store.failures["m"] = 2  # cleared elsewhere, then failed twice there
        assert await tracker.get_count("m") == 2
        resume_write.set()
        await confirming.wait()
        await tracker.clear("m")
        resume_confirm.set()

        assert await write == (0, False)

    async def test_concurrent_writes_hand_each_missed_increment_over_once(self, tracker, store) -> None:
        store.down = True
        await tracker.increment_and_check("m", 10)
        await tracker.increment_and_check("m", 10)
        store.down = False
        real = store.increment_and_check

        async def yields_first(message_id: str, max_attempts: int) -> tuple[int, bool]:
            await asyncio.sleep(0)
            return await real(message_id, max_attempts)

        store.increment_and_check = yields_first

        await asyncio.gather(tracker.increment_and_check("m", 10), tracker.increment_and_check("m", 10))

        assert store.failures["m"] == 4
        assert await tracker.get_count("m") == 4

    async def test_with_nothing_seen_before_the_outage_its_increments_are_still_handed_over(
        self, tracker, store
    ) -> None:
        """No cached count to compare with: the outage's budget must not be forgotten."""
        store.down = True
        await tracker.increment_and_check("m", 10)
        await tracker.increment_and_check("m", 10)
        store.down = False

        assert await tracker.get_count("m") == 2
        assert await tracker.increment_and_check("m", 10) == (3, False)
        assert store.failures["m"] == 3

    async def test_hand_backs_cleared_elsewhere_are_not_resurrected(self, tracker, store) -> None:
        await tracker.record_handback("m")
        store.handbacks.pop("m")

        assert await tracker.get_handbacks("m") == 0

    async def test_increments_the_store_missed_are_handed_to_it(self, tracker, store) -> None:
        store.down = True
        await tracker.increment_and_check("m", MAX_ATTEMPTS)
        await tracker.increment_and_check("m", MAX_ATTEMPTS)
        store.down = False

        await tracker.increment_and_check("m", 10)

        assert store.failures["m"] == 3

    async def test_a_hand_over_cut_short_keeps_the_rest(self, tracker, store) -> None:
        store.down = True
        for _ in range(3):
            await tracker.increment_and_check("m", 10)
        store.down = False
        answered = 0
        real = store.increment_and_check

        async def answers_twice(message_id: str, max_attempts: int) -> tuple[int, bool]:
            nonlocal answered
            answered += 1
            if answered > 2:
                raise TimeoutError("Timeout connecting to server")
            return await real(message_id, max_attempts)

        store.increment_and_check = answers_twice

        count, _ = await tracker.increment_and_check("m", 10)

        assert store.failures["m"] == 2
        assert count == 4

    async def test_reads_during_an_outage_add_the_missed_increments_to_the_last_stored_count(
        self, tracker, store
    ) -> None:
        await tracker.increment_and_check("m", MAX_ATTEMPTS)
        store.down = True
        await tracker.increment_and_check("m", MAX_ATTEMPTS)

        assert await tracker.get_count("m") == 2
