"""Kafka: reading past a connector at its cap, remembering only positions.

The report this guards: lanes are partitions chosen by connector, so two
connectors can share one, and a connector with a large backlog at the head of
the shared partition held another connector's messages behind it until the
backlog drained. Runs the real consume loop and worker thread against the
in-memory broker in ``tests.support.fake_kafka``.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
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
from app.services.messaging.kafka.consumer.remembered import RememberedOffsets
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import ParseTier
from tests.support.fake_kafka import FakeKafkaBroker

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Iterator

TOPIC = "record-events"
GROUP = "records_consumer_group"
TP = TopicPartition(TOPIC, 0)
BACKLOG = 20_000


def _envelope(record_id: str, connector_id: str) -> dict:
    return {
        "eventType": "newRecord",
        "payload": {
            "recordId": record_id,
            "orgId": "org-1",
            "connectorId": connector_id,
            "extension": "txt",
            "mimeType": "text/plain",
        },
        "timestamp": 1,
    }


def _fair(**overrides: object) -> FairSchedulerConfig:
    return FairSchedulerConfig(**{
        "enabled": True,
        "key_fields": ("orgId", "connectorId"),
        "default_quantum": 1,
        "max_buffered_messages": 200,
        "max_per_entity_messages": 50,
        "max_dwell_seconds": 900.0,
        "parallel_partitions": True,
        "max_remembered_positions": 200_000,
        **overrides,
    })


class Handler:
    """Records the order records reached the handler in. A record of a
    connector in ``slow`` takes ``delay`` seconds, so a backlog takes a while
    to work through, as it does in production."""

    def __init__(self, slow: str | None = None, delay: float = 0.0) -> None:
        self._lock = threading.Lock()
        self.seen: list[str] = []
        self.slow = slow
        self.delay = delay

    async def __call__(self, message: StreamMessage) -> AsyncGenerator[PipelineEvent, None]:
        with self._lock:
            self.seen.append(str(message.payload["recordId"]))
        yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(tier=ParseTier.LIGHT))
        if self.delay and message.payload["connectorId"] == self.slow:
            await asyncio.sleep(self.delay)
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    def first(self, prefix: str) -> int:
        with self._lock:
            return next((i for i, r in enumerate(self.seen) if r.startswith(prefix)), -1)


async def _until(predicate, timeout: float = 25.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached before timeout")
        await asyncio.sleep(0.02)


@pytest.fixture
def broker(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeKafkaBroker]:
    monkeypatch.setenv("MESSAGE_TIMEOUT_MS", "10")
    monkeypatch.setenv("MESSAGE_BATCH_SIZE_INDEXING", "100")
    monkeypatch.setenv("SHUTDOWN_TASK_TIMEOUT", "5")
    monkeypatch.setenv("MAX_CONCURRENT_INDEXING", "16")
    broker = FakeKafkaBroker()
    factory = broker.consumer_factory()
    with patch.object(consumer_module, "AIOKafkaConsumer", factory), \
            patch.object(remembered_module, "AIOKafkaConsumer", factory):
        yield broker


class Harness:
    def __init__(self, broker: FakeKafkaBroker) -> None:
        self.broker = broker
        self.consumers: list[IndexingKafkaConsumer] = []

    def produce_backlog(self, a: int = BACKLOG, b: int = 5) -> None:
        for i in range(a):
            self.broker.produce(TOPIC, _envelope(f"a-{i:05d}", "conn-a"))
        for i in range(b):
            self.broker.produce(TOPIC, _envelope(f"b-{i}", "conn-b"))

    def committed(self) -> int:
        return self.broker.committed_offset(GROUP, TOPIC) or 0

    async def start(self, handler: Handler, **fair: object) -> IndexingKafkaConsumer:
        consumer = IndexingKafkaConsumer(
            logging.getLogger("test"),
            KafkaConsumerConfig(
                topics=[TOPIC], client_id="indexing-test", group_id=GROUP,
                auto_offset_reset="earliest", enable_auto_commit=False,
                bootstrap_servers=["kafka:9092"],
            ),
            fair_scheduler_config=_fair(**fair),
        )
        self.consumers.append(consumer)
        await consumer.start(handler)
        return consumer

    async def stop(self) -> None:
        for consumer in self.consumers:
            await consumer.stop()


@pytest.fixture
async def harness(broker: FakeKafkaBroker) -> AsyncGenerator[Harness, None]:
    h = Harness(broker)
    yield h
    await h.stop()


def _a_in_order(handler: Handler) -> bool:
    a = [r for r in handler.seen if r.startswith("a-")]
    return a == sorted(a)


class TestTheReport:
    async def test_a_connector_behind_a_large_backlog_on_its_partition_is_reached_within_a_few_reads(
        self, harness: Harness
    ) -> None:
        harness.produce_backlog()
        # conn-a's records take a little time each, as indexing does, so its
        # backlog outlasts the reads, which is what puts it at its cap.
        handler = Handler(slow="conn-a", delay=0.002)
        consumer = await harness.start(handler)

        await _until(lambda: handler.first("b-") >= 0)
        first_b = handler.first("b-")
        remembered_at_b = consumer._remembered.total
        await _until(lambda: len(handler.seen) >= 1_000)

        print(f"first conn-b record reached the handler at position {first_b} of {BACKLOG + 5}; "
              f"{remembered_at_b} conn-a positions remembered then")
        assert first_b <= 60
        assert remembered_at_b > 15_000
        assert _a_in_order(handler)

    async def test_a_backlog_read_past_is_still_indexed_whole_and_in_order(self, harness: Harness) -> None:
        harness.produce_backlog(a=3_000)
        handler = Handler(slow="conn-a", delay=0.001)
        consumer = await harness.start(handler)

        await _until(lambda: harness.committed() == 3_005, timeout=60.0)

        assert handler.first("b-") <= 60
        assert len(handler.seen) == 3_005
        assert _a_in_order(handler)
        assert consumer._remembered.total == 0

    async def test_with_reading_past_off_the_backlog_still_blocks_the_other_connector(
        self, harness: Harness
    ) -> None:
        """Today's behaviour, kept when the budget is 0: what the test above
        guards against."""
        harness.produce_backlog(a=3_000)
        handler = Handler(slow="conn-a", delay=0.001)
        await harness.start(handler, max_remembered_positions=0)

        await _until(lambda: harness.committed() == 3_005, timeout=60.0)

        print(f"reading past off: first conn-b record reached the handler at position "
              f"{handler.first('b-')} of 3005")
        assert handler.first("b-") > 2_500


class TestThePositionsBudget:
    async def test_past_the_budget_the_lane_stops_and_nothing_is_lost_or_reordered(
        self, harness: Harness, caplog: pytest.LogCaptureFixture
    ) -> None:
        harness.produce_backlog(a=3_000)
        handler = Handler(slow="conn-a", delay=0.001)
        with caplog.at_level(logging.WARNING):
            consumer = await harness.start(handler, max_remembered_positions=300)
            await _until(lambda: harness.committed() == 3_005, timeout=60.0)

        assert consumer._remembered.total == 0
        assert len(handler.seen) == 3_005
        assert _a_in_order(handler)
        assert "Remembered-positions budget of 300 is spent" in caplog.text


class TestTheWatermark:
    async def test_nothing_is_committed_past_a_remembered_offset(self, harness: Harness) -> None:
        harness.produce_backlog(a=500)
        release = asyncio.Event()
        real_fetch = remembered_module.OffsetFetcher.fetch

        async def held_fetch(self, wanted):  # noqa: ANN202
            await release.wait()
            return await real_fetch(self, wanted)

        handler = Handler()
        with patch.object(remembered_module.OffsetFetcher, "fetch", held_fetch):
            consumer = await harness.start(handler)
            await _until(lambda: handler.first("b-") >= 0)
            await _until(lambda: len(handler.seen) == 50 + 5)
            await asyncio.sleep(0.3)
            oldest_remembered = min(
                offset for e in consumer._remembered.entities()
                for _tp, offset in consumer._remembered.peek(e, 10**9)
            )
            committed_while_waiting = harness.committed()
            release.set()
            await _until(lambda: harness.committed() == 505)

        assert committed_while_waiting <= oldest_remembered == 50
        assert _a_in_order(handler)


class TestRebalanceRetentionAndRestart:
    async def test_a_revoked_partitions_positions_are_forgotten_and_left_to_its_new_owner(
        self, harness: Harness
    ) -> None:
        harness.produce_backlog(a=500)
        release = asyncio.Event()
        fetched: list[dict] = []
        real_fetch = remembered_module.OffsetFetcher.fetch

        async def held_fetch(self, wanted):  # noqa: ANN202
            fetched.append(wanted)
            await release.wait()
            return await real_fetch(self, wanted)

        handler = Handler()
        with patch.object(remembered_module.OffsetFetcher, "fetch", held_fetch):
            consumer = await harness.start(handler)
            await _until(lambda: len(handler.seen) == 55)
            await _until(lambda: bool(fetched))
            main = harness.broker.consumers[0]
            main.assigned_by_hand = []
            await consumer._on_partitions_revoked([TP])
            assert consumer._remembered.total == 0
            release.set()
            await asyncio.sleep(0.3)
            seen_before_reassign = list(handler.seen)
            # Assigned again: like a new owner, it starts from the committed offset.
            main.assigned_by_hand = None
            main.position.pop(TP, None)
            await _until(lambda: harness.committed() == 505)

        assert seen_before_reassign == handler.seen[: len(seen_before_reassign)]
        assert len(seen_before_reassign) == 55, "nothing fetched back after the revoke"
        assert {*handler.seen} == {f"a-{i:05d}" for i in range(500)} | {f"b-{i}" for i in range(5)}

    async def test_an_offset_deleted_by_retention_is_resolved_and_the_rest_fetched_back(
        self, harness: Harness, caplog: pytest.LogCaptureFixture
    ) -> None:
        harness.produce_backlog(a=500)
        release = asyncio.Event()
        real_fetch = remembered_module.OffsetFetcher.fetch

        async def held_fetch(self, wanted):  # noqa: ANN202
            await release.wait()
            return await real_fetch(self, wanted)

        handler = Handler()
        with caplog.at_level(logging.WARNING), \
                patch.object(remembered_module.OffsetFetcher, "fetch", held_fetch):
            await harness.start(handler)
            await _until(lambda: len(handler.seen) == 55)
            harness.broker.log_start[TP] = 80
            release.set()
            await _until(lambda: harness.committed() == 505)

        assert not any(f"a-{i:05d}" in handler.seen for i in range(50, 80))
        assert "deleted by Kafka retention" in caplog.text
        assert len(handler.seen) == 505 - 30

    async def test_a_restart_replays_from_the_watermark_and_loses_nothing(self, harness: Harness) -> None:
        harness.produce_backlog(a=500)
        release = asyncio.Event()
        real_fetch = remembered_module.OffsetFetcher.fetch

        async def held_fetch(self, wanted):  # noqa: ANN202
            await release.wait()
            return await real_fetch(self, wanted)

        first = Handler()
        with patch.object(remembered_module.OffsetFetcher, "fetch", held_fetch):
            consumer = await harness.start(first)
            await _until(lambda: len(first.seen) == 55)
            committed_at_stop = harness.committed()
            release.set()
            await consumer.stop()

        second = Handler()
        await harness.start(second)
        await _until(lambda: harness.committed() == 505)

        replayed = sorted({*first.seen} & {*second.seen})
        print(f"restart: committed {committed_at_stop}, replayed {len(replayed)} already-handled records")
        assert committed_at_stop <= 50
        assert {*first.seen} | {*second.seen} == (
            {f"a-{i:05d}" for i in range(500)} | {f"b-{i}" for i in range(5)}
        )


class TestRememberedOffsets:
    def test_each_connector_keeps_its_own_order_within_one_budget(self) -> None:
        remembered = RememberedOffsets(budget=3)
        assert remembered.remember(("o", "a"), TP, 5)
        assert remembered.remember(("o", "b"), TP, 6)
        assert remembered.remember(("o", "a"), TP, 7)
        assert not remembered.remember(("o", "a"), TP, 8)

        assert remembered.peek(("o", "a"), 10) == [(TP, 5), (TP, 7)]
        assert remembered.pop(("o", "a")) == (TP, 5)
        remembered.restore_front(("o", "a"), [(TP, 5)])
        assert remembered.peek(("o", "a"), 10) == [(TP, 5), (TP, 7)]
        assert remembered.total == 3

    def test_dropping_a_partition_forgets_only_its_positions(self) -> None:
        other = TopicPartition(TOPIC, 1)
        remembered = RememberedOffsets(budget=10)
        remembered.remember(("o", "a"), TP, 1)
        remembered.remember(("o", "a"), other, 2)
        remembered.remember(("o", "b"), TP, 3)

        assert remembered.drop_partitions([TP]) == 2
        assert remembered.total == 1
        assert remembered.entities() == [("o", "a")]
        assert remembered.has_room


class TestARevokeWhileFetchedBackMessagesAreInHand:
    async def test_they_are_neither_buffered_nor_remembered_nor_committed(self, harness: Harness) -> None:
        """The revocation can run during any await after the positions were
        taken off the queue, where it can no longer see them. They must be left
        to the partition's next owner, which reads them from the commit."""
        from app.services.messaging.connector_off import ConnectorOffResult

        harness.produce_backlog(a=500)
        seen_in_filter: dict[str, int] = {}
        revoked: list[bool] = []
        consumer_ref: list[IndexingKafkaConsumer] = []

        class RevokeOnFetchBack:
            async def settle(self, messages):  # noqa: ANN202
                for message in messages:
                    rid = message.payload["recordId"]
                    seen_in_filter[rid] = seen_in_filter.get(rid, 0) + 1
                # The second time a-00050 passes through is its fetch-back.
                if seen_in_filter.get("a-00050") == 2 and not revoked:
                    revoked.append(True)
                    await consumer_ref[0]._on_partitions_revoked([TP])
                return ConnectorOffResult()

        handler = Handler()
        consumer = IndexingKafkaConsumer(
            logging.getLogger("test"),
            KafkaConsumerConfig(
                topics=[TOPIC], client_id="indexing-test", group_id=GROUP,
                auto_offset_reset="earliest", enable_auto_commit=False,
                bootstrap_servers=["kafka:9092"],
            ),
            fair_scheduler_config=_fair(),
            connector_off_filter=RevokeOnFetchBack(),
        )
        consumer_ref.append(consumer)
        harness.consumers.append(consumer)
        await consumer.start(handler)

        await _until(lambda: bool(revoked))
        await asyncio.sleep(0.5)
        main = harness.broker.consumers[0]
        assert not any(r.startswith("a-0005") for r in handler.seen[55:]), "fetched-back work ran"
        assert "a-00050" not in handler.seen
        assert all(tp != TP for e in consumer._remembered.entities()
                   for tp, _o in consumer._remembered.peek(e, 10**9))
        assert harness.committed() <= 50
        assert consumer._scheduler.pending_count == 0

        # Given back: as aiokafka does, the position restarts at the commit.
        await consumer._on_partitions_assigned([TP])
        main.position.pop(TP, None)
        await _until(lambda: harness.committed() == 505)
        assert {*handler.seen} == {f"a-{i:05d}" for i in range(500)} | {f"b-{i}" for i in range(5)}


