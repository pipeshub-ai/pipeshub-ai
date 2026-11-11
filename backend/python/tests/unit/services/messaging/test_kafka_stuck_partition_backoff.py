"""F45: a partition whose head is not dispatched is not re-read in a tight loop.

When the head of a partition fails (a ConnectionError from the key store; F19 keeps it retryable) the consumer seeks
back to it and stops the partition's batch (F25 on FIFO, the read phase on fair scheduling). Before F45 nothing slowed
the next poll down: the head was re-fetched, re-parsed and logged at ERROR about 2,000 times a second, at ~70% of a
core. FIFO did the same, silently, for a re-queued head waiting out its ``_retry_not_before``.

With F45 the partition is paused until a backoff passes (1 s, doubling per failure of the same offset, capped at 30 s),
or until a re-queued head is due. Other partitions keep flowing. Runs the real consume loop against the in-memory
broker. The consumer module's clock is a fake whose monotonic time only moves when a test advances it, so no test
depends on wall-clock timing: "not re-read" is checked over a number of polls, not seconds.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from aiokafka.errors import IllegalStateError
from aiokafka.structs import TopicPartition

from app.services.messaging.kafka.consumer import indexing_consumer as consumer_module
from tests.unit.services.messaging import test_kafka_fifo_redelivery as f25_tests
from tests.unit.services.messaging.test_kafka_fifo_redelivery import (
    GROUP,
    MODES,
    TOPIC,
    FlakyKeyStore,
    Handler,
    _consumer,
    _good,
    _until,
)

broker = f25_tests.broker  # F25's in-memory broker fixture, shared rather than copied
TP0 = TopicPartition(TOPIC, 0)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from tests.support.fake_kafka import FakeKafkaBroker

BASE_S = 1.0
CAP_S = 30.0


class Clock:
    """Stands in for the consumer module's ``time``: monotonic and wall time are both frozen until ``advance``, so a
    ``_retry_not_before`` stamped from it falls due exactly when the test says so."""

    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.wall0 = time.time()
        self.skew = 0.0

    def monotonic(self) -> float:
        return self.t0 + self.skew

    def time(self) -> float:
        return self.wall0 + self.skew

    def advance(self, seconds: float) -> None:
        self.skew += seconds

    def __getattr__(self, name: str) -> object:
        return getattr(time, name)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[Clock]:
    fake = Clock()
    monkeypatch.setattr(consumer_module, "time", fake)
    yield fake


class Keys(FlakyKeyStore):
    """FlakyKeyStore that counts the opens of each record (fair scheduling opens a record twice per read: once for the
    connector-off filter, once on enqueue)."""

    def __init__(self, down: set[int]) -> None:
        super().__init__(down=down)
        self.opens: dict[int, int] = {}

    async def __call__(self, topic: str, raw: object, model: type) -> object:
        offset = int(raw["payload"]["recordId"][1:])  # type: ignore[index]
        self.opens[offset] = self.opens.get(offset, 0) + 1
        return await super().__call__(topic, raw, model)


def _produce(broker: FakeKafkaBroker, n: int, partition: int = 0, first: int = 0) -> None:
    for offset in range(first, first + n):
        broker.produce(TOPIC, _good(offset), partition=partition)


async def _stays(broker: FakeKafkaBroker, check: Callable[[], bool], polls: int = 30) -> bool:
    """Whether ``check()`` holds over the next ``polls`` polls of the consume loop."""
    start, broken = broker.polls, False

    def done() -> bool:
        nonlocal broken
        broken = broken or not check()
        return broken or broker.polls >= start + polls

    assert await _until(done)
    return not broken


class Running:
    """The consumer under test, started with ``keys`` patched in, stopped on exit."""

    def __init__(self, fair: bool, keys: Keys, handler: Handler | None = None) -> None:
        self.consumer = _consumer(fair=fair)
        self.handler = handler or Handler()
        self.patch = patch.object(consumer_module, "open_stream_message", keys)

    async def __aenter__(self) -> Running:
        self.patch.start()
        await self.consumer.start(self.handler)
        return self

    async def __aexit__(self, *exc: object) -> None:
        try:
            await self.consumer.stop()
        finally:
            self.patch.stop()


FIFO = pytest.mark.parametrize("fair", [False], ids=["fifo"])


# --- the schedule ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("failures", "expected"), [(1, 1.0), (2, 2.0), (3, 4.0), (5, 16.0), (6, 30.0), (7, 30.0)])
def test_the_backoff_doubles_per_failure_from_one_second_and_is_capped_at_thirty(failures: int, expected: float) -> None:
    assert consumer_module._stuck_retry_delay_s(failures) == expected


def test_the_backoff_of_a_partition_stuck_for_weeks_does_not_overflow() -> None:
    # 30 s apart, 100,000 failures is about 35 days; 2.0 ** 100_000 alone raises OverflowError.
    assert consumer_module._stuck_retry_delay_s(100_000) == CAP_S


# --- a failing head is held, not spun on ------------------------------------------------------------------------------


@MODES
async def test_a_failing_head_is_not_re_read_until_its_backoff_passes(
    broker: FakeKafkaBroker, clock: Clock, fair: bool, caplog: pytest.LogCaptureFixture
) -> None:
    _produce(broker, 5)
    keys = Keys(down={2})
    caplog.set_level(logging.ERROR, logger="test.f25")
    async with Running(fair, keys) as run:
        assert await _until(lambda: keys.opens.get(2, 0) >= 1)
        per_read = keys.opens[2]
        assert await _stays(broker, lambda: keys.opens[2] == per_read), f"re-read while held: {keys.opens[2]} opens"
        assert len([r for r in caplog.records if r.levelno >= logging.ERROR]) == 1  # one line per attempt
        assert broker.committed_offset(GROUP, TOPIC) == 2

        clock.advance(BASE_S)
        assert await _until(lambda: keys.opens[2] == 2 * per_read), "not re-read once the backoff passed"

        keys.down.clear()
        clock.advance(2 * BASE_S)
        assert await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 5)
    assert sorted(run.handler.seen) == [0, 1, 2, 3, 4]


@MODES
async def test_the_wait_doubles_while_the_same_head_keeps_failing(broker: FakeKafkaBroker, clock: Clock, fair: bool) -> None:
    _produce(broker, 5)
    keys = Keys(down={2})
    async with Running(fair, keys):
        assert await _until(lambda: keys.opens.get(2, 0) >= 1)
        per_read = keys.opens[2]
        for reads, wait in enumerate((1.0, 2.0, 4.0), start=1):
            clock.advance(wait - 0.01)
            assert await _stays(broker, lambda r=reads: keys.opens[2] == r * per_read), f"re-read before {wait} s"
            clock.advance(0.01)
            assert await _until(lambda r=reads: keys.opens[2] == (r + 1) * per_read), f"not re-read after {wait} s"


@MODES
async def test_a_head_that_recovered_does_not_pass_its_backoff_on_to_the_next_failure(
    broker: FakeKafkaBroker, clock: Clock, fair: bool
) -> None:
    _produce(broker, 6)
    keys = Keys(down={2, 4})
    async with Running(fair, keys) as run:
        assert await _until(lambda: keys.opens.get(2, 0) >= 1)
        per_read = keys.opens[2]
        for reads, wait in enumerate((1.0, 2.0), start=1):  # r2 fails three times
            clock.advance(wait)
            assert await _until(lambda r=reads: keys.opens[2] == (r + 1) * per_read)
        keys.down.discard(2)
        clock.advance(4.0)
        assert await _until(lambda: keys.opens.get(4, 0) >= per_read)  # r2, r3 ran; r4 fails for the first time
        first = keys.opens[4]
        clock.advance(BASE_S)  # 1 s, not the 8 s r2's next failure would have earned
        assert await _until(lambda: keys.opens[4] > first)
        keys.down.clear()
        clock.advance(2 * BASE_S)
        assert await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 6)
    assert sorted(run.handler.seen) == list(range(6))


@MODES
async def test_other_partitions_keep_flowing_while_one_is_held(broker: FakeKafkaBroker, clock: Clock, fair: bool) -> None:
    _produce(broker, 5, partition=0)
    _produce(broker, 40, partition=1, first=100)  # fair dispatches ~20/s per partition here (one per 50 ms poll)
    keys = Keys(down={2})
    async with Running(fair, keys) as run:
        assert await _until(lambda: keys.opens.get(2, 0) >= 1)
        first = keys.opens[2]
        re_read: list[int] = []

        def p1_done() -> bool:
            if keys.opens[2] != first:  # reading p1 must not release p0's hold (review F6)
                re_read.append(keys.opens[2])
            return broker.committed_offset(GROUP, TOPIC, 1) == 40

        assert await _until(p1_done), "p1 starved by the held p0"
        assert not re_read, f"p0 re-read while held and p1 flowed: {re_read[:3]}"
        assert broker.committed_offset(GROUP, TOPIC, 0) == 2
        keys.down.clear()
        clock.advance(BASE_S)
        assert await _until(lambda: broker.committed_offset(GROUP, TOPIC, 0) == 5)
    assert sorted(run.handler.seen) == [0, 1, 2, 3, 4, *range(100, 140)]
    if not fair:
        assert [o for o in run.handler.seen if o < 100] == [0, 1, 2, 3, 4]


# --- what may resume a held partition -----------------------------------------------------------------------------------


async def test_the_in_flight_message_finishing_does_not_resume_a_held_partition(broker: FakeKafkaBroker, clock: Clock) -> None:
    """FIFO: r1 fails while r0 runs. r0's finish seeks to r1 and, before F45, resumed the partition at once."""
    _produce(broker, 4)
    keys = Keys(down={1})
    handler = Handler()
    gate = handler.hold[0] = threading.Event()
    async with Running(False, keys, handler):
        assert await _until(lambda: keys.opens.get(1, 0) >= 1 and 0 in handler.running)
        gate.set()
        assert await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 1)
        assert await _stays(broker, lambda: keys.opens[1] == 1), "r0's finish resumed the held partition"
        keys.down.clear()
        clock.advance(BASE_S)
        assert await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 4)
    assert handler.seen == [0, 1, 2, 3]


