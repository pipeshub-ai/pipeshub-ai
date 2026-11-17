"""F25: on the FIFO read path a transient failure must stop the partition's batch, never skip or commit past a message.

On main, ``IndexingKafkaConsumer.__consume_loop`` (``FAIR_SCHEDULING_ENABLED=false``) handles a failure with
``__finish_partition(message, retry_current=True)`` and ``continue``. That releases the reservation of the message
already in flight on the partition, so a later offset runs and commits beside it, and the in-flight message's own
finish then seeks over the failed offset. The failed offset, and the deferred ones before it, are never handled, and
the commit is already past them, so a restart does not bring them back. The same happens with no failure when the
in-flight message ends during a later message's parse (the loop parsed messages it could not dispatch), and to the
in-flight message itself when it fails and its rewind lands during the next message's parse.

Runs the real consume loop against the in-memory broker in ``tests.support.fake_kafka``. A failure is a
``ConnectionError`` from ``open_stream_message`` (a key-store outage): F19 keeps it retryable, by design.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from aiokafka.errors import IllegalStateError
from aiokafka.structs import TopicPartition

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    StreamMessage,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer import indexing_consumer as consumer_module
from app.services.messaging.kafka.consumer import remembered as remembered_module
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from tests.support.fake_kafka import FakeAIOKafkaConsumer, FakeKafkaBroker

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Iterator

TOPIC = "record-events"
GROUP = "records_consumer_group"


def _good(offset: int) -> dict:
    return {"eventType": "newRecord", "timestamp": 1,
            "payload": {"recordId": f"r{offset}", "orgId": "org-1", "connectorId": "conn-1",
                        "extension": "txt", "mimeType": "text/plain"}}


def _consumer(fair: bool) -> IndexingKafkaConsumer:
    return IndexingKafkaConsumer(
        logging.getLogger("test.f25"),
        KafkaConsumerConfig(topics=[TOPIC], client_id="f25", group_id=GROUP, auto_offset_reset="earliest",
                            enable_auto_commit=False, bootstrap_servers=["kafka:9092"]),
        fair_scheduler_config=FairSchedulerConfig(enabled=fair, key_fields=("orgId", "connectorId")),
    )


class Handler:
    """Records handled offsets. ``hold`` blocks a record's handling until released (the handler runs on the worker
    thread's loop, so a threading.Event, waited on in a thread)."""

    def __init__(self) -> None:
        self.seen: list[int] = []
        self.running: set[int] = set()
        self.max_running = 0
        self.hold: dict[int, threading.Event] = {}

    async def __call__(self, message: StreamMessage) -> AsyncGenerator[PipelineEvent, None]:
        offset = int(message.payload["recordId"][1:])
        self.seen.append(offset)
        self.running.add(offset)
        self.max_running = max(self.max_running, len(self.running))
        gate = self.hold.get(offset)
        if gate is not None:
            await asyncio.to_thread(gate.wait, 10)
        self.running.discard(offset)
        yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData())
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)


class FlakyKeyStore:
    """``open_stream_message`` that raises ConnectionError for chosen records: ``fail[offset]`` times, or for ever while
    ``offset in down``."""

    def __init__(self, fail: dict[int, int] | None = None, down: set[int] | None = None) -> None:
        self.fail = dict(fail or {})
        self.down = set(down or ())
        self.real = consumer_module.open_stream_message

    async def __call__(self, topic: str, raw: object, model: type) -> object:
        offset = int(raw["payload"]["recordId"][1:])  # type: ignore[index]
        if offset in self.down or self.fail.get(offset, 0) > 0:
            self.fail[offset] = self.fail.get(offset, 0) - 1
            raise ConnectionError(f"key store down while opening r{offset}")
        return await self.real(topic, raw, model)


