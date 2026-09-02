"""Integration-style tests for fair scheduling in ``IndexingKafkaConsumer``.

Covers the consumer-level behavior that the scheduler/offset-tracker unit
tests (``test_drr_scheduler.py``, ``test_partition_offset_tracker.py``)
can't see on their own: key extraction from a real Kafka ``ConsumerRecord``,
the commit watermark actually gating ``consumer.commit()``, a full buffer
stopping the partition instead of re-publishing, every wrapper exit path
settling its watermark claim, rebalance purge dropping buffered state for
revoked partitions, and the scheduler staying off entirely when fair
scheduling is disabled.
"""
from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiokafka import TopicPartition

from app.services.messaging.config import messaging_env
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
    _InFlightOffset,
    _ReadOutcome,
    _SchedulerRebalanceListener,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig


@pytest.fixture
def logger():
    return logging.getLogger("test_fair_scheduling_kafka")


@pytest.fixture
def plain_config():
    return KafkaConsumerConfig(
        topics=["record-events"],
        client_id="idx-consumer",
        group_id="idx-group",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["broker:9092"],
        ssl=False,
        sasl=None,
    )


def _fair_config(**overrides) -> FairSchedulerConfig:
    defaults = {
        "enabled": True,
        "key_fields": ("orgId", "connectorId"),
        "default_quantum": 1,
        "max_buffered_messages": 100,
        "max_per_entity_messages": 50,
        "max_dwell_seconds": 900.0,
    }
    defaults.update(overrides)
    return FairSchedulerConfig(**defaults)


def _make_message(
    topic: str = "record-events",
    partition: int = 0,
    offset: int = 0,
    org_id: str = "org-a",
    connector_id: str = "conn-1",
    record_id: str | None = None,
    tracking_id: str | None = None,
):
    payload: dict = {
        "recordId": record_id or f"rec-{offset}",
        "orgId": org_id,
        "connectorId": connector_id,
        "virtualRecordId": f"vr-{offset}",
        "extension": "txt",
        "mimeType": "text/plain",
    }
    if tracking_id is not None:
        payload["_retry_tracking_id"] = tracking_id
    envelope = {"eventType": "newRecord", "payload": payload, "requestId": f"req-{offset}"}
    msg = MagicMock()
    msg.topic = topic
    msg.partition = partition
    msg.offset = offset
    msg.value = json.dumps(envelope).encode()
    return msg


def _make_consumer(logger, plain_config, fair_scheduler_config=None, producer=None, retry_manager=None):
    return IndexingKafkaConsumer(
        logger,
        plain_config,
        retry_manager=retry_manager,
        producer=producer,
        fair_scheduler_config=fair_scheduler_config,
    )


class TestMultiOrgInterleaving:
    async def test_two_orgs_interleave_round_robin(self, logger, plain_config):
        consumer = _make_consumer(logger, plain_config, _fair_config())
        tp = TopicPartition("record-events", 0)

        offset = 0
        for _ in range(5):
            await consumer._IndexingKafkaConsumer__enqueue_message(
                tp, _make_message(offset=offset, org_id="org-a")
            )
            offset += 1
        for _ in range(5):
            await consumer._IndexingKafkaConsumer__enqueue_message(
                tp, _make_message(offset=offset, org_id="org-b")
            )
            offset += 1

        assert consumer._scheduler.pending_count == 10

        order = []
        while not consumer._scheduler.is_empty:
            key, _item = consumer._scheduler.dequeue()
            order.append(key)

        # Quantum 1 for both keys: strict alternation, not "all of org-a's
        # backlog first" -- the exact failure mode this feature fixes.
        assert order == [("org-a", "conn-1"), ("org-b", "conn-1")] * 5


class TestWatermarkCommitOrdering:
    async def test_commit_waits_for_lower_offset_before_advancing(self, logger, plain_config):
        consumer = _make_consumer(logger, plain_config, _fair_config())
        consumer.consumer = MagicMock()
        consumer.consumer.commit = AsyncMock()
        tp = TopicPartition("record-events", 0)

        for offset in range(3):
            consumer._offset_tracker.track(tp, offset)

        # DRR can dispatch/finish offset 1 before offset 0. The watermark
        # this produces still reflects offset 0 as not-yet-done (0, not 2) --
        # committing past it here would lose it on a crash before it is
        # processed.
        await consumer._IndexingKafkaConsumer__resolve_offset(
            _InFlightOffset(tp, 1), done=True
        )
        consumer.consumer.commit.assert_awaited_once_with({tp: 0})

        # Offset 2 finishing next still can't advance past the outstanding
        # offset 0 -- no further commit.
        consumer.consumer.commit.reset_mock()
        await consumer._IndexingKafkaConsumer__resolve_offset(
            _InFlightOffset(tp, 2), done=True
        )
        consumer.consumer.commit.assert_not_awaited()

        # Offset 0 finally resolves: watermark jumps straight to 3 (every
        # tracked offset 0-2 is now done), not just to 1.
        await consumer._IndexingKafkaConsumer__resolve_offset(
            _InFlightOffset(tp, 0), done=True
        )
        consumer.consumer.commit.assert_awaited_once_with({tp: 3})