async def test_a_dispatch_that_fails_after_reserving_holds_the_partition(broker: FakeKafkaBroker, clock: Clock) -> None:
    """FIFO: the head parsed and reserved its partition, then the hand-off to the worker loop raised (a closed worker
    loop). The finish seeks back to it; the hold, taken first, keeps the finish from resuming the partition at once."""

    class WorkerLoopDown:
        """The consumer module's ``asyncio``, with ``run_coroutine_threadsafe`` raising while ``down``."""

        def __init__(self) -> None:
            self.down = True
            self.attempts = 0

        def run_coroutine_threadsafe(self, coro: object, loop: object) -> object:
            self.attempts += 1
            if self.down:
                raise RuntimeError("Event loop is closed")
            return asyncio.run_coroutine_threadsafe(coro, loop)  # type: ignore[arg-type]

        def __getattr__(self, name: str) -> object:
            return getattr(asyncio, name)

    _produce(broker, 4)
    worker = WorkerLoopDown()
    async with Running(False, Keys(down=set())) as run:
        with patch.object(consumer_module, "asyncio", worker):
            assert await _until(lambda: worker.attempts >= 1)
            assert await _stays(broker, lambda: worker.attempts == 1), f"re-dispatched while held: {worker.attempts}"
            worker.down = False
            clock.advance(BASE_S)
            assert await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 4)
    assert run.handler.seen == [0, 1, 2, 3]