@pytest.fixture
def broker(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeKafkaBroker]:
    monkeypatch.setenv("MESSAGE_TIMEOUT_MS", "10")
    monkeypatch.setenv("SHUTDOWN_TASK_TIMEOUT", "5")
    broker = FakeKafkaBroker()
    broker.polls = 0
    real_getmany = FakeAIOKafkaConsumer.getmany

    async def counting_getmany(self, *partitions, **kwargs):  # noqa: ANN202
        broker.polls += 1
        # A real poll after a seek waits on the network. The fake returns at once, so without this a partition that
        # keeps failing would spin the loop and starve the test's own coroutine.
        await asyncio.sleep(0)
        return await real_getmany(self, *partitions, **kwargs)

    monkeypatch.setattr(FakeAIOKafkaConsumer, "getmany", counting_getmany)
    factory = broker.consumer_factory()
    with patch.object(consumer_module, "AIOKafkaConsumer", factory), \
            patch.object(remembered_module, "AIOKafkaConsumer", factory):
        yield broker


def _produce(broker: FakeKafkaBroker, n: int) -> None:
    for offset in range(n):
        broker.produce(TOPIC, _good(offset))


def _commits(broker: FakeKafkaBroker) -> list[int]:
    return [offsets[tp] for c in broker.consumers for offsets in c.commit_calls for tp in offsets if tp.topic == TOPIC]


async def _until(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(0.01)
    return True


async def _settle(broker: FakeKafkaBroker, polls: int = 20) -> None:
    """Keep the loop polling: a late seek-back or a skipped offset shows up here."""
    start = broker.polls
    await _until(lambda: broker.polls >= start + polls)


async def _consume_all(broker: FakeKafkaBroker, fair: bool, n: int, keys: FlakyKeyStore,
                       handler: Handler | None = None) -> Handler:
    handler = handler or Handler()
    consumer = _consumer(fair=fair)
    with patch.object(consumer_module, "open_stream_message", keys):
        await consumer.start(handler)
        try:
            await _until(lambda: broker.committed_offset(GROUP, TOPIC) == n and set(handler.seen) >= set(range(n)))
            await _settle(broker)
        finally:
            await consumer.stop()
    return handler


MODES = pytest.mark.parametrize("fair", [False, True], ids=["fifo", "fair_scheduling_control"])


@MODES
@pytest.mark.parametrize(("failing", "n"), [
    pytest.param({2: 1, 5: 1}, 10, id="two_failures_in_one_batch"),
    pytest.param({1: 1}, 10, id="failure_right_behind_the_in_flight_message"),
    pytest.param({9: 1}, 10, id="failure_at_the_end_of_the_batch"),
    pytest.param({0: 1}, 10, id="failure_at_the_head"),
    pytest.param(dict.fromkeys(range(2, 30, 3), 1), 30, id="every_third_record"),
    pytest.param({4: 3}, 10, id="one_record_fails_three_times"),
])
async def test_every_message_is_handled_once_in_order_after_transient_failures(
    broker: FakeKafkaBroker, fair: bool, failing: dict[int, int], n: int
) -> None:
    _produce(broker, n)
    handler = await _consume_all(broker, fair, n, FlakyKeyStore(fail=failing))

    lost = sorted(set(range(n)) - set(handler.seen))
    assert not lost, f"never handled: {lost}; handled order {handler.seen}"
    assert sorted(handler.seen) == list(range(n)), f"handled more than once: {handler.seen}"
    assert broker.committed_offset(GROUP, TOPIC) == n
    if not fair:  # FIFO's contract: one message per partition at a time, in offset order, commits only move forward
        assert handler.seen == list(range(n))
        commits = _commits(broker)
        assert commits == sorted(commits), f"commit went backwards: {commits}"


@MODES
async def test_a_message_that_keeps_failing_is_never_committed_past_and_runs_once_it_recovers(
    broker: FakeKafkaBroker, fair: bool
) -> None:
    _produce(broker, 10)
    keys = FlakyKeyStore(down={3})
    handler = Handler()
    consumer = _consumer(fair=fair)
    with patch.object(consumer_module, "open_stream_message", keys):
        await consumer.start(handler)
        try:
            await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 3)
            await _settle(broker, polls=40)
            held_commit, held_seen = broker.committed_offset(GROUP, TOPIC), list(handler.seen)
            keys.down.clear()  # the key store recovers
            await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 10)
            await _settle(broker)
        finally:
            await consumer.stop()

    assert held_commit == 3, f"committed past the failing offset 3: {held_commit}"
    if not fair:  # fair scheduling may handle later offsets ahead; only FIFO promises order
        assert held_seen == [0, 1, 2], f"ran past the failing offset 3: {held_seen}"
    assert sorted(handler.seen) == list(range(10))
    assert broker.committed_offset(GROUP, TOPIC) == 10


