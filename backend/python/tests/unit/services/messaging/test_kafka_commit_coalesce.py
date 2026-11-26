"""Kafka fair consumer: offset commits are coalesced per partition, so the parallel path can wake on every finish.

aiokafka sends one commit at a time per consumer, and the consumer used to commit once per finished record: under
parallel dispatch the queue reached thousands, every record waited seconds for its own commit, and the parallel path
paced its reads (a 50 ms poll, and a 2 s one while records were in flight) to stay under it.

Two kinds of tests. Through the real consume loop, worker thread and in-memory broker (F53's harness), the broker's
commit is serialised like aiokafka's and gated. Against the consumer object alone, the watermark paths are driven
directly so a revocation or a stop lands at an exact point. A bound is shortened only for a close whose gate never
opens, and only after the waiters it must not time out have started.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiokafka import TopicPartition

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    StreamMessage,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
    _InFlightOffset,
)
from app.services.messaging.kafka.consumer.watermark_committer import CommitDropped
from app.services.resource_governor.models import ParseTier
from tests.support import fake_kafka
from tests.support import record_consumer_world as world
from tests.unit.services.messaging import test_kafka_fair_finish_wakes_loop as f53
from tests.unit.services.messaging.test_fair_scheduling_kafka import (
    _fair_config,
    _make_message,
)

# F53's fixtures, bound under their own names so pytest finds them here.
broker = f53.broker
harness = f53.harness
only_a_finish_wakes_the_loop = f53.only_a_finish_wakes_the_loop

P0 = TopicPartition("record-events", 0)
SHORT = 0.05


class GatedCommits:
    """Commits serialised by one lock, as aiokafka's _commit_lock does, each waiting until ``open`` is set."""

    def __init__(self) -> None:
        self.open = asyncio.Event()
        self.lock = asyncio.Lock()
        self.waiting = 0
        self.most_waiting = 0
        self.calls: list[dict[TopicPartition, int]] = []


@pytest.fixture
def gated(monkeypatch: pytest.MonkeyPatch) -> GatedCommits:
    gate = GatedCommits()
    real = fake_kafka.FakeAIOKafkaConsumer.commit

    async def commit(self: fake_kafka.FakeAIOKafkaConsumer, offsets: dict[TopicPartition, int] | None = None) -> None:
        gate.waiting += 1
        gate.most_waiting = max(gate.most_waiting, gate.waiting)
        try:
            async with gate.lock:
                await gate.open.wait()
                gate.calls.append(dict(offsets or {}))
                await real(self, offsets)
        finally:
            gate.waiting -= 1

    monkeypatch.setattr(fake_kafka.FakeAIOKafkaConsumer, "commit", commit)
    return gate


class DeadLetteringHandler(f53.Handler):
    """``f53.Handler``, except that the records tagged in ``fail`` raise a terminal error (FileNotFoundError)."""

    def __init__(self, fail: frozenset[str]) -> None:
        super().__init__()
        self.fail = fail

    async def __call__(self, message: StreamMessage):  # noqa: ANN204 - an async generator, as the consumer expects
        if str(message.payload["tag"]) in self.fail:
            yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(tier=ParseTier.LIGHT))
            raise FileNotFoundError("terminal")
        async for event in super().__call__(message):
            yield event


def _settled(consumer: IndexingKafkaConsumer, unsettled: set[int]) -> bool:
    """Every offset read on p0 has settled on the main loop except ``unsettled``."""
    tracker = consumer._offset_tracker
    assert tracker is not None
    return P0 in tracker.tracked_partitions and set(tracker._outstanding.get(P0, {})) == unsettled


# -- through the real loop -------------------------------------------------------------------------------------------
async def test_finishes_during_a_commit_in_flight_wait_for_one_next_commit(
    harness: f53.Harness, gated: GatedCommits
) -> None:
    harness.produce(0, 10)  # ten records of their own: all run at once under parallel dispatch
    handler = f53.Handler()
    consumer = await harness.start(handler, parallel=True)

    # Every record settled on the main loop: the tracker holds none of them, and each asked for its commit then.
    await f53._until(lambda: _settled(consumer, set()) and gated.waiting >= 1)

    # One commit in flight and at most one more carrying the latest watermark; main queued one per record.
    assert gated.most_waiting <= 2, f"{gated.most_waiting} commits queued behind one in flight"
    gated.open.set()
    await f53._until(lambda: harness.committed() == 10)
    assert len(gated.calls) <= 2