class TestFullBufferStopsReadingInsteadOfRepublishing:
    """A full buffer must never bounce the message back to the topic tail.

    Re-publishing had no retry budget, destroyed ordering, and (on Redis)
    grew the stream past its MAXLEN trim point. The message stays unread and
    the partition is rewound instead.
    """

    async def test_entity_full_parks_the_message_and_never_publishes(
        self, logger, plain_config
    ):
        producer = AsyncMock()
        retry_manager = MagicMock()
        retry_manager.increment_and_check = AsyncMock()
        consumer = _make_consumer(
            logger,
            plain_config,
            _fair_config(max_per_entity_messages=1),
            producer=producer,
            retry_manager=retry_manager,
        )
        consumer.consumer = MagicMock()
        consumer.consumer.commit = AsyncMock()
        tp = TopicPartition("record-events", 0)

        outcome, blocked = await consumer._IndexingKafkaConsumer__enqueue_message(
            tp, _make_message(offset=0, org_id="org-a")
        )
        assert (outcome, blocked) == (_ReadOutcome.BUFFERED, None)

        outcome, blocked = await consumer._IndexingKafkaConsumer__enqueue_message(
            tp, _make_message(offset=1, org_id="org-a")
        )

        # The key is capped but the buffer as a whole is not, so the message
        # is held in memory and reading continues -- stopping here would cap
        # read-ahead at one key's share of the buffer.
        assert outcome == _ReadOutcome.PARKED
        assert blocked == ("org-a", "conn-1")
        assert len(consumer._deferred_messages) == 1
        assert consumer._scheduler.pending_count == 1
        producer.send_event.assert_not_awaited()
        retry_manager.increment_and_check.assert_not_awaited()
        # Nothing is committed: offset 1 was never processed, and offset 0 is
        # still buffered.
        consumer.consumer.commit.assert_not_awaited()

    async def test_read_phase_seeks_back_only_when_the_whole_buffer_is_full(
        self, logger, plain_config
    ):
        consumer = _make_consumer(
            logger,
            plain_config,
            _fair_config(max_per_entity_messages=1, max_buffered_messages=2),
        )
        tp = TopicPartition("record-events", 0)
        consumer.consumer = MagicMock()
        consumer.consumer.commit = AsyncMock()
        consumer.consumer.getmany = AsyncMock(
            return_value={
                tp: [
                    _make_message(offset=0, org_id="org-a"),
                    _make_message(offset=1, org_id="org-a"),
                    _make_message(offset=2, org_id="org-a"),
                ]
            }
        )
        consumer.running = True

        await consumer._IndexingKafkaConsumer__read_phase()

        # Offset 0 buffered, offset 1 parked -- that is the whole budget, so
        # offset 2 has nowhere to go and the partition rewinds to it.
        consumer.consumer.seek.assert_called_once_with(tp, 2)

    async def test_read_phase_seeks_back_every_partition_it_cut_short(
        self, logger, plain_config
    ):
        """Returning out of the whole batch would abandon the messages
        getmany() already handed us for the other partitions: their fetch
        position has advanced and nothing would re-read them."""
        consumer = _make_consumer(
            logger, plain_config, _fair_config(max_buffered_messages=1)
        )
        tp0 = TopicPartition("record-events", 0)
        tp1 = TopicPartition("record-events", 1)
        consumer.consumer = MagicMock()
        consumer.consumer.commit = AsyncMock()
        consumer.consumer.getmany = AsyncMock(
            return_value={
                tp0: [
                    _make_message(partition=0, offset=0, org_id="org-a"),
                    _make_message(partition=0, offset=1, org_id="org-a"),
                ],
                tp1: [_make_message(partition=1, offset=7, org_id="org-b")],
            }
        )
        consumer.running = True

        await consumer._IndexingKafkaConsumer__read_phase()

        seeks = {call.args for call in consumer.consumer.seek.call_args_list}
        assert (tp0, 1) in seeks
        assert (tp1, 7) in seeks


class TestReadAheadAndPolling:
    async def test_reads_ahead_of_dispatch_capacity(self, logger, plain_config):
        """The buffer must be filled beyond what can be dispatched right
        now. Bounding reads by pipeline capacity (as the FIFO path does)
        keeps the buffer one message deep, and DRR has nothing to
        interleave -- fair scheduling silently degenerates to FIFO."""
        consumer = _make_consumer(
            logger, plain_config, _fair_config(max_buffered_messages=100)
        )
        tp = TopicPartition("record-events", 0)
        consumer.consumer = MagicMock()
        consumer.consumer.commit = AsyncMock()
        consumer.consumer.getmany = AsyncMock(return_value={})
        consumer.running = True

        await consumer._IndexingKafkaConsumer__read_phase()

        _args, kwargs = consumer.consumer.getmany.call_args
        assert kwargs["max_records"] == messaging_env.message_batch_size_indexing

    async def test_still_polls_when_the_buffer_is_full(self, logger, plain_config):
        """getmany() is what resets max_poll_interval_ms. Skipping it while
        the buffer drains would have the group evict this consumer."""
        consumer = _make_consumer(
            logger, plain_config, _fair_config(max_buffered_messages=1)
        )
        tp = TopicPartition("record-events", 0)
        consumer.consumer = MagicMock()
        consumer.consumer.commit = AsyncMock()
        consumer.consumer.getmany = AsyncMock(return_value={})
        consumer.running = True
        await consumer._IndexingKafkaConsumer__enqueue_message(
            tp, _make_message(offset=0, org_id="org-a")
        )
        assert consumer._scheduler.pending_count == 1  # buffer is now full
        consumer.consumer.getmany.reset_mock()

        await consumer._IndexingKafkaConsumer__read_phase()

        consumer.consumer.getmany.assert_awaited_once()
        _args, kwargs = consumer.consumer.getmany.call_args
        assert kwargs["max_records"] >= 1

    async def test_read_batch_is_capped_by_remaining_buffer_room(
        self, logger, plain_config
    ):
        consumer = _make_consumer(
            logger, plain_config, _fair_config(max_buffered_messages=3)
        )
        tp = TopicPartition("record-events", 0)
        consumer.consumer = MagicMock()
        consumer.consumer.commit = AsyncMock()
        consumer.consumer.getmany = AsyncMock(return_value={})
        consumer.running = True
        await consumer._IndexingKafkaConsumer__enqueue_message(
            tp, _make_message(offset=0, org_id="org-a")
        )

        await consumer._IndexingKafkaConsumer__read_phase()

        _args, kwargs = consumer.consumer.getmany.call_args
        assert kwargs["max_records"] == 2