async def test_a_failure_does_not_release_the_partition_held_by_the_in_flight_message(broker: FakeKafkaBroker) -> None:
    _produce(broker, 6)
    handler = Handler()
    release = threading.Event()
    handler.hold[0] = release
    keys = FlakyKeyStore(fail={1: 1})
    consumer = _consumer(fair=False)
    with patch.object(consumer_module, "open_stream_message", keys):
        await consumer.start(handler)
        try:
            await _until(lambda: keys.fail[1] == 0)  # r1's failure happened while r0 is in flight
            await _settle(broker)
            seen_while_r0_runs = list(handler.seen)
            release.set()
            await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 6)
            await _settle(broker)
        finally:
            await consumer.stop()

    assert seen_while_r0_runs == [0], f"ran beside the in-flight r0: {seen_while_r0_runs}"
    assert handler.max_running == 1
    assert handler.seen == list(range(6))


async def test_a_message_behind_a_deferred_one_does_not_run_first_when_the_partition_frees_mid_batch(
    broker: FakeKafkaBroker,
) -> None:
    # No failure at all: r1 is deferred behind the in-flight r0, and r0 ends while r2's parse is suspended (a large
    # payload is parsed in a thread). On main r2 then finds the partition free and runs ahead of r1, and the offset r2
    # defers overwrites r0's seek back to r1: r1 is never handled ([0, 2, 3, 4, 5]). The fix stops the batch at r1, so
    # r2 is not parsed while r0 runs and the poll fallback releases r0: a regression guard for that `break`.
    _produce(broker, 6)
    handler = Handler()
    release = threading.Event()
    handler.hold[0] = release
    consumer = _consumer(fair=False)
    real_open = consumer_module.open_stream_message
    armed = [True]

    async def slow_r2_once(topic: str, raw: object, model: type) -> object:
        if raw["payload"]["recordId"] == "r2" and armed[0]:  # type: ignore[index]
            armed[0] = False
            release.set()
            await _until(lambda: not consumer._in_flight_partitions)  # r0's __finish_partition has run
        return await real_open(topic, raw, model)

    with patch.object(consumer_module, "open_stream_message", slow_r2_once):
        await consumer.start(handler)
        try:
            # Fixed code never parses r2 while r0 runs, so r0 is released here instead.
            await _until(lambda: release.is_set() or broker.polls >= 20)
            release.set()
            await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 6)
            await _settle(broker)
        finally:
            await consumer.stop()

    assert handler.seen == list(range(6)), f"handled out of order or twice: {handler.seen}"
    commits = _commits(broker)
    assert commits == sorted(commits), f"commit went backwards: {commits}"


async def test_a_dispatch_failure_after_reserving_redelivers_the_message(broker: FakeKafkaBroker) -> None:
    # The other branch of the same except: the message holds the reservation, so finishing it is right; the batch must
    # still stop there.
    _produce(broker, 6)
    consumer = _consumer(fair=False)
    real_start = consumer._IndexingKafkaConsumer__start_processing_task
    failed: list[int] = []

    async def start_once_failing(message, parsed=None, *args, **kwargs):  # noqa: ANN202
        if message.offset == 2 and not failed:
            failed.append(message.offset)
            raise RuntimeError("worker loop busy")
        return await real_start(message, parsed, *args, **kwargs)

    consumer._IndexingKafkaConsumer__start_processing_task = start_once_failing
    handler = Handler()
    await consumer.start(handler)
    try:
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 6)
        await _settle(broker)
    finally:
        await consumer.stop()

    assert failed == [2]
    assert handler.seen == list(range(6))