async def test_out_of_order_finishes_never_commit_past_an_unfinished_record(
    harness: f53.Harness, gated: GatedCommits
) -> None:
    gated.open.set()
    harness.produce(0, 6)
    handler = f53.Handler(block=frozenset({"p0-001", "p0-003"}))
    consumer = await harness.start(handler, parallel=True)

    # Every record but the two held has settled on the main loop, and asked for whatever commit it could.
    await f53._until(lambda: len(handler.of("p0")) == 6 and _settled(consumer, {1, 3}) and harness.committed() == 1)
    assert max(offset for call in gated.calls for offset in call.values()) <= 1
    handler.release.set()  # both held records finish, in whatever order the worker resumes them
    await f53._until(lambda: harness.committed() == 6)
    values = [call[P0] for call in gated.calls]
    assert values == sorted(values)


@pytest.mark.usefixtures("only_a_finish_wakes_the_loop")
async def test_the_parallel_path_wakes_on_a_finish(harness: f53.Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    # Only a finish can make progress: both poll timeouts are a minute. The events share a record, so they run one
    # after another, and each waits for the previous one's finish. Five, so every waiting event keeps its payload
    # (a read-back of one kept by offset only ends on no finish).
    monkeypatch.setenv("MESSAGE_TIMEOUT_MS", "60000")
    harness.produce(0, 5, record_id="same-record")
    handler = f53.Handler()
    await harness.start(handler, parallel=True)

    await f53._until(lambda: harness.committed() == 5, timeout=5.0)
    assert handler.of("p0") == [f"p0-{i:03d}" for i in range(5)]


async def test_a_dead_lettered_record_counts_as_finished(harness: f53.Harness, gated: GatedCommits) -> None:
    gated.open.set()
    harness.produce(0, 5)
    await harness.start(DeadLetteringHandler(fail=frozenset({"p0-002"})), parallel=True)

    await f53._until(lambda: harness.committed() == 5)


async def test_on_the_default_path_a_partition_commits_while_another_partitions_commit_is_held(
    harness: f53.Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    p0_gate = asyncio.Event()
    real = fake_kafka.FakeAIOKafkaConsumer.commit

    async def commit(self: fake_kafka.FakeAIOKafkaConsumer, offsets: dict[TopicPartition, int] | None = None) -> None:
        if P0 in (offsets or {}):
            await p0_gate.wait()
        await real(self, offsets)

    monkeypatch.setattr(fake_kafka.FakeAIOKafkaConsumer, "commit", commit)
    harness.produce(0, 3)
    harness.produce(1, 3)
    await harness.start(f53.Handler())

    await f53._until(lambda: harness.committed(1) == 3)
    assert harness.committed(0) == 0
    p0_gate.set()
    await f53._until(lambda: harness.committed(0) == 3)


# -- against the consumer object ------------------------------------------------------------------------------------
class BrokerCommits:
    """The aiokafka consumer's commit, recorded on entry and gated, plus the order of commit and stop."""

    def __init__(self) -> None:
        self.open = asyncio.Event()
        self.calls: list[dict[TopicPartition, int]] = []
        self.events: list[str] = []
        self.fail_next: list[Exception] = []

    async def commit(self, offsets: dict[TopicPartition, int]) -> None:
        self.calls.append(dict(offsets))
        await self.open.wait()
        if self.fail_next:
            raise self.fail_next.pop(0)
        self.events.append(f"commit {offsets[P0]}")

    async def stop(self) -> None:
        self.events.append("stop")


def _consumer() -> tuple[IndexingKafkaConsumer, BrokerCommits]:
    consumer = IndexingKafkaConsumer(
        logging.getLogger("test_kafka_commit_coalesce"),
        KafkaConsumerConfig(
            topics=["record-events"], client_id="c", group_id="g", auto_offset_reset="earliest",
            enable_auto_commit=False, bootstrap_servers=["kafka:9092"],
        ),
        fair_scheduler_config=_fair_config(),
    )
    commits = BrokerCommits()
    consumer.consumer = MagicMock()
    consumer.consumer.commit = commits.commit
    consumer.consumer.stop = AsyncMock(side_effect=commits.stop)
    consumer.running = True
    return consumer, commits


def _resolve(consumer: IndexingKafkaConsumer, offset: int, epoch: int | None = None) -> asyncio.Task[bool]:
    resolve = consumer._IndexingKafkaConsumer__resolve_offset  # type: ignore[attr-defined]
    return asyncio.ensure_future(resolve(_InFlightOffset(P0, offset, epoch), done=True))


async def _one_in_flight_one_waiting(consumer: IndexingKafkaConsumer, commits: BrokerCommits) -> tuple[asyncio.Task[bool], asyncio.Task[bool]]:
    for offset in range(3):
        consumer._offset_tracker.track(P0, offset)  # type: ignore[union-attr]
    in_flight = _resolve(consumer, 0)
    await f53._until(lambda: commits.calls == [{P0: 1}])
    waiting = _resolve(consumer, 1)
    await asyncio.sleep(0.01)
    return in_flight, waiting


async def test_a_revocation_commits_what_was_asked_for_before_it_returns() -> None:
    consumer, commits = _consumer()
    await consumer._IndexingKafkaConsumer__enqueue_message(P0, _make_message(offset=2))  # type: ignore[attr-defined]
    in_flight, waiting = await _one_in_flight_one_waiting(consumer, commits)

    revoking = asyncio.ensure_future(consumer._on_partitions_revoked([P0]))
    await asyncio.sleep(0.01)
    assert not revoking.done()
    assert consumer._scheduler.pending_count == 0  # purged before the wait: no dispatch can take a revoked record
    commits.open.set()
    await revoking
    assert commits.events == ["commit 1", "commit 2"]
    assert await in_flight is True and await waiting is True

    # A finish after the revocation commits nothing (its claim was lost with the partition).
    assert await _resolve(consumer, 2) is False
    assert commits.calls == [{P0: 1}, {P0: 2}]


async def test_what_still_waits_after_the_revocation_bound_is_dropped_never_sent_later() -> None:
    consumer, commits = _consumer()
    _in_flight, waiting = await _one_in_flight_one_waiting(consumer, commits)
    consumer._committer._timeout = SHORT

    await consumer._on_partitions_revoked([P0])  # the gate never opens within the bound

    assert await waiting is False  # dropped: index.end notes claim_lost
    # The partition comes back; the next poll clears the revocation; the new assignment finishes offset 10.
    await consumer._on_partitions_assigned([P0])
    consumer._revoked_since_poll.clear()
    consumer._offset_tracker.track(P0, 10)  # type: ignore[union-attr]
    new_assignment = _resolve(consumer, 10, epoch=consumer._partition_epoch[P0])
    await asyncio.sleep(0.01)
    commits.open.set()
    assert await new_assignment is True
    assert commits.calls == [{P0: 1}, {P0: 11}]  # the old assignment's waiting 2 was never sent


async def test_stop_commits_what_was_asked_for_before_it_stops_the_consumer() -> None:
    consumer, commits = _consumer()
    in_flight, waiting = await _one_in_flight_one_waiting(consumer, commits)

    stopping = asyncio.ensure_future(consumer.stop())
    await asyncio.sleep(0.05)
    assert "stop" not in commits.events
    commits.open.set()
    await stopping
    assert commits.events == ["commit 1", "commit 2", "stop"]
    assert await in_flight is True and await waiting is True


async def test_stop_mid_commit_hangs_nothing_and_commits_nothing_more() -> None:
    consumer, commits = _consumer()
    in_flight, waiting = await _one_in_flight_one_waiting(consumer, commits)
    consumer._committer._timeout = SHORT

    await consumer.stop()  # the gate never opens within the bound

    assert commits.events == ["stop"]
    assert await waiting is False  # dropped: nothing waits on a stopped consumer
    commits.open.set()  # the commit already in flight was never cancelled; it answers late
    assert await in_flight is True
    assert commits.calls == [{P0: 1}]


async def test_a_sender_cancelled_mid_commit_does_not_stall_the_read_phase() -> None:
    consumer, commits = _consumer()
    tracker = consumer._offset_tracker
    for offset in range(2):
        tracker.track(P0, offset)  # type: ignore[union-attr]
    commit_resolved = consumer._IndexingKafkaConsumer__commit_resolved  # type: ignore[attr-defined]
    settling = asyncio.ensure_future(commit_resolved([(P0, 0)]))
    await f53._until(lambda: commits.calls)

    consumer._committer._sending[P0].cancel()

    await settling  # warned, not raised, not hung
    commits.open.set()
    await commit_resolved([(P0, 1)])
    assert commits.events == ["commit 2"]


async def test_a_read_phase_settle_waits_for_the_partitions_commit_in_flight_instead_of_sending_beside_it() -> None:
    consumer, commits = _consumer()
    in_flight, waiting = await _one_in_flight_one_waiting(consumer, commits)
    settled = {(P0, 2): StreamMessage.model_validate(
        {"eventType": "newRecord", "payload": {"connectorId": "conn-1"}, "timestamp": 1})}
    resolve_settled = consumer._IndexingKafkaConsumer__resolve_settled  # type: ignore[attr-defined]
    settling = asyncio.ensure_future(resolve_settled([(P0, 2)], settled))  # connector off: settled at read
    await asyncio.sleep(0.01)
    assert commits.calls == [{P0: 1}]  # merged into the waiting commit, not a second request beside the first
    commits.open.set()
    await asyncio.gather(in_flight, waiting, settling)
    assert commits.calls == [{P0: 1}, {P0: 3}]


async def test_one_failed_commit_fails_each_record_merged_into_it_and_the_next_one_carries_them() -> None:
    consumer, commits = _consumer()
    in_flight, waiting = await _one_in_flight_one_waiting(consumer, commits)
    tail = _resolve(consumer, 2)  # merges into the same waiting commit as offset 1
    await asyncio.sleep(0.01)
    error = RuntimeError("refused")
    commits.fail_next.append(RuntimeError("first refused"))
    commits.fail_next.append(error)
    commits.open.set()

    with pytest.raises(RuntimeError, match="first refused"):
        await in_flight
    for waiter in (waiting, tail):
        with pytest.raises(RuntimeError, match="refused"):
            await waiter
    assert commits.calls == [{P0: 1}, {P0: 3}]

    consumer._offset_tracker.track(P0, 3)  # type: ignore[union-attr]
    assert await _resolve(consumer, 3) is True
    assert commits.events == ["commit 4"]


async def test_a_commit_asked_for_after_stop_is_dropped_not_an_attribute_error() -> None:
    consumer, commits = _consumer()
    consumer._offset_tracker.track(P0, 0)  # type: ignore[union-attr]
    consumer.consumer = None  # stopped
    resolve = consumer._IndexingKafkaConsumer__resolve_offset  # type: ignore[attr-defined]
    assert await resolve(_InFlightOffset(P0, 0), done=True) is True  # no consumer: nothing to settle against
    with pytest.raises(CommitDropped):
        await consumer._committer.commit({P0: 1})
    assert commits.calls == []


# -- the seam-off world (tests/support/record_consumer_world.py) -----------------------------------------------------
@pytest.mark.timeout(240)
async def test_coalescing_keeps_the_seam_off_worlds_committed_offsets_and_never_commits_backwards_or_out_of_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The seam-off golden was regenerated for this change: coalescing moves its commit calls and loop steps. Its
    # final offsets are pinned here as they were before, and every commit call along the way is checked.
    for key, value in world.GOLDEN_ENV.items():
        monkeypatch.setenv(key, value)
    messages = world.generate_messages(world.GOLDEN_SEED, world.GOLDEN_SIZES["kafka"])
    trace = await world.run_kafka(messages)

    totals = {0: 285, 1: 763, 2: 404, 3: 548}
    assert dict(trace.extra["committed"]) == totals
    last: dict[int, int] = {}
    for call in trace.settled:
        for partition, offset in call:
            assert 0 <= offset <= totals[partition]
            assert offset >= last.get(partition, 0), f"partition {partition} went back to {offset}"
            last[partition] = offset