class TestFetchBackFailures:
    async def test_a_failed_commit_while_resolving_deleted_offsets_does_not_strand_the_partition(
        self, harness: Harness
    ) -> None:
        harness.produce_backlog(a=500)
        release = asyncio.Event()
        real_fetch = remembered_module.OffsetFetcher.fetch

        async def held_fetch(self, wanted):  # noqa: ANN202
            await release.wait()
            return await real_fetch(self, wanted)

        handler = Handler()
        with patch.object(remembered_module.OffsetFetcher, "fetch", held_fetch):
            consumer = await harness.start(handler)
            await _until(lambda: len(handler.seen) == 55)
            main = harness.broker.consumers[0]
            commit = main.commit
            failed: list[dict] = []

            async def commit_failing_once(offsets=None):  # noqa: ANN202
                if not failed:
                    failed.append(dict(offsets or {}))
                    raise RuntimeError("coordinator moved")
                return await commit(offsets)

            main.commit = commit_failing_once
            harness.broker.log_start[TP] = 80
            release.set()
            await _until(lambda: harness.committed() == 505)

        assert failed, "the failing commit was never attempted"
        assert len(handler.seen) == 505 - 30
        assert consumer._remembered.total == 0

    async def test_a_message_that_fails_to_parse_on_fetch_back_stays_remembered_and_is_retried(
        self, harness: Harness
    ) -> None:
        harness.produce_backlog(a=500)
        handler = Handler()
        consumer = await harness.start(handler)
        parse = consumer._IndexingKafkaConsumer__parse_message
        calls: dict[int, int] = {}

        async def parse_failing_once_on_fetch_back(message):  # noqa: ANN202
            calls[message.offset] = calls.get(message.offset, 0) + 1
            # The second parse of offset 50 is its fetch-back.
            if message.offset == 50 and calls[50] == 2:
                raise ValueError("transient")
            return await parse(message)

        consumer._IndexingKafkaConsumer__parse_message = parse_failing_once_on_fetch_back
        await _until(lambda: harness.committed() == 505)

        assert calls[50] >= 3
        assert handler.seen.count("a-00050") == 1
        assert _a_in_order(handler)