class TestParallelPartitionDispatch:
    """Several records from one partition in flight at once.

    Without this the consumer holds a partition for a record's whole
    lifetime, so a single-partition ``record-events`` indexes exactly one
    record at a time regardless of MAX_CONCURRENT_INDEXING.
    """

    def _consumer(self, logger, plain_config, *, parallel, **overrides):
        consumer = _make_consumer(
            logger,
            plain_config,
            _fair_config(parallel_partitions=parallel, **overrides),
        )
        consumer.consumer = MagicMock()
        consumer.consumer.commit = AsyncMock()
        consumer.running = True
        # Started tasks are recorded, never run: nothing releases the
        # reservation, so what the dispatch loop admits stays visible.
        consumer._IndexingKafkaConsumer__start_processing_task = AsyncMock()
        return consumer

    async def _buffer(self, consumer, tp, records):
        for offset, record_id in enumerate(records):
            await consumer._IndexingKafkaConsumer__enqueue_message(
                tp, _make_message(offset=offset, record_id=record_id)
            )

    async def test_one_partition_dispatches_several_records_at_once(
        self, logger, plain_config
    ):
        consumer = self._consumer(logger, plain_config, parallel=True)
        tp = TopicPartition("record-events", 0)
        await self._buffer(consumer, tp, [f"rec-{i}" for i in range(5)])

        await consumer._IndexingKafkaConsumer__dispatch_phase()

        started = consumer._IndexingKafkaConsumer__start_processing_task
        assert started.await_count == 5
        assert len(consumer._in_flight_records) == 5

    async def test_disabled_still_serialises_on_the_partition(
        self, logger, plain_config
    ):
        """The default. One partition, one record in flight."""
        consumer = self._consumer(logger, plain_config, parallel=False)
        tp = TopicPartition("record-events", 0)
        await self._buffer(consumer, tp, [f"rec-{i}" for i in range(5)])

        await consumer._IndexingKafkaConsumer__dispatch_phase()

        assert consumer._IndexingKafkaConsumer__start_processing_task.await_count == 1
        assert consumer._in_flight_partitions == {tp}
        assert consumer._in_flight_records == set()

    async def test_two_events_for_one_record_never_run_together(
        self, logger, plain_config
    ):
        """The invariant that replaces per-partition serialisation.

        ``record_lease_wait_seconds`` documents the cluster-wide ``record:``
        lease as "only contended by duplicate in-flight deliveries of the
        same record", and the loser of that contention is dropped as
        already-handled. Dispatching a create and its update together would
        put two genuinely different events into that contention and silently
        discard one.
        """
        consumer = self._consumer(logger, plain_config, parallel=True)
        tp = TopicPartition("record-events", 0)
        await self._buffer(consumer, tp, ["rec-1", "rec-1"])

        await consumer._IndexingKafkaConsumer__dispatch_phase()

        assert consumer._IndexingKafkaConsumer__start_processing_task.await_count == 1
        assert consumer._in_flight_records == {"rec-1"}
        # Held back, not dropped.
        assert consumer._scheduler.pending_count == 1

    async def test_a_blocked_record_holds_its_own_key_queue(
        self, logger, plain_config
    ):
        """Documents the limit of parallel dispatch, so it is a known
        trade-off rather than a surprise.

        A key's virtual queue is FIFO, so a blocked head blocks that key --
        a second event for the same record parks everything behind it for
        that connector until the first finishes. That is the pre-Phase-4
        behaviour for that one connector, never worse, and it is rare: a
        sync emits distinct records, so adjacent same-record events mostly
        arise from re-queues. Other keys are unaffected.
        """
        consumer = self._consumer(logger, plain_config, parallel=True)
        tp = TopicPartition("record-events", 0)
        await self._buffer(consumer, tp, ["rec-1", "rec-1", "rec-2"])

        await consumer._IndexingKafkaConsumer__dispatch_phase()

        assert consumer._IndexingKafkaConsumer__start_processing_task.await_count == 1
        # rec-2 sits behind the duplicate in the same key's FIFO queue.
        assert consumer._scheduler.pending_count == 2

    async def test_another_key_is_not_blocked_by_it(self, logger, plain_config):
        consumer = self._consumer(logger, plain_config, parallel=True)
        tp = TopicPartition("record-events", 0)
        await self._buffer(consumer, tp, ["rec-1", "rec-1"])
        await consumer._IndexingKafkaConsumer__enqueue_message(
            tp,
            _make_message(offset=2, connector_id="other", record_id="rec-9"),
        )

        await consumer._IndexingKafkaConsumer__dispatch_phase()

        assert consumer._in_flight_records == {"rec-1", "rec-9"}

    async def test_the_held_back_event_dispatches_once_the_first_finishes(
        self, logger, plain_config
    ):
        consumer = self._consumer(logger, plain_config, parallel=True)
        tp = TopicPartition("record-events", 0)
        await self._buffer(consumer, tp, ["rec-1", "rec-1"])
        await consumer._IndexingKafkaConsumer__dispatch_phase()
        assert consumer._scheduler.pending_count == 1

        consumer._IndexingKafkaConsumer__finish_partition(
            _make_message(offset=0, record_id="rec-1"),
            retry_current=False,
            record_key="rec-1",
        )
        await consumer._IndexingKafkaConsumer__dispatch_phase()

        assert consumer._IndexingKafkaConsumer__start_processing_task.await_count == 2
        assert consumer._scheduler.is_empty

    async def test_events_without_a_record_id_never_block_each_other(
        self, logger, plain_config
    ):
        """Bulk deletes and collection drops are not per-record work, so they
        fall back to their unique stable message id."""
        consumer = self._consumer(logger, plain_config, parallel=True)
        tp = TopicPartition("record-events", 0)
        for offset in range(3):
            message = _make_message(offset=offset)
            envelope = json.loads(message.value)
            del envelope["payload"]["recordId"]
            message.value = json.dumps(envelope).encode()
            await consumer._IndexingKafkaConsumer__enqueue_message(tp, message)

        await consumer._IndexingKafkaConsumer__dispatch_phase()

        assert consumer._IndexingKafkaConsumer__start_processing_task.await_count == 3

    async def test_finish_releases_the_record_not_the_partition(
        self, logger, plain_config
    ):
        consumer = self._consumer(logger, plain_config, parallel=True)
        consumer._in_flight_records.add("rec-1")

        consumer._IndexingKafkaConsumer__finish_partition(
            _make_message(offset=0, record_id="rec-1"),
            retry_current=False,
            record_key="rec-1",
        )

        assert consumer._in_flight_records == set()
        # The partition was never paused for this message, so nothing resumes.
        consumer.consumer.resume.assert_not_called()

    async def test_out_of_order_completion_still_commits_contiguously(
        self, logger, plain_config
    ):
        """Parallel dispatch is only safe because of the commit watermark:
        offsets from one partition now finish out of order by design."""
        consumer = self._consumer(logger, plain_config, parallel=True)
        tp = TopicPartition("record-events", 0)
        await self._buffer(consumer, tp, ["rec-0", "rec-1", "rec-2"])
        await consumer._IndexingKafkaConsumer__dispatch_phase()

        tracker = consumer._offset_tracker
        await consumer._IndexingKafkaConsumer__resolve_offset(
            _InFlightOffset(tp, 2), done=True
        )
        await consumer._IndexingKafkaConsumer__resolve_offset(
            _InFlightOffset(tp, 1), done=True
        )
        # Offset 0 outstanding: nothing may commit past it.
        assert tracker.watermark_lag(tp) == 3

        await consumer._IndexingKafkaConsumer__resolve_offset(
            _InFlightOffset(tp, 0), done=True
        )
        assert tracker.watermark_lag(tp) == 0
        consumer.consumer.commit.assert_awaited_with({tp: 3})

    def test_flag_alone_does_not_enable_it_without_the_watermark(
        self, logger, plain_config
    ):
        """Out-of-order completion within a partition is only safe with the
        commit watermark, so the flag is inert when fair scheduling is off."""
        consumer = _make_consumer(logger, plain_config, fair_scheduler_config=None)
        assert not consumer._IndexingKafkaConsumer__parallel_dispatch()


