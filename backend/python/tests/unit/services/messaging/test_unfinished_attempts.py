"""A record that kills the indexing process is given up on after two attempts
that never finished, and nothing else is charged for its crashes.

A crash runs no ``except`` and no ``finally``. These tests stand in for one
the only way a test can: an attempt counted as started (``start_attempt``)
and never finished, which is exactly what a killed process leaves in Redis.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest

from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
    StreamMessage,
    messaging_env,
)
from app.services.messaging.fail_safe_retry_tracker import FailSafeRetryTracker
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.retry_manager import RetryManager

pytest.importorskip("fakeredis.aioredis")
from fakeredis.aioredis import FakeRedis

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

LOGGER = logging.getLogger("test-unfinished-attempts")


@pytest.fixture
def tracker() -> RetryManager:
    return RetryManager(LOGGER, redis_client=FakeRedis())


async def _crash(tracker: RetryManager, record_id: str, times: int = 1) -> None:
    """What a process killed mid-attempt leaves behind."""
    for _ in range(times):
        await tracker.start_attempt(record_id)


class TestRetryManager:
    async def test_an_attempt_is_unfinished_from_its_start_until_it_finishes(self, tracker) -> None:
        assert await tracker.start_attempt("r1") == 0
        assert await tracker.unfinished_attempts("r1") == 1

        await tracker.finish_attempt("r1", settled=False)

        assert await tracker.unfinished_attempts("r1") == 0

    async def test_attempts_a_crash_left_add_up_and_the_next_start_reports_them(self, tracker) -> None:
        await _crash(tracker, "r1", times=2)

        assert await tracker.start_attempt("r1") == 2

    async def test_a_finished_attempt_keeps_the_crashes_before_it(self, tracker) -> None:
        await _crash(tracker, "r1")
        await tracker.start_attempt("r1")

        await tracker.finish_attempt("r1", settled=False)

        assert await tracker.unfinished_attempts("r1") == 1

    async def test_settling_forgets_every_unfinished_attempt(self, tracker) -> None:
        await _crash(tracker, "r1", times=2)

        await tracker.finish_attempt("r1", settled=True)

        assert await tracker.unfinished_attempts("r1") == 0

    async def test_a_finish_whose_count_already_expired_leaves_no_negative_count(self, tracker) -> None:
        await tracker.finish_attempt("r1", settled=False)

        assert await tracker.unfinished_attempts("r1") == 0
        assert await tracker.start_attempt("r1") == 0

    async def test_the_count_expires_like_the_other_counters(self, tracker) -> None:
        await tracker.start_attempt("r1")

        ttl = await tracker._client().ttl(tracker._build_key("r1", prefix=RetryManager.UNFINISHED_KEY_PREFIX))

        assert 0 < ttl <= tracker.ttl_seconds


class TestFailSafeTracker:
    async def test_it_passes_the_counts_through_while_the_store_answers(self, tracker) -> None:
        fail_safe = FailSafeRetryTracker(tracker, LOGGER, ttl_seconds=60)

        assert await fail_safe.start_attempt("r1") == 0
        assert await fail_safe.unfinished_attempts("r1") == 1
        await fail_safe.finish_attempt("r1", settled=True)
        assert await tracker.unfinished_attempts("r1") == 0

    async def test_an_unreachable_store_never_gives_up_on_a_record(self) -> None:
        store = AsyncMock(spec=RetryManager)
        store.unfinished_attempts.side_effect = TimeoutError("Timeout connecting to server")
        store.start_attempt.side_effect = TimeoutError("Timeout connecting to server")
        store.finish_attempt.side_effect = TimeoutError("Timeout connecting to server")
        fail_safe = FailSafeRetryTracker(store, LOGGER, ttl_seconds=60)

        assert await fail_safe.unfinished_attempts("r1") == 0
        assert await fail_safe.start_attempt("r1") == 0
        await fail_safe.finish_attempt("r1", settled=False)


class Sink:
    def __init__(self) -> None:
        self.abandoned: list[tuple[str | None, int]] = []
        self.on_abandon = None

    async def on_message_abandoned(self, message: StreamMessage | None, *, reason: str, attempts: int) -> None:
        if self.on_abandon is not None:
            self.on_abandon()
        self.abandoned.append((message.payload.get("recordId") if message else None, attempts))


class Indexer:
    """The record handler: notes which records run at the same time."""

    def __init__(self, tracker: RetryManager, fail: dict[str, BaseException] | None = None) -> None:
        self.tracker = tracker
        self.fail = fail or {}
        self.seen: list[str] = []
        self.running: set[str] = set()
        self.together: list[set[str]] = []
        self.unfinished_while_running: dict[str, int] = {}

    async def __call__(self, message: StreamMessage) -> AsyncGenerator[PipelineEvent, None]:
        record_id = message.payload["recordId"]
        self.seen.append(record_id)
        self.unfinished_while_running[record_id] = await self.tracker.unfinished_attempts(record_id)
        self.running.add(record_id)
        self.together.append(set(self.running))
        try:
            await asyncio.sleep(0.05)
            self.together.append(set(self.running))
            if record_id in self.fail:
                raise self.fail[record_id]
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=record_id))
        finally:
            self.running.discard(record_id)

    def ran_together(self, a: str, b: str) -> bool:
        return any({a, b} <= seen for seen in self.together)


def _consumer(tracker: RetryManager, sink: Sink, handler: Indexer) -> IndexingRedisStreamsConsumer:
    consumer = IndexingRedisStreamsConsumer(
        LOGGER,
        RedisStreamsConfig(host="localhost", port=6379, client_id="c", group_id="g", topics=["t"]),
        retry_manager=tracker,
        producer=None,
        disposition_sink=sink,
    )
    consumer.running = True
    consumer.parsing_semaphore = asyncio.Semaphore(4)
    consumer.indexing_semaphore = asyncio.Semaphore(4)
    consumer._pending_message_is_owned = AsyncMock(return_value=True)
    consumer._ack_message = AsyncMock()
    consumer._requeue_message = AsyncMock()
    consumer.message_handler = handler
    return consumer


def _fields(record_id: str) -> dict[str, str]:
    return {"value": json.dumps({"eventType": "newRecord", "payload": {"recordId": record_id}})}


class TestRedisConsumer:
    async def test_a_record_whose_attempts_never_finished_twice_is_failed_without_running(self, tracker) -> None:
        sink, indexer = Sink(), Indexer(tracker)
        consumer = _consumer(tracker, sink, indexer)
        await _crash(tracker, "r1", times=2)

        result = await consumer._process_message_wrapper("t", "1-0", _fields("r1"))

        assert result is False
        assert indexer.seen == []
        assert sink.abandoned == [("r1", 2)]
        consumer._ack_message.assert_awaited_once_with("t", "1-0")
        assert await tracker.unfinished_attempts("r1") == 0

    async def test_records_with_one_unfinished_attempt_run_one_at_a_time_and_hold_up_no_one_else(
        self, tracker
    ) -> None:
        sink, indexer = Sink(), Indexer(tracker)
        consumer = _consumer(tracker, sink, indexer)
        await _crash(tracker, "suspect-1")
        await _crash(tracker, "suspect-2")

        results = await asyncio.gather(
            consumer._process_message_wrapper("t", "1-0", _fields("suspect-1")),
            consumer._process_message_wrapper("t", "2-0", _fields("suspect-2")),
            consumer._process_message_wrapper("t", "3-0", _fields("fresh")),
        )

        assert results == [True, True, True]
        assert not indexer.ran_together("suspect-1", "suspect-2")
        assert indexer.ran_together("suspect-1", "fresh") or indexer.ran_together("suspect-2", "fresh")
        assert sink.abandoned == []

    async def test_an_attempt_counts_as_unfinished_only_while_it_runs(self, tracker) -> None:
        indexer = Indexer(tracker)
        consumer = _consumer(tracker, Sink(), indexer)

        assert await consumer._process_message_wrapper("t", "1-0", _fields("r1")) is True

        assert indexer.unfinished_while_running == {"r1": 1}
        assert await tracker.unfinished_attempts("r1") == 0

    async def test_indexing_a_record_clears_the_crash_before_it(self, tracker) -> None:
        consumer = _consumer(tracker, Sink(), Indexer(tracker))
        await _crash(tracker, "r1")

        assert await consumer._process_message_wrapper("t", "1-0", _fields("r1")) is True

        assert await tracker.unfinished_attempts("r1") == 0

    async def test_a_failed_attempt_is_a_failure_not_a_crash(self, tracker) -> None:
        indexer = Indexer(tracker, fail={"r1": ConnectionError("vector store unreachable")})
        consumer = _consumer(tracker, Sink(), indexer)

        assert await consumer._process_message_wrapper("t", "1-0", _fields("r1")) is False

        assert await tracker.unfinished_attempts("r1") == 0
        assert await tracker.get_count("1-0") == 1
        consumer._requeue_message.assert_awaited_once()

    async def test_a_delivery_that_waits_out_its_records_lease_starts_no_attempt(self, tracker) -> None:
        indexer = Indexer(tracker)
        consumer = _consumer(tracker, Sink(), indexer)
        manager = AsyncMock()

        async def try_acquire(pool, _owner, _limit, _lease_seconds) -> bool:
            return not pool.startswith("record:")

        manager.try_acquire.side_effect = try_acquire
        consumer.concurrency_manager = manager
        with (
            patch.object(
                type(messaging_env), "record_lease_wait_seconds", new_callable=PropertyMock, return_value=0.05,
            ),
            patch.object(
                type(messaging_env), "concurrency_acquire_poll_seconds", new_callable=PropertyMock, return_value=0.01,
            ),
        ):
            assert await consumer._process_message_wrapper("t", "1-0", _fields("r1")) is False

        assert indexer.seen == []
        assert await tracker.unfinished_attempts("r1") == 0

    async def test_giving_up_lets_go_of_the_records_lease_before_failing_it(self, tracker) -> None:
        """The sink fails an IN_PROGRESS record only when no live holder has its lease."""
        sink = Sink()
        consumer = _consumer(tracker, sink, Indexer(tracker))
        manager = AsyncMock()
        manager.try_acquire.return_value = True
        consumer.concurrency_manager = manager
        released_before_failing: list[bool] = []
        sink.on_abandon = lambda: released_before_failing.append(
            any(call.args[0] == "record:r1" for call in manager.release.await_args_list)
        )
        await _crash(tracker, "r1", times=2)

        assert await consumer._process_message_wrapper("t", "1-0", _fields("r1")) is False

        assert sink.abandoned == [("r1", 2)]
        assert released_before_failing == [True]

    async def test_redis_delivery_counts_no_longer_drop_entries(self, tracker) -> None:
        """Every restart claims every entry the dead consumer held, adding one each."""
        sink = Sink()
        consumer = _consumer(tracker, sink, Indexer(tracker))
        consumer.redis = AsyncMock()
        consumer.redis.xpending_range = AsyncMock(return_value=[{"times_delivered": 25}])

        assert await consumer._should_dead_letter("t", "5-0", stable_message_id="5-0") is False

        assert sink.abandoned == []
        consumer.redis.xack.assert_not_called()

    async def test_a_record_handed_back_for_ever_without_a_parse_slot_is_failed(self, tracker) -> None:
        indexer = Indexer(tracker, fail={"r1": concurrency.ParseAdmissionTimeout("light_parse", 1.0)})
        sink = Sink()
        consumer = _consumer(tracker, sink, indexer)
        for _ in range(messaging_env.redis_max_deliveries - 1):
            await tracker.record_delivery("1-0")

        assert await consumer._process_message_wrapper("t", "1-0", _fields("r1")) is False

        assert sink.abandoned == [("r1", messaging_env.redis_max_deliveries)]
        consumer._ack_message.assert_awaited_once_with("t", "1-0")
        consumer._requeue_message.assert_not_called()


class _Message:
    def __init__(self, record_id: str, offset: int) -> None:
        self.topic, self.partition, self.offset = "record-events", 0, offset
        self.value = json.dumps({"eventType": "newRecord", "payload": {"recordId": record_id}}).encode("utf-8")


def _kafka_consumer(tracker, sink: Sink, handler: Indexer) -> IndexingKafkaConsumer:
    consumer = IndexingKafkaConsumer(
        LOGGER,
        KafkaConsumerConfig(
            topics=["record-events"], client_id="c", group_id="g", auto_offset_reset="earliest",
            enable_auto_commit=False, bootstrap_servers=["broker:9092"], ssl=False, sasl=None,
        ),
        retry_manager=tracker,
        producer=None,
        disposition_sink=sink,
    )
    consumer.running = True
    consumer.parsing_semaphore = asyncio.Semaphore(4)
    consumer.indexing_semaphore = asyncio.Semaphore(4)
    consumer._commit_offset = AsyncMock()
    consumer._requeue_message = AsyncMock()
    consumer.message_handler = handler
    return consumer


async def _run(consumer: IndexingKafkaConsumer, record_id: str, offset: int = 0) -> bool:
    return await consumer._IndexingKafkaConsumer__process_message_wrapper(_Message(record_id, offset))


class TestKafkaConsumer:
    async def test_a_record_whose_attempts_never_finished_twice_is_failed_and_committed_without_running(
        self, tracker
    ) -> None:
        sink, indexer = Sink(), Indexer(tracker)
        consumer = _kafka_consumer(tracker, sink, indexer)
        await _crash(tracker, "r1", times=2)

        assert await _run(consumer, "r1") is False

        assert indexer.seen == []
        assert sink.abandoned == [("r1", 2)]
        consumer._commit_offset.assert_awaited_once()
        assert await tracker.unfinished_attempts("r1") == 0

    async def test_records_with_one_unfinished_attempt_run_one_at_a_time_and_hold_up_no_one_else(
        self, tracker
    ) -> None:
        indexer = Indexer(tracker)
        consumer = _kafka_consumer(tracker, Sink(), indexer)
        await _crash(tracker, "suspect-1")
        await _crash(tracker, "suspect-2")

        results = await asyncio.gather(
            _run(consumer, "suspect-1", 0), _run(consumer, "suspect-2", 1), _run(consumer, "fresh", 2),
        )

        assert results == [True, True, True]
        assert not indexer.ran_together("suspect-1", "suspect-2")
        assert indexer.ran_together("suspect-1", "fresh") or indexer.ran_together("suspect-2", "fresh")
        assert [await tracker.unfinished_attempts(r) for r in ("suspect-1", "suspect-2", "fresh")] == [0, 0, 0]

    async def test_a_delivery_that_waits_out_its_records_lease_is_not_counted(self, tracker) -> None:
        indexer = Indexer(tracker)
        consumer = _kafka_consumer(tracker, Sink(), indexer)
        manager = AsyncMock()

        async def try_acquire(pool, _owner, _limit, _lease_seconds) -> bool:
            return not pool.startswith("record:")

        manager.try_acquire.side_effect = try_acquire
        consumer.concurrency_manager = manager
        with (
            patch.object(
                type(messaging_env), "record_lease_wait_seconds", new_callable=PropertyMock, return_value=0.05,
            ),
            patch.object(
                type(messaging_env), "concurrency_acquire_poll_seconds", new_callable=PropertyMock, return_value=0.01,
            ),
        ):
            assert await _run(consumer, "r1") is False

        assert indexer.seen == []
        assert await tracker.unfinished_attempts("r1") == 0
        assert await tracker.record_delivery("record-events-0-0") == 1

    async def test_a_record_handed_back_for_ever_without_a_parse_slot_is_failed(self, tracker) -> None:
        indexer = Indexer(tracker, fail={"r1": concurrency.ParseAdmissionTimeout("light_parse", 1.0)})
        sink = Sink()
        consumer = _kafka_consumer(tracker, sink, indexer)
        for _ in range(messaging_env.redis_max_deliveries - 1):
            await tracker.record_delivery("record-events-0-0")

        assert await _run(consumer, "r1") is False

        assert sink.abandoned == [("r1", messaging_env.redis_max_deliveries)]
        consumer._requeue_message.assert_not_called()
        consumer._commit_offset.assert_awaited_once()

    async def test_attempt_counts_that_cannot_be_read_or_written_do_not_block_indexing(self) -> None:
        store = AsyncMock(spec=RetryManager)
        store.get_count.return_value = 0
        for name in ("unfinished_attempts", "start_attempt", "finish_attempt"):
            getattr(store, name).side_effect = ConnectionError("redis down")
        indexer = Indexer(RetryManager(LOGGER, redis_client=FakeRedis()))
        consumer = _kafka_consumer(store, Sink(), indexer)

        assert await _run(consumer, "r1") is True

        assert indexer.seen == ["r1"]


class TestSuspectLockAcrossRestarts:
    """A consumer started again runs on a new worker loop; its suspect lock must too."""

    @pytest.mark.parametrize("build", [_consumer, _kafka_consumer])
    def test_a_restarted_consumer_can_hold_suspects_back_again(self, build, tracker) -> None:
        consumer = build(tracker, Sink(), Indexer(tracker))

        async def contend() -> None:
            await consumer._suspects.acquire()
            waiter = asyncio.ensure_future(consumer._suspects.acquire())
            await asyncio.sleep(0)
            consumer._suspects.release()
            await waiter
            consumer._suspects.release()

        for _ in range(2):
            loop = asyncio.new_event_loop()
            try:
                consumer._on_worker_loop_started(loop)
                loop.run_until_complete(contend())
            finally:
                loop.close()