async def test_a_restart_after_the_failures_has_nothing_left_to_recover(broker: FakeKafkaBroker) -> None:
    # Stop while r3 keeps failing, restart in the same group once it recovers: every offset is handled at least once.
    _produce(broker, 10)
    keys = FlakyKeyStore(fail={1: 1}, down={3})
    handler = Handler()
    first = _consumer(fair=False)
    with patch.object(consumer_module, "open_stream_message", keys):
        await first.start(handler)
        try:
            await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 3)
            await _settle(broker)
        finally:
            await first.stop()
        committed_at_stop = broker.committed_offset(GROUP, TOPIC)
        keys.down.clear()
        second = _consumer(fair=False)
        await second.start(handler)
        try:
            await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 10)
            await _settle(broker)
        finally:
            await second.stop()

    assert committed_at_stop == 3
    assert sorted(set(handler.seen)) == list(range(10)), f"lost across the restart: {handler.seen}"
    assert handler.seen == list(range(10))


@pytest.mark.parametrize("next_message", ["parses", "parse_raises", "backoff_not_elapsed", "poison"])
async def test_a_rewind_during_the_next_parse_is_not_overtaken(broker: FakeKafkaBroker, next_message: str) -> None:
    # r0 fails in flight (its commit failed), so its finish seeks back to 0 and frees the partition, and it lands while
    # r1's parse is suspended. Without the rewind check r1 overwrites that seek (it runs, its parse raises and it is
    # re-read from 1, or its backoff seeks to 1) and r0 is never handled.
    for offset in range(6):
        record = _good(offset)
        if offset == 1 and next_message == "backoff_not_elapsed":
            record["payload"]["_retry_not_before"] = time.time() + 0.5
        broker.produce(TOPIC, record)
    handler = Handler()
    consumer = _consumer(fair=False)
    real_wrapper = consumer._IndexingKafkaConsumer__process_message_wrapper
    release = threading.Event()
    failed: list[int] = []

    async def wrapper(message, *args, **kwargs):  # noqa: ANN202
        if message.offset == 0 and not failed:
            failed.append(0)
            await asyncio.to_thread(release.wait, 10)
            raise RuntimeError("commit failed")  # on_future_done -> retry_current=True: r0 must be read again
        return await real_wrapper(message, *args, **kwargs)

    consumer._IndexingKafkaConsumer__process_message_wrapper = wrapper
    real_open = consumer_module.open_stream_message
    armed = [True]

    async def slow_r1_once(topic: str, raw: object, model: type) -> object:
        if raw["payload"]["recordId"] == "r1" and armed[0]:  # type: ignore[index]
            armed[0] = False
            await _until(lambda: bool(failed))  # r0 is running
            release.set()
            await _until(lambda: not consumer._in_flight_partitions)  # r0's finish (seek back to 0) has run
            if next_message == "parse_raises":
                raise ConnectionError("key store down while opening r1")
            if next_message == "poison":
                raise TypeError("not a StreamMessage")  # F19's poison on this read only: it would be committed past r0
        return await real_open(topic, raw, model)

    with patch.object(consumer_module, "open_stream_message", slow_r1_once):
        await consumer.start(handler)
        try:
            await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 6, timeout=8)
            await _settle(broker)
        finally:
            await consumer.stop()

    assert failed == [0]
    assert handler.seen == list(range(6)), f"handled {handler.seen}"
    commits = _commits(broker)
    assert commits == sorted(commits), f"commit went backwards: {commits}"