class TestPerLaneBackpressure:
    """A key at its cap must stop *its* lane, not every lane.

    On Kafka a lane is a partition, so this is the difference between one
    busy connector pausing its own partition and it stalling reads for every
    other connector the consumer owns.
    """

    def _consumer(self, logger, plain_config, partitions=(0, 1), **overrides):
        consumer = _make_consumer(logger, plain_config, _fair_config(**overrides))
        consumer.consumer = MagicMock()
        consumer.consumer.commit = AsyncMock()
        # A lane is only paused when another lane could still be read, so the
        # assignment has to be real for these.
        assigned = {TopicPartition("record-events", p) for p in partitions}
        paused: set = set()
        consumer.consumer.assignment = MagicMock(return_value=assigned)
        consumer.consumer.paused = MagicMock(return_value=paused)
        # paused.update(tp) would iterate the namedtuple and store its
        # fields ('record-events', 0) rather than the TopicPartition itself.
        consumer.consumer.pause = MagicMock(
            side_effect=lambda *tps: paused.update(tps)
        )
        consumer.consumer.resume = MagicMock(
            side_effect=lambda *tps: paused.difference_update(tps)
        )
        consumer.running = True
        return consumer

    async def test_a_full_key_pauses_only_its_own_partition(
        self, logger, plain_config
    ):
        consumer = self._consumer(plain_config=plain_config, logger=logger,
                                  max_per_entity_messages=1)
        tp0 = TopicPartition("record-events", 0)
        tp1 = TopicPartition("record-events", 1)
        consumer.consumer.getmany = AsyncMock(
            return_value={
                tp0: [
                    _make_message(partition=0, offset=0, connector_id="busy"),
                    _make_message(partition=0, offset=1, connector_id="busy"),
                ],
                tp1: [_make_message(partition=1, offset=0, connector_id="quiet")],
            }
        )

        await consumer._IndexingKafkaConsumer__read_phase()

        paused = [call.args[0] for call in consumer.consumer.pause.call_args_list]
        assert tp0 in paused
        assert tp1 not in paused
        # The quiet lane's message was still buffered.
        assert consumer._scheduler.pending_count_for(("org-a", "quiet")) == 1

    async def test_paused_lane_is_recorded_against_the_blocking_key(
        self, logger, plain_config
    ):
        consumer = self._consumer(plain_config=plain_config, logger=logger,
                                  max_per_entity_messages=1)
        tp = TopicPartition("record-events", 0)
        other = TopicPartition("record-events", 1)
        consumer.consumer.getmany = AsyncMock(
            return_value={
                tp: [
                    _make_message(offset=0, connector_id="busy"),
                    _make_message(offset=1, connector_id="busy"),
                ],
                other: [_make_message(partition=1, offset=0, connector_id="quiet")],
            }
        )

        await consumer._IndexingKafkaConsumer__read_phase()

        # The other partition produced work, so it is a real alternative and
        # the blocked one is paused in its favour.
        assert consumer._lane_paused == {tp: ("org-a", "busy")}

    async def test_the_only_lane_is_never_paused(self, logger, plain_config):
        """Pausing the last readable lane just stops the consumer. With one
        partition the capped key's overflow is parked instead, so reading
        continues and other keys further down the topic are still reached."""
        consumer = self._consumer(
            plain_config=plain_config,
            logger=logger,
            partitions=(0,),
            max_per_entity_messages=1,
        )
        tp = TopicPartition("record-events", 0)
        consumer.consumer.getmany = AsyncMock(
            return_value={
                tp: [
                    _make_message(offset=0, connector_id="busy"),
                    _make_message(offset=1, connector_id="busy"),
                    _make_message(offset=2, connector_id="quiet"),
                ]
            }
        )

        await consumer._IndexingKafkaConsumer__read_phase()

        assert consumer._lane_paused == {}
        # Reading past the capped key is what reaches the quiet one.
        assert consumer._scheduler.pending_count_for(("org-a", "quiet")) == 1
        assert len(consumer._deferred_messages) == 1

    async def test_lane_resumes_once_its_key_drains_below_the_cap(
        self, logger, plain_config
    ):
        consumer = self._consumer(plain_config=plain_config, logger=logger,
                                  max_per_entity_messages=1)
        consumer._deferred_messages.clear()
        tp = TopicPartition("record-events", 0)
        other = TopicPartition("record-events", 1)
        consumer.consumer.getmany = AsyncMock(
            return_value={
                tp: [
                    _make_message(offset=0, connector_id="busy"),
                    _make_message(offset=1, connector_id="busy"),
                ],
                other: [_make_message(partition=1, offset=0, connector_id="quiet")],
            }
        )
        await consumer._IndexingKafkaConsumer__read_phase()
        assert tp in consumer._lane_paused

        # Still full: nothing resumes.
        consumer._IndexingKafkaConsumer__resume_drained_lanes()
        assert tp in consumer._lane_paused

        consumer._scheduler.dequeue()
        consumer._IndexingKafkaConsumer__resume_drained_lanes()

        assert consumer._lane_paused == {}
        consumer.consumer.resume.assert_called_with(tp)

    async def test_a_lane_with_a_message_in_flight_is_not_resumed_here(
        self, logger, plain_config
    ):
        """Ordering keeps a partition paused while one of its messages is in
        flight; __finish_partition owns that resume."""
        consumer = self._consumer(plain_config=plain_config, logger=logger,
                                  max_per_entity_messages=1)
        tp = TopicPartition("record-events", 0)
        consumer.consumer.getmany = AsyncMock(
            return_value={
                tp: [
                    _make_message(offset=0, connector_id="busy"),
                    _make_message(offset=1, connector_id="busy"),
                ]
            }
        )
        await consumer._IndexingKafkaConsumer__read_phase()
        consumer._scheduler.dequeue()
        with consumer._partition_lock:
            consumer._in_flight_partitions.add(tp)
        consumer.consumer.resume.reset_mock()

        consumer._IndexingKafkaConsumer__resume_drained_lanes()

        assert tp not in consumer._lane_paused
        consumer.consumer.resume.assert_not_called()

    def test_finish_partition_does_not_undo_a_lane_pause(
        self, logger, plain_config
    ):
        consumer = self._consumer(plain_config=plain_config, logger=logger)
        tp = TopicPartition("record-events", 0)
        consumer._lane_paused[tp] = ("org-a", "busy")
        consumer.consumer.resume.reset_mock()

        consumer._IndexingKafkaConsumer__finish_partition(
            _make_message(offset=5), retry_current=False
        )

        consumer.consumer.resume.assert_not_called()

    def test_global_backpressure_clear_does_not_undo_a_lane_pause(
        self, logger, plain_config
    ):
        """The buffer as a whole having room says nothing about whether the
        key that blocked this particular lane has drained."""
        consumer = self._consumer(plain_config=plain_config, logger=logger)
        tp_blocked = TopicPartition("record-events", 0)
        tp_free = TopicPartition("record-events", 1)
        consumer._lane_paused[tp_blocked] = ("org-a", "busy")
        consumer.consumer.assignment = MagicMock(
            return_value={tp_blocked, tp_free}
        )
        consumer.consumer.paused = MagicMock(return_value={tp_blocked, tp_free})

        consumer._IndexingKafkaConsumer__apply_backpressure()

        resumed = {
            tp
            for call in consumer.consumer.resume.call_args_list
            for tp in call.args
        }
        assert resumed == {tp_free}

    async def test_revocation_drops_lane_state(self, logger, plain_config):
        consumer = self._consumer(plain_config=plain_config, logger=logger)
        tp = TopicPartition("record-events", 0)
        consumer._lane_paused[tp] = ("org-a", "busy")

        await consumer._on_partitions_revoked([tp])

        assert consumer._lane_paused == {}