def test_a_finish_resumes_a_partition_whose_hold_has_passed(clock: Clock) -> None:
    """A held partition is resumed by its in-flight message's finish once the deadline has passed, not left for the next
    __apply_backpressure (which, with nothing else to read, comes a whole MESSAGE_TIMEOUT_MS poll later)."""
    consumer = _consumer(fair=False)
    consumer.consumer = MagicMock()
    consumer.running = True
    message = MagicMock(topic=TOPIC, partition=0, offset=2)
    consumer.consumer.pause.reset_mock()
    consumer._IndexingKafkaConsumer__hold_partition(TP0, 2)

    consumer._IndexingKafkaConsumer__finish_partition(message, retry_current=True)
    consumer.consumer.resume.assert_not_called()

    clock.advance(BASE_S)
    consumer._IndexingKafkaConsumer__finish_partition(message, retry_current=True)
    consumer.consumer.resume.assert_called_once_with(TP0)


# --- a re-queued head waiting out its backoff ---------------------------------------------------------------------------


@FIFO  # fair scheduling buffers it with not_before and does not re-read it (control measured on real Kafka: 20 polls/s)
async def test_a_re_queued_head_is_read_once_and_held_until_it_is_due(broker: FakeKafkaBroker, clock: Clock, fair: bool) -> None:
    for offset in range(4):
        value = _good(offset)
        if offset == 1:
            value["payload"]["_retry_not_before"] = clock.time() + 60
        broker.produce(TOPIC, value)
    keys = Keys(down=set())
    async with Running(fair, keys) as run:
        assert await _until(lambda: keys.opens.get(1, 0) >= 1)
        assert await _stays(broker, lambda: keys.opens[1] == 1), f"spun on a head that is not due: {keys.opens[1]} opens"
        assert run.handler.seen == [0]
        clock.advance(59)  # past any failure backoff, short of the head's own due time
        assert await _stays(broker, lambda: keys.opens[1] == 1), f"re-read before it was due: {keys.opens[1]} opens"
        clock.advance(1)
        assert await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 4)
    assert run.handler.seen == [0, 1, 2, 3]