@pytest.mark.parametrize("offsets", [(5, 3), (3, 5)], ids=["lower_second", "lower_first"])
def test_defer_offset_keeps_the_lowest(offsets: tuple[int, int]) -> None:
    consumer = _consumer(fair=False)
    tp = TopicPartition(TOPIC, 0)
    for offset in offsets:
        consumer._IndexingKafkaConsumer__defer_offset(tp, offset)
    assert consumer._deferred_partition_offsets == {tp: 3}


async def test_messages_behind_a_deferred_one_are_not_parsed(broker: FakeKafkaBroker) -> None:
    # The batch stops at the first message it cannot dispatch: parsing the ones behind it is thrown away (they are read
    # again once the in-flight message ends), and every extra parse is one more chance to fail or to suspend.
    _produce(broker, 6)
    handler = Handler()
    release = threading.Event()
    handler.hold[0] = release
    consumer = _consumer(fair=False)
    real_open = consumer_module.open_stream_message
    parsed: list[str] = []

    async def counting_open(topic: str, raw: object, model: type) -> object:
        parsed.append(raw["payload"]["recordId"])  # type: ignore[index]
        return await real_open(topic, raw, model)

    with patch.object(consumer_module, "open_stream_message", counting_open):
        await consumer.start(handler)
        try:
            await _until(lambda: handler.seen == [0])
            await _settle(broker)
            parsed_while_r0_runs = list(parsed)
            release.set()
            await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 6)
        finally:
            await consumer.stop()

    assert parsed_while_r0_runs == ["r0", "r1"]
    assert handler.seen == list(range(6))


async def test_a_pause_that_raises_does_not_leave_the_partition_reserved(broker: FakeKafkaBroker) -> None:
    # A rebalance that revokes the partition during the parse makes aiokafka's pause() raise IllegalStateError. The
    # reservation must not outlive it: when the partition comes back, its head would otherwise be "busy" for ever.
    _produce(broker, 6)
    handler = Handler()
    consumer = _consumer(fair=False)
    await consumer.start(handler)
    real_pause = consumer.consumer.pause
    raised: list[int] = []

    def pause_once_raising(*partitions: TopicPartition) -> None:
        if not raised:
            raised.append(1)
            raise IllegalStateError(f"No current assignment for partition {partitions[0]}")
        real_pause(*partitions)

    consumer.consumer.pause = pause_once_raising
    try:
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 6, timeout=5)
        await _settle(broker)
        left_in_flight = set(consumer._in_flight_partitions)
    finally:
        await consumer.stop()

    assert raised == [1]
    assert handler.seen == list(range(6)), f"handled {handler.seen}; reserved with no owner: {left_in_flight}"


async def test_a_seek_that_raises_does_not_drop_the_other_partitions_batch(
    broker: FakeKafkaBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # p0's head fails to parse and the rewind raises (a rebalance revoked p0 during the parse). p1's records in the same
    # batch were already fetched past: if the raise escaped, the loop would drop them and they would never be handled.
    for offset in range(3):
        broker.produce(TOPIC, _good(offset), partition=0)
    for offset in range(100, 105):
        broker.produce(TOPIC, _good(offset), partition=1)
    real_seek = FakeAIOKafkaConsumer.seek
    raised: list[int] = []

    def seek_raising_once_on_p0(self: FakeAIOKafkaConsumer, tp: TopicPartition, offset: int) -> None:
        if tp.partition == 0 and not raised:
            raised.append(offset)
            raise IllegalStateError(f"No current assignment for partition {tp}")
        real_seek(self, tp, offset)

    monkeypatch.setattr(FakeAIOKafkaConsumer, "seek", seek_raising_once_on_p0)
    handler = Handler()
    consumer = _consumer(fair=False)
    with patch.object(consumer_module, "open_stream_message", FlakyKeyStore(fail={0: 1})):
        await consumer.start(handler)
        try:
            await _until(lambda: broker.committed_offset(GROUP, TOPIC, partition=1) == 5, timeout=5)
            await _settle(broker)
        finally:
            await consumer.stop()

    assert raised == [0]
    assert [o for o in handler.seen if o >= 100] == list(range(100, 105))