class TestBackpressureCountsParkedMessages:
    """Parked messages hold the same budget as buffered ones.

    Counting only the scheduler leaves the partitions unpaused once parking
    has taken the remaining room, and the read phase then polls, gets a
    message it cannot hold, and seeks it straight back -- every iteration.
    """

    def _consumer(self, logger, plain_config, **overrides):
        consumer = _make_consumer(logger, plain_config, _fair_config(**overrides))
        consumer.consumer = MagicMock()
        consumer.consumer.commit = AsyncMock()
        tp = TopicPartition("record-events", 0)
        consumer.consumer.assignment = MagicMock(return_value={tp})
        consumer.consumer.paused = MagicMock(return_value=set())
        consumer.running = True
        return consumer

    async def test_partitions_pause_when_parked_messages_fill_the_budget(
        self, logger, plain_config
    ):
        consumer = self._consumer(
            logger,
            plain_config,
            max_buffered_messages=2,
            max_per_entity_messages=1,
        )
        tp = TopicPartition("record-events", 0)
        for offset in range(2):
            await consumer._IndexingKafkaConsumer__enqueue_message(
                tp, _make_message(offset=offset, connector_id="busy")
            )
        # One buffered, one parked: the whole budget is held.
        assert consumer._scheduler.pending_count == 1
        assert len(consumer._deferred_messages) == 1

        consumer._IndexingKafkaConsumer__apply_backpressure()

        consumer.consumer.pause.assert_called()

    def test_partitions_stay_open_while_the_budget_has_room(
        self, logger, plain_config
    ):
        consumer = self._consumer(logger, plain_config, max_buffered_messages=100)

        consumer._IndexingKafkaConsumer__apply_backpressure()

        consumer.consumer.pause.assert_not_called()