async def test_a_re_queued_head_that_then_fails_waits_the_first_failure_backoff(broker: FakeKafkaBroker, clock: Clock) -> None:
    """Waiting out ``_retry_not_before`` is not a failure: if the head's parse fails once it is due, that is its first
    failure, so it waits 1 s, not 2 (CodeRabbit on #5691)."""
    for offset in range(4):
        value = _good(offset)
        if offset == 1:
            value["payload"]["_retry_not_before"] = clock.time() + 60
        broker.produce(TOPIC, value)
    keys = Keys(down=set())
    async with Running(False, keys):
        assert await _until(lambda: keys.opens.get(1, 0) >= 1)
        keys.down.add(1)
        clock.advance(60)
        assert await _until(lambda: keys.opens[1] == 2)  # due, read again, the parse fails
        clock.advance(BASE_S - 0.01)
        assert await _stays(broker, lambda: keys.opens[1] == 2), "re-read before its first failure's 1 s"
        clock.advance(0.01)
        assert await _until(lambda: keys.opens[1] == 3), "the not-due wait was counted as a failure: waited 2 s, not 1"
        keys.down.clear()
        clock.advance(2 * BASE_S)
        assert await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 4)


@MODES
@pytest.mark.parametrize("not_before", ["NaN", "inf"])
async def test_a_retry_time_that_is_not_a_finite_number_counts_as_due(
    broker: FakeKafkaBroker, clock: Clock, fair: bool, not_before: str
) -> None:
    """``float()`` accepts "NaN" and "inf". NaN compares false both ways, so the head was sought back and never held: the
    loop spun on it for ever. inf held it for ever. Either is treated as malformed, like an unparseable value: due now."""
    for offset in range(4):
        value = _good(offset)
        if offset == 1:
            value["payload"]["_retry_not_before"] = not_before
        broker.produce(TOPIC, value)
    async with Running(fair, Keys(down=set())) as run:
        assert await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 4), f"stuck on {not_before}: {run.handler.seen}"
    assert sorted(run.handler.seen) == [0, 1, 2, 3]


# --- failure path -------------------------------------------------------------------------------------------------------


def test_a_pause_that_raises_leaves_the_partition_unheld_and_does_not_raise(
    clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    """A rebalance can revoke the partition during the parse; its pause then raises. In the FIFO failure branch the hold
    runs before the redelivery, so an escaping exception would skip it and become an "Error in consume_messages loop"
    (a 1 s stall of every partition). Nor may it keep a hold on a partition this consumer no longer reads."""
    consumer = _consumer(fair=False)
    consumer.consumer = MagicMock()
    consumer.consumer.pause.side_effect = IllegalStateError("No current assignment for partition")
    tp = TopicPartition(TOPIC, 0)
    caplog.set_level(logging.WARNING, logger="test.f25")

    consumer._IndexingKafkaConsumer__hold_partition(tp, 2)

    assert not consumer._IndexingKafkaConsumer__is_held(tp)
    assert [r.levelno for r in caplog.records] == [logging.WARNING]


def test_a_hold_asked_for_after_the_consumer_is_gone_is_a_quiet_no_op(
    clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    consumer = _consumer(fair=False)  # never started: no Kafka client, as during stop()
    tp = TopicPartition(TOPIC, 0)
    caplog.set_level(logging.DEBUG, logger="test.f25")

    consumer._IndexingKafkaConsumer__hold_partition(tp, 2)

    assert not consumer._IndexingKafkaConsumer__is_held(tp)
    assert not caplog.records  # not a misleading "Failed to pause" line


# --- rebalance --------------------------------------------------------------------------------------------------------


@MODES
async def test_a_hold_lost_to_a_reassignment_does_not_stall_the_recovered_head(
    broker: FakeKafkaBroker, clock: Clock, fair: bool
) -> None:
    """aiokafka's rebalance is eager: every partition comes back unpaused, read from the commit (so does a restart). The
    old deadline must not then keep the recovered partition paused after its head is dispatched (review F1)."""
    _produce(broker, 25)  # more than one poll (fair buffers a whole batch, so it must read again after the head)
    keys = Keys(down={2})
    async with Running(fair, keys) as run:
        assert await _until(lambda: keys.opens.get(2, 0) >= 1)
        per_read = keys.opens[2]
        for reads, wait in enumerate((1.0, 2.0, 4.0, 8.0), start=2):  # the wait is now 16 s
            clock.advance(wait)
            assert await _until(lambda r=reads: keys.opens[2] == r * per_read)
        fake = run.consumer.consumer
        keys.down.clear()
        fake.paused_partitions.discard(TP0)  # the reassignment: a fresh, unpaused partition state ...
        fake.position[TP0] = broker.committed_offset(GROUP, TOPIC)  # ... read from the commit
        broker.notify()
        assert await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 25), (
            f"stalled after the head recovered: committed {broker.committed_offset(GROUP, TOPIC)}, paused={fake.paused()}"
        )
    assert sorted(run.handler.seen) == list(range(25))