async def test_a_revoke_while_a_poison_message_is_reported_commits_nothing(harness: Harness) -> None:
    """The revocation clears the partition's watermark; marking the poison
    offset done afterwards would compute one from nothing and commit past
    offsets the next owner still needs."""
    harness.broker.produce(TOPIC, _envelope("a-00000", "conn-a"))
    harness.broker.produce(TOPIC, b"{not json")
    consumer_ref: list[IndexingKafkaConsumer] = []
    reported: list[bool] = []

    class RevokingSink:
        async def on_message_abandoned(self, message, *, reason, attempts):  # noqa: ANN202
            await consumer_ref[0]._on_partitions_revoked([TP])
            reported.append(True)

    handler = Handler()
    consumer = IndexingKafkaConsumer(
        logging.getLogger("test"),
        KafkaConsumerConfig(
            topics=[TOPIC], client_id="indexing-test", group_id=GROUP,
            auto_offset_reset="earliest", enable_auto_commit=False,
            bootstrap_servers=["kafka:9092"],
        ),
        fair_scheduler_config=_fair(),
        disposition_sink=RevokingSink(),
    )
    consumer_ref.append(consumer)
    harness.consumers.append(consumer)
    await consumer.start(handler)
    await _until(lambda: bool(reported))
    await asyncio.sleep(0.3)

    # Offset 0 was buffered when the revocation purged it; a commit past it
    # would make the next owner skip it.
    commits = harness.broker.consumers[0].commit_calls
    assert all(c.get(TP, 0) == 0 for c in commits), commits