class TestReadFailureFloorIsNotSweptAway:
    """The read-phase failure path rewinds the partition, so its offset comes
    back on its own. The dwell sweep must not commit past it in the meantime
    -- that would skip a message nothing ever processed."""

    async def test_a_rewound_read_failure_survives_the_sweep(
        self, logger, plain_config
    ):
        consumer = _make_consumer(
            logger, plain_config, _fair_config(max_dwell_seconds=0.0)
        )
        consumer.consumer = MagicMock()
        consumer.consumer.commit = AsyncMock()
        consumer.running = True
        tp = TopicPartition("record-events", 0)
        # An unparseable value makes __enqueue_message raise, which is the
        # path that resolves as redeliver and rewinds.
        broken = _make_message(offset=0)
        broken.value = b"{ not json"
        with patch.object(
            consumer,
            "_IndexingKafkaConsumer__enqueue_message",
            side_effect=RuntimeError("boom"),
        ):
            consumer.consumer.getmany = AsyncMock(return_value={tp: [broken]})
            await consumer._IndexingKafkaConsumer__read_phase()

        consumer.consumer.seek.assert_called_once_with(tp, 0)
        consumer.consumer.commit.reset_mock()
        consumer._last_dwell_sweep = 0.0

        await consumer._IndexingKafkaConsumer__sweep_stale_offsets()

        consumer.consumer.commit.assert_not_awaited()


class TestParkedBranchDoesNotAbandonTheRestOfTheBatch:
    """getmany() advances the fetch position over every record it returns.

    Parking one message and pausing the lane must therefore rewind past it,
    or the rest of that partition's batch is never enqueued, never tracked,
    and never re-read -- and with no watermark floor of their own those
    offsets get committed once the parked one resolves. Records dropped
    without indexing.
    """

    def _consumer(self, logger, plain_config):
        consumer = _make_consumer(
            logger, plain_config, _fair_config(max_per_entity_messages=1)
        )
        consumer.consumer = MagicMock()
        consumer.consumer.commit = AsyncMock()
        consumer.running = True
        assigned = {
            TopicPartition("record-events", 0),
            TopicPartition("record-events", 1),
        }
        paused: set = set()
        consumer.consumer.assignment = MagicMock(return_value=assigned)
        consumer.consumer.paused = MagicMock(return_value=paused)
        # paused.update(tp) would iterate the namedtuple and store its
        # fields ('record-events', 0) rather than the TopicPartition itself.
        consumer.consumer.pause = MagicMock(
            side_effect=lambda *tps: paused.update(tps)
        )
        return consumer

    async def test_rewinds_past_the_parked_message(self, logger, plain_config):
        consumer = self._consumer(logger, plain_config)
        tp = TopicPartition("record-events", 0)
        other = TopicPartition("record-events", 1)
        consumer.consumer.getmany = AsyncMock(
            return_value={
                tp: [
                    _make_message(offset=0, connector_id="busy"),
                    _make_message(offset=1, connector_id="busy"),
                    _make_message(offset=2, connector_id="busy"),
                    _make_message(offset=3, connector_id="busy"),
                ],
                other: [_make_message(partition=1, offset=0, connector_id="quiet")],
            }
        )

        await consumer._IndexingKafkaConsumer__read_phase()

        # Offset 1 was parked; 2 and 3 were fetched but never enqueued, so the
        # partition must rewind to 2.
        consumer.consumer.seek.assert_any_call(tp, 2)
        assert tp in consumer._lane_paused

    async def test_the_parked_message_is_not_re_read(self, logger, plain_config):
        """It is already held in memory, so rewinding onto it would
        duplicate it."""
        consumer = self._consumer(logger, plain_config)
        tp = TopicPartition("record-events", 0)
        other = TopicPartition("record-events", 1)
        consumer.consumer.getmany = AsyncMock(
            return_value={
                tp: [
                    _make_message(offset=0, connector_id="busy"),
                    _make_message(offset=1, connector_id="busy"),
                ],
                other: [_make_message(partition=1, offset=0, connector_id="quiet")],
            }
        )

        await consumer._IndexingKafkaConsumer__read_phase()

        assert len(consumer._deferred_messages) == 1
        seeks = [c.args for c in consumer.consumer.seek.call_args_list]
        assert (tp, 1) not in seeks, "must not rewind onto the parked message"
        assert (tp, 2) in seeks

    async def test_no_offset_between_the_parked_one_and_the_rewind_is_skipped(
        self, logger, plain_config
    ):
        """The watermark check that would have caught the original bug:
        nothing may commit past an offset that was never enqueued."""
        consumer = self._consumer(logger, plain_config)
        tp = TopicPartition("record-events", 0)
        other = TopicPartition("record-events", 1)
        consumer.consumer.getmany = AsyncMock(
            return_value={
                tp: [
                    _make_message(offset=0, connector_id="busy"),
                    _make_message(offset=1, connector_id="busy"),
                    _make_message(offset=2, connector_id="busy"),
                ],
                other: [_make_message(partition=1, offset=0, connector_id="quiet")],
            }
        )

        await consumer._IndexingKafkaConsumer__read_phase()

        tracker = consumer._offset_tracker
        # Resolve everything the consumer took responsibility for.
        await consumer._IndexingKafkaConsumer__resolve_offset(
            _InFlightOffset(tp, 0), done=True
        )
        await consumer._IndexingKafkaConsumer__resolve_offset(
            _InFlightOffset(tp, 1), done=True
        )
        committed = [
            call.args[0].get(tp)
            for call in consumer.consumer.commit.await_args_list
            if tp in call.args[0]
        ]
        assert all(w <= 2 for w in committed), (
            f"committed past offset 2, which was never enqueued: {committed}"
        )
        assert tracker.watermark_lag(tp) >= 0


