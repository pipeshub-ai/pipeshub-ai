"""The Kafka indexing consumer hands back vector-writing events in the FIFO loop, at dispatch, and in the resolve step.

The real filter and verdict run; the broker is a fake. Nothing is downloaded: the handler start is a recorded stub.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiokafka import TopicPartition

from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
    _ReadOutcome,
)
from app.services.messaging.precision_check import (
    HANDBACKS_KEY,
    PrecisionFilter,
    PrecisionVerdict,
    Verdict,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig

log = logging.getLogger("precision-kafka")
TOPIC = "record-events"


def _config() -> KafkaConsumerConfig:
    return KafkaConsumerConfig(
        topics=[TOPIC], client_id="c", group_id="g", auto_offset_reset="earliest",
        enable_auto_commit=False, bootstrap_servers=["b:9092"], ssl=False, sasl=None,
    )


def _record(offset: int, event: str = "newRecord", partition: int = 0, **extra):  # noqa: ANN202
    payload = {"recordId": f"rec-{offset}", "orgId": "org-a", "connectorId": "c1", "virtualRecordId": f"v{offset}",
               "extension": "txt", "mimeType": "text/plain", **extra}
    msg = MagicMock()
    msg.topic, msg.partition, msg.offset = TOPIC, partition, offset
    msg.value = json.dumps({"eventType": event, "payload": payload, "requestId": f"r{offset}"}).encode()
    return msg


def _verdict(mismatch: bool) -> PrecisionVerdict:
    verdict = PrecisionVerdict(fetch_states=AsyncMock(), indexes=AsyncMock(), logger=log)
    verdict._verdict = Verdict(mismatch, ["x"] if mismatch else [])
    return verdict


def _consumer(verdict: PrecisionVerdict, *, scheduler: bool):  # noqa: ANN202
    fair = FairSchedulerConfig(
        enabled=scheduler, key_fields=("orgId", "connectorId"), default_quantum=1, max_buffered_messages=100,
        max_per_entity_messages=50, max_dwell_seconds=900.0,
    )
    consumer = IndexingKafkaConsumer(log, _config(), fair_scheduler_config=fair, precision_filter=PrecisionFilter(verdict, log))
    consumer.consumer = MagicMock()
    consumer.consumer.commit = AsyncMock()
    consumer.consumer.assignment.return_value = set()
    consumer.consumer.paused.return_value = set()
    consumer.running = True
    consumer._requeue_message = AsyncMock()
    consumer._clear_retry_tracking = AsyncMock()
    consumer._IndexingKafkaConsumer__start_processing_task = AsyncMock()
    return consumer


class TestDispatchRecheck:
    @pytest.mark.asyncio
    async def test_buffered_messages_are_rechecked_at_dispatch(self) -> None:
        verdict = _verdict(False)
        consumer = _consumer(verdict, scheduler=True)
        tp = TopicPartition(TOPIC, 0)
        for offset in range(3):
            await consumer._IndexingKafkaConsumer__enqueue_message(tp, _record(offset, **{HANDBACKS_KEY: 1}))
        assert consumer._scheduler.pending_count == 3
        verdict._verdict = Verdict(True, ["flip"])  # the verdict turns after the buffer filled

        await consumer._IndexingKafkaConsumer__dispatch_phase()

        consumer._IndexingKafkaConsumer__start_processing_task.assert_not_awaited()
        assert consumer._requeue_message.await_count == 3
        for call in consumer._requeue_message.await_args_list:
            assert 480 <= call.kwargs["delay_s"] <= 720 and call.kwargs["retry_count"] == 0
            assert call.kwargs["extra_payload"] == {HANDBACKS_KEY: 2}
        assert consumer._scheduler.pending_count == 0 and consumer._in_flight_partitions == set()
        consumer.consumer.commit.assert_awaited()  # the offsets were resolved: the watermark passed them
        assert consumer.consumer.commit.await_args.args[0] == {tp: 3}

    @pytest.mark.asyncio
    async def test_a_match_verdict_dispatches_as_before(self) -> None:
        consumer = _consumer(_verdict(False), scheduler=True)
        tp = TopicPartition(TOPIC, 0)
        await consumer._IndexingKafkaConsumer__enqueue_message(tp, _record(0))
        await consumer._IndexingKafkaConsumer__dispatch_phase()
        consumer._IndexingKafkaConsumer__start_processing_task.assert_awaited_once()
        consumer._requeue_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_publish_that_fails_keeps_the_message_buffered(self) -> None:
        verdict = _verdict(False)
        consumer = _consumer(verdict, scheduler=True)
        tp = TopicPartition(TOPIC, 0)
        await consumer._IndexingKafkaConsumer__enqueue_message(tp, _record(0))
        verdict._verdict = Verdict(True, ["flip"])
        consumer._requeue_message = AsyncMock(side_effect=RuntimeError("broker down"))
        await consumer._IndexingKafkaConsumer__dispatch_phase()
        consumer._IndexingKafkaConsumer__start_processing_task.assert_not_awaited()
        consumer.consumer.commit.assert_not_awaited()
        assert consumer._scheduler.pending_count + len(consumer._deferred_messages) == 1  # not lost, not committed

    @pytest.mark.asyncio
    async def test_deletes_in_the_buffer_still_dispatch_under_a_mismatch(self) -> None:
        verdict = _verdict(False)
        consumer = _consumer(verdict, scheduler=True)
        tp = TopicPartition(TOPIC, 0)
        await consumer._IndexingKafkaConsumer__enqueue_message(tp, _record(0, event="deleteRecord"))
        verdict._verdict = Verdict(True, ["flip"])
        await consumer._IndexingKafkaConsumer__dispatch_phase()
        consumer._IndexingKafkaConsumer__start_processing_task.assert_awaited_once()
        consumer._requeue_message.assert_not_awaited()


class TestFifoLoop:
    async def _run(self, consumer, batch) -> None:
        calls = 0

        async def getmany(**_k):  # noqa: ANN202
            nonlocal calls
            calls += 1
            if calls == 1:
                return batch
            consumer.running = False
            return {}

        consumer.consumer.getmany = getmany
        await consumer._IndexingKafkaConsumer__consume_loop()

    @pytest.mark.asyncio
    async def test_kafka_fifo_hands_back_a_vector_writing_event_and_lets_a_delete_through(self) -> None:
        consumer = _consumer(_verdict(True), scheduler=False)
        assert consumer._scheduler is None
        tp = TopicPartition(TOPIC, 0)
        new, delete = _record(0), _record(1, event="deleteRecord")
        await self._run(consumer, {tp: [new, delete]})
        consumer._requeue_message.assert_awaited_once()
        assert consumer._requeue_message.await_args.kwargs["extra_payload"] == {HANDBACKS_KEY: 1}
        started = consumer._IndexingKafkaConsumer__start_processing_task
        started.assert_awaited_once()
        assert started.await_args.args[0] is delete  # only the delete reached a handler
        consumer.consumer.commit.assert_awaited_once_with({tp: 1})  # the hand-back's offset is resolved

    @pytest.mark.asyncio
    async def test_kafka_fifo_a_failed_publish_seeks_back_and_commits_nothing(self) -> None:
        consumer = _consumer(_verdict(True), scheduler=False)
        consumer._requeue_message = AsyncMock(side_effect=RuntimeError("down"))
        tp = TopicPartition(TOPIC, 0)
        await self._run(consumer, {tp: [_record(0)]})
        consumer._IndexingKafkaConsumer__start_processing_task.assert_not_awaited()
        consumer.consumer.commit.assert_not_awaited()
        consumer.consumer.seek.assert_called_once_with(tp, 0)
        assert tp not in consumer._in_flight_partitions

    @pytest.mark.asyncio
    async def test_kafka_fifo_without_a_mismatch_processes_normally(self) -> None:
        consumer = _consumer(_verdict(False), scheduler=False)
        tp = TopicPartition(TOPIC, 0)
        await self._run(consumer, {tp: [_record(0)]})
        consumer._requeue_message.assert_not_awaited()
        consumer._IndexingKafkaConsumer__start_processing_task.assert_awaited_once()


class TestResolveStep:
    @pytest.mark.asyncio
    async def test_a_seek_back_does_not_duplicate_hand_backs(self) -> None:
        """A position the read loop never reached is not published at filter time; it is published once when read."""
        consumer = _consumer(_verdict(True), scheduler=True)
        tp = TopicPartition(TOPIC, 0)
        batch = [_record(0), _record(1, event="deleteRecord"), _record(2)]
        outcomes = iter([(_ReadOutcome.STOP_PARTITION, None)])

        async def stop_at_the_delete(*_a, **_k):  # noqa: ANN202
            return next(outcomes)

        consumer._IndexingKafkaConsumer__enqueue_message = stop_at_the_delete
        consumer.consumer.getmany = AsyncMock(return_value={tp: batch})
        consumer._IndexingKafkaConsumer__pause_lane = MagicMock()
        await consumer._IndexingKafkaConsumer__read_phase()

        assert consumer._requeue_message.await_count == 1  # offset 0 only; offset 2 was behind the stop
        consumer.consumer.seek.assert_called_with(tp, 1)

        # the next pass reads 1..2 again: offset 2 is published now, once
        consumer._IndexingKafkaConsumer__enqueue_message = AsyncMock(return_value=(_ReadOutcome.BUFFERED, None))
        consumer.consumer.getmany = AsyncMock(return_value={tp: batch[1:]})
        await consumer._IndexingKafkaConsumer__read_phase()
        assert consumer._requeue_message.await_count == 2
        offsets = [c.args[1].payload["recordId"] for c in consumer._requeue_message.await_args_list]
        assert offsets == ["rec-0", "rec-2"]

    @pytest.mark.asyncio
    async def test_a_failed_publish_in_the_resolve_step_reads_the_message_again(self) -> None:
        consumer = _consumer(_verdict(True), scheduler=True)
        consumer._requeue_message = AsyncMock(side_effect=RuntimeError("down"))
        tp = TopicPartition(TOPIC, 0)
        consumer.consumer.getmany = AsyncMock(return_value={tp: [_record(0)]})
        await consumer._IndexingKafkaConsumer__read_phase()
        consumer.consumer.seek.assert_called_with(tp, 0)
        consumer.consumer.commit.assert_not_awaited()


class TestHandBackOffsets:
    """A hand-back that cannot be published must keep the commit watermark below its offset."""

    @staticmethod
    def _fail_on(consumer, offsets: set[int]) -> None:
        async def requeue(_topic, message, *_a, **_k):  # noqa: ANN202
            if int(message.payload["recordId"].split("-")[1]) in offsets:
                raise RuntimeError("broker down")

        consumer._requeue_message = AsyncMock(side_effect=requeue)

    @pytest.mark.asyncio
    async def test_a_failed_hand_back_floors_the_watermark_below_it(self) -> None:
        consumer = _consumer(_verdict(True), scheduler=True)
        tp = TopicPartition(TOPIC, 0)
        self._fail_on(consumer, {3})
        consumer.consumer.getmany = AsyncMock(return_value={tp: [_record(3)]})
        await consumer._IndexingKafkaConsumer__read_phase()
        consumer.consumer.seek.assert_called_with(tp, 3)

        # later offsets of the partition finish before the rewound read comes back to 3
        tracker = consumer._offset_tracker
        tracker.track(tp, 4)
        assert tracker.mark_done(tp, 4) is None  # not 5: offset 3 still has to be read again
        consumer.consumer.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_two_failures_on_a_partition_rewind_it_once_to_the_lowest(self) -> None:
        consumer = _consumer(_verdict(True), scheduler=True)
        tp = TopicPartition(TOPIC, 0)
        self._fail_on(consumer, {1, 2})
        consumer.consumer.getmany = AsyncMock(return_value={tp: [_record(0), _record(1), _record(2)]})
        await consumer._IndexingKafkaConsumer__read_phase()

        assert [c.args for c in consumer.consumer.seek.call_args_list] == [(tp, 1)]
        published = [c.args[1].payload["recordId"] for c in consumer._requeue_message.await_args_list]
        assert published == ["rec-0", "rec-1"]  # nothing is published behind the failure: it is read again
        assert consumer.consumer.commit.await_args.args[0] == {tp: 1}  # only offset 0 is committed past

    @pytest.mark.asyncio
    async def test_a_failure_on_one_partition_does_not_hold_another(self) -> None:
        consumer = _consumer(_verdict(True), scheduler=True)
        a, b = TopicPartition(TOPIC, 0), TopicPartition(TOPIC, 1)
        self._fail_on(consumer, {5})
        consumer.consumer.getmany = AsyncMock(return_value={a: [_record(5)], b: [_record(7, partition=1)]})
        await consumer._IndexingKafkaConsumer__read_phase()
        assert [c.args for c in consumer.consumer.seek.call_args_list] == [(a, 5)]
        assert consumer.consumer.commit.await_args.args[0] == {b: 8}

    @pytest.mark.asyncio
    async def test_a_rebalance_during_the_publish_commits_nothing_for_the_partition(self) -> None:
        consumer = _consumer(_verdict(True), scheduler=True)
        tp = TopicPartition(TOPIC, 0)

        async def revoked_and_assigned_again(*_a, **_k) -> None:
            await consumer._on_partitions_revoked([tp])
            consumer._revoked_since_poll.clear()  # the next poll has already cleared the flag

        consumer._requeue_message = AsyncMock(side_effect=revoked_and_assigned_again)
        consumer.consumer.getmany = AsyncMock(return_value={tp: [_record(0), _record(1)]})
        await consumer._IndexingKafkaConsumer__read_phase()

        consumer.consumer.commit.assert_not_awaited()
        assert consumer._requeue_message.await_count == 1  # the rest of the stale batch is not published
        assert not consumer._offset_tracker._outstanding.get(tp) and tp not in consumer._offset_tracker._committed


class TestReachedHandBackIsTrackedBeforeLaterOffsets:
    @pytest.mark.asyncio
    async def test_an_unparseable_message_behind_a_hand_back_cannot_commit_past_it(self) -> None:
        consumer = _consumer(_verdict(True), scheduler=True)
        tp = TopicPartition(TOPIC, 0)
        poison = _record(1)
        poison.value = b"{not json"
        consumer._requeue_message = AsyncMock()
        seen: list[tuple[int, int]] = []  # (offset committed, hand-backs published at that moment)

        async def commit(offsets):  # noqa: ANN202
            seen.append((offsets[tp], consumer._requeue_message.await_count))

        consumer.consumer.commit = AsyncMock(side_effect=commit)
        consumer.consumer.getmany = AsyncMock(return_value={tp: [_record(0), poison]})
        await consumer._IndexingKafkaConsumer__read_phase()

        # Nothing is committed past the hand-back before it is published.
        assert seen and all(offset <= 0 for offset, published in seen if published == 0)
        assert seen[-1] == (2, 1)

    @pytest.mark.asyncio
    async def test_the_hand_back_floors_the_watermark_while_the_poison_resolves(self) -> None:
        consumer = _consumer(_verdict(True), scheduler=True)
        tp = TopicPartition(TOPIC, 0)
        poison = _record(1)
        poison.value = b"{not json"
        consumer._requeue_message = AsyncMock(side_effect=RuntimeError("broker down"))
        consumer.consumer.getmany = AsyncMock(return_value={tp: [_record(0), poison]})
        await consumer._IndexingKafkaConsumer__read_phase()
        # the hand-back never published: nothing may be committed past offset 0
        for call in consumer.consumer.commit.await_args_list:
            assert call.args[0][tp] <= 0