class TestWatermarkClaimIsAlwaysSettled:
    """Regression suite for the pinned-watermark bug: an offset that reaches
    neither ``mark_done`` nor ``mark_redeliver`` stalls every later commit on
    its partition for the rest of the process's life."""

    def _consumer(self, logger, plain_config):
        consumer = _make_consumer(logger, plain_config, _fair_config())
        consumer.consumer = MagicMock()
        consumer.consumer.commit = AsyncMock()
        consumer.main_loop = None
        return consumer

    async def test_contended_record_lease_resolves_as_done(
        self, logger, plain_config
    ):
        """A contended lease means a duplicate delivery owns the record, so
        this delivery is finished with. Leaving it unresolved was the bug."""
        consumer = self._consumer(logger, plain_config)
        tp = TopicPartition("record-events", 0)
        tracker = consumer._offset_tracker
        for offset in (0, 1, 2):
            tracker.track(tp, offset)

        await consumer._IndexingKafkaConsumer__resolve_offset(
            _InFlightOffset(tp, 0), done=True
        )
        await consumer._IndexingKafkaConsumer__resolve_offset(
            _InFlightOffset(tp, 1), done=True
        )
        await consumer._IndexingKafkaConsumer__resolve_offset(
            _InFlightOffset(tp, 2), done=True
        )

        assert tracker.watermark_lag(tp) == 0
        consumer.consumer.commit.assert_awaited_with({tp: 3})

    async def test_shutdown_paths_resolve_as_redeliver_not_done(
        self, logger, plain_config
    ):
        consumer = self._consumer(logger, plain_config)
        tp = TopicPartition("record-events", 0)
        tracker = consumer._offset_tracker
        tracker.track(tp, 0)
        tracker.track(tp, 1)

        await consumer._IndexingKafkaConsumer__resolve_offset(
            _InFlightOffset(tp, 0), done=False
        )
        await consumer._IndexingKafkaConsumer__resolve_offset(
            _InFlightOffset(tp, 1), done=True
        )

        # Offset 0 must be redelivered, so nothing may commit past it.
        for call in consumer.consumer.commit.await_args_list:
            assert call.args[0][tp] <= 0

    async def test_resolution_is_idempotent(self, logger, plain_config):
        consumer = self._consumer(logger, plain_config)
        tp = TopicPartition("record-events", 0)
        consumer._offset_tracker.track(tp, 0)
        token = _InFlightOffset(tp, 0)

        await consumer._IndexingKafkaConsumer__resolve_offset(token, done=True)
        await consumer._IndexingKafkaConsumer__resolve_offset(token, done=True)

        assert consumer.consumer.commit.await_count == 1

    async def test_dwell_sweep_unpins_a_watermark_nobody_resolved(
        self, logger, plain_config
    ):
        """The escape hatch: if a dispatch path ever fails to settle its
        claim, the sweep force-commits past it rather than letting commits
        stop until a restart."""
        consumer = _make_consumer(
            logger, plain_config, _fair_config(max_dwell_seconds=0.0)
        )
        consumer.consumer = MagicMock()
        consumer.consumer.commit = AsyncMock()
        tp = TopicPartition("record-events", 0)
        tracker = consumer._offset_tracker
        tracker.track(tp, 0)
        tracker.track(tp, 1)
        # Both were handed to a worker; offset 0 is the one that never came
        # back. Only dispatched offsets arm the sweep -- a merely buffered
        # one has not been processed, so committing past it would skip it.
        tracker.mark_dispatched(tp, 0)
        tracker.mark_dispatched(tp, 1)
        await consumer._IndexingKafkaConsumer__resolve_offset(
            _InFlightOffset(tp, 1), done=True
        )
        assert tracker.watermark_lag(tp) > 0  # pinned by the unresolved 0

        consumer._last_dwell_sweep = 0.0
        await consumer._IndexingKafkaConsumer__sweep_stale_offsets()

        assert tracker.watermark_lag(tp) == 0
        consumer.consumer.commit.assert_awaited_with({tp: 2})

    async def test_dwell_sweep_leaves_healthy_offsets_alone(
        self, logger, plain_config
    ):
        consumer = _make_consumer(
            logger, plain_config, _fair_config(max_dwell_seconds=900.0)
        )
        consumer.consumer = MagicMock()
        consumer.consumer.commit = AsyncMock()
        tp = TopicPartition("record-events", 0)
        consumer._offset_tracker.track(tp, 0)

        consumer._last_dwell_sweep = 0.0
        await consumer._IndexingKafkaConsumer__sweep_stale_offsets()

        consumer.consumer.commit.assert_not_awaited()


class TestDwellNeverSkipsUnprocessedMessages:
    """Regression: a lane paused longer than the dwell budget used to make
    the sweep commit past a message that had been refused and seeked back --
    never processed, and on a crash after that commit, never redelivered."""

    async def test_parked_message_is_not_committed_past(
        self, logger, plain_config
    ):
        """A parked message has been read but never processed. Its offset
        floors the watermark, and the dwell sweep must not step over it."""
        consumer = _make_consumer(
            logger,
            plain_config,
            _fair_config(max_per_entity_messages=1, max_dwell_seconds=0.0),
        )
        consumer.consumer = MagicMock()
        consumer.consumer.commit = AsyncMock()
        consumer.running = True
        tp = TopicPartition("record-events", 0)
        consumer.consumer.getmany = AsyncMock(
            return_value={tp: [_make_message(offset=0), _make_message(offset=1)]}
        )

        await consumer._IndexingKafkaConsumer__read_phase()
        assert len(consumer._deferred_messages) == 1

        consumer._last_dwell_sweep = 0.0
        await consumer._IndexingKafkaConsumer__sweep_stale_offsets()

        committed = [
            call.args[0][tp] for call in consumer.consumer.commit.await_args_list
        ]
        assert all(watermark <= 1 for watermark in committed), (
            f"committed past the unprocessed offset 1: {committed}"
        )

    async def test_buffered_but_undispatched_message_is_not_committed_past(
        self, logger, plain_config
    ):
        consumer = _make_consumer(
            logger, plain_config, _fair_config(max_dwell_seconds=0.0)
        )
        consumer.consumer = MagicMock()
        consumer.consumer.commit = AsyncMock()
        tp = TopicPartition("record-events", 0)
        await consumer._IndexingKafkaConsumer__enqueue_message(
            tp, _make_message(offset=0)
        )
        assert consumer._scheduler.pending_count == 1

        consumer._last_dwell_sweep = 0.0
        await consumer._IndexingKafkaConsumer__sweep_stale_offsets()

        consumer.consumer.commit.assert_not_awaited()

    async def test_a_dispatched_message_that_never_resolves_still_escapes(
        self, logger, plain_config
    ):
        """The pin scenario the sweep exists for must still be covered."""
        consumer = _make_consumer(
            logger, plain_config, _fair_config(max_dwell_seconds=0.0)
        )
        consumer.consumer = MagicMock()
        consumer.consumer.commit = AsyncMock()
        consumer.running = True
        consumer._IndexingKafkaConsumer__start_processing_task = AsyncMock()
        tp = TopicPartition("record-events", 0)
        await consumer._IndexingKafkaConsumer__enqueue_message(
            tp, _make_message(offset=0)
        )
        await consumer._IndexingKafkaConsumer__dispatch_phase()

        consumer._last_dwell_sweep = 0.0
        await consumer._IndexingKafkaConsumer__sweep_stale_offsets()

        consumer.consumer.commit.assert_awaited_with({tp: 1})


class TestNoSeekWhileBuffered:
    def test_finish_partition_does_not_rewind_under_the_scheduler(
        self, logger, plain_config
    ):
        """Rewinding with a populated buffer re-reads every offset above the
        failed one -- all of them already buffered or dispatched."""
        consumer = _make_consumer(logger, plain_config, _fair_config())
        consumer.consumer = MagicMock()
        consumer.running = False

        consumer._IndexingKafkaConsumer__finish_partition(
            _make_message(offset=5), retry_current=True
        )

        consumer.consumer.seek.assert_not_called()

    def test_finish_partition_still_rewinds_in_fifo_mode(
        self, logger, plain_config
    ):
        consumer = _make_consumer(logger, plain_config, fair_scheduler_config=None)
        consumer.consumer = MagicMock()
        consumer.running = False

        consumer._IndexingKafkaConsumer__finish_partition(
            _make_message(offset=5), retry_current=True
        )

        consumer.consumer.seek.assert_called_once_with(
            TopicPartition("record-events", 0), 5
        )


class TestRebalancePurge:
    async def test_revoked_partition_is_dropped_from_scheduler_and_tracker(
        self, logger, plain_config
    ):
        consumer = _make_consumer(logger, plain_config, _fair_config())
        tp1 = TopicPartition("record-events", 0)
        tp2 = TopicPartition("record-events", 1)

        await consumer._IndexingKafkaConsumer__enqueue_message(
            tp1, _make_message(topic="record-events", partition=0, offset=0, org_id="org-a")
        )
        await consumer._IndexingKafkaConsumer__enqueue_message(
            tp1, _make_message(topic="record-events", partition=0, offset=1, org_id="org-a")
        )
        await consumer._IndexingKafkaConsumer__enqueue_message(
            tp2, _make_message(topic="record-events", partition=1, offset=0, org_id="org-b")
        )
        assert consumer._scheduler.pending_count == 3

        await consumer._on_partitions_revoked([tp1])

        assert consumer._scheduler.pending_count == 1
        remaining_key, _item = consumer._scheduler.dequeue()
        assert remaining_key == ("org-b", "conn-1")

        # tp1's tracker state was dropped too: tracking a fresh offset on it
        # behaves exactly as if it had never been seen (see
        # test_partition_offset_tracker.py::TestRevoke).
        consumer._offset_tracker.track(tp1, 100)
        assert consumer._offset_tracker.mark_done(tp1, 100) == 101


class TestDisabledIsCurrentFifo:
    def test_no_fair_scheduler_config_disables_scheduler(self, logger, plain_config):
        consumer = IndexingKafkaConsumer(logger, plain_config, retry_manager=None, producer=None)
        assert consumer.fair_scheduler_config.enabled is False
        assert consumer._scheduler is None
        assert consumer._offset_tracker is None

    def test_explicitly_disabled_config_disables_scheduler(self, logger, plain_config):
        consumer = _make_consumer(logger, plain_config, _fair_config(enabled=False))
        assert consumer._scheduler is None
        assert consumer._offset_tracker is None


class TestInitializeSubscription:
    async def test_disabled_uses_positional_topics_no_listener(self, logger, plain_config):
        consumer = _make_consumer(logger, plain_config, _fair_config(enabled=False))
        mock_aio = AsyncMock()
        mock_aio.start = AsyncMock()

        with patch(
            "app.services.messaging.kafka.consumer.indexing_consumer.AIOKafkaConsumer",
            return_value=mock_aio,
        ) as mock_cls:
            await consumer.initialize()

        assert mock_cls.call_args.args == tuple(plain_config.topics)
        mock_aio.subscribe.assert_not_called()
        consumer._IndexingKafkaConsumer__stop_worker_thread()

    async def test_enabled_subscribes_with_rebalance_listener(self, logger, plain_config):
        consumer = _make_consumer(logger, plain_config, _fair_config())
        mock_aio = AsyncMock()
        mock_aio.start = AsyncMock()
        mock_aio.subscribe = MagicMock()

        with patch(
            "app.services.messaging.kafka.consumer.indexing_consumer.AIOKafkaConsumer",
            return_value=mock_aio,
        ) as mock_cls:
            await consumer.initialize()

        assert mock_cls.call_args.args == ()
        mock_aio.subscribe.assert_called_once()
        _, kwargs = mock_aio.subscribe.call_args
        assert kwargs["topics"] == plain_config.topics
        assert isinstance(kwargs["listener"], _SchedulerRebalanceListener)
        consumer._IndexingKafkaConsumer__stop_worker_thread()
