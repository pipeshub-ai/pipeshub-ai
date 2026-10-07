"""Queued events of a turned-off or removed connector are settled as they are read.

Runs both indexing consumers' real consume loops and worker threads against the
in-memory brokers (fakeredis, ``tests.support.fake_kafka``), with the real
``GraphConnectorOffFilter`` over an in-memory graph. The handler stands in for
the record handler and records what reached it: anything settled at read time
must never get there, and must never take a buffer slot on the way.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from logging import getLogger
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from aiokafka.structs import TopicPartition

pytest.importorskip("fakeredis.aioredis")

from app.config.constants.arangodb import EventTypes, ProgressStatus
from app.modules.indexing.connector_off_events import GraphConnectorOffFilter
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
    StreamMessage,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer import indexing_consumer as kafka_module
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import ParseTier
from tests.support.fake_connector_graph import FakeConnectorGraph
from tests.support.fake_kafka import FakeKafkaBroker
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Iterator

    from tests.support.fake_cluster_redis import FakeClusterRedis

TOPIC = "record-events"
GROUP = "records_consumer_group"
BACKLOG = 20_000
OFF, ON, GONE = "conn-off", "conn-on", "conn-gone"


def _envelope(record_id: str, connector_id: str, event_type: str = "newRecord", **extra: object) -> dict:
    return {
        "eventType": event_type,
        "payload": {
            "recordId": record_id,
            "orgId": "org-1",
            "connectorId": connector_id,
            "extension": "txt",
            "mimeType": "text/plain",
            **extra,
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
        **overrides,
    })


class Handler:
    """Records every record that reached the handler. A held record waits
    until ``release`` is set, so "still indexing" is a state the test controls."""

    def __init__(self, hold: set[str] | None = None) -> None:
        self._lock = threading.Lock()
        self.seen: list[str] = []
        self.hold = hold or set()
        self.release = threading.Event()

    async def __call__(self, message: StreamMessage) -> AsyncGenerator[PipelineEvent, None]:
        record_id = str(message.payload["recordId"])
        with self._lock:
            self.seen.append(record_id)
        yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(tier=ParseTier.LIGHT))
        if record_id in self.hold:
            await asyncio.to_thread(self.release.wait, 20.0)
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)


class BufferWatch:
    """Counts what entered the fair-scheduling buffer, per connector."""

    def __init__(self, consumer: IndexingRedisStreamsConsumer | IndexingKafkaConsumer) -> None:
        self.by_connector: dict[str, int] = {}
        scheduler = consumer._scheduler
        enqueue = scheduler.enqueue

        def counted(key, item, not_before=None):  # noqa: ANN202
            result = enqueue(key, item, not_before=not_before)
            self.by_connector[key[1]] = self.by_connector.get(key[1], 0) + 1
            return result

        scheduler.enqueue = counted


def _graph(off_records: int = BACKLOG) -> FakeConnectorGraph:
    graph = FakeConnectorGraph()
    graph.add_connector(OFF, active=False)
    graph.add_connector(ON, active=True)
    for i in range(off_records):
        graph.add_record(f"off-{i}", OFF)
    for i in range(5):
        graph.add_record(f"on-{i}", ON)
    return graph


def _attach(consumer, graph: FakeConnectorGraph) -> None:
    # Assigned rather than passed to the constructor so this module also runs,
    # and fails on its assertions, against a consumer that predates the filter.
    consumer.connector_off_filter = GraphConnectorOffFilter(graph, getLogger("test"), 15.0)


async def _until(predicate, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if asyncio.iscoroutine(value):
            value = await value
        if value:
            return
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached before timeout")
        await asyncio.sleep(0.02)


# --------------------------------------------------------------------------- Redis


class _NonBlockingReadProvider(FakeRedisConnectionProvider):
    """fakeredis can deadlock when one client blocks in XREADGROUP while another
    runs a command; reads go out without BLOCK and an empty one waits instead."""

    def create_client(self, options=None) -> FakeClusterRedis:
        client = super().create_client(options)
        read = client.xreadgroup

        async def xreadgroup(*args, block=None, **kwargs):  # noqa: ANN202
            result = await read(*args, **kwargs)
            if not result and block:
                await asyncio.sleep(block / 1000)
            return result

        client.xreadgroup = xreadgroup
        return client


class RedisHarness:
    def __init__(self) -> None:
        self.provider = _NonBlockingReadProvider(is_cluster=False)
        self.consumer: IndexingRedisStreamsConsumer | None = None

    @property
    def client(self) -> FakeClusterRedis:
        return self.provider.get_client()

    async def produce(self, *envelopes: dict) -> None:
        pipe = self.client.pipeline(transaction=False)
        for envelope in envelopes:
            pipe.xadd(TOPIC, {"value": json.dumps(envelope)})
        await pipe.execute()

    def build(self, graph: FakeConnectorGraph | None) -> IndexingRedisStreamsConsumer:
        self.consumer = IndexingRedisStreamsConsumer(
            getLogger("test"),
            RedisStreamsConfig(
                topics=[TOPIC], group_id=GROUP, client_id="indexing-1",
                batch_size=100, block_ms=20, claim_min_idle_ms=60_000,
            ),
            fair_scheduler_config=_fair(),
            provider=self.provider,
        )
        if graph is not None:
            _attach(self.consumer, graph)
        return self.consumer

    async def settled(self) -> bool:
        """Every entry was handed to the group and none is still pending."""
        stream = await self.client.xinfo_stream(TOPIC)
        group = next(g for g in await self.client.xinfo_groups(TOPIC) if g["name"] == GROUP)
        return group["last-delivered-id"] == stream["last-generated-id"] and int(group["pending"]) == 0

    async def stop(self) -> None:
        if self.consumer is not None:
            await self.consumer.stop()


@pytest.fixture
async def redis_harness() -> AsyncGenerator[RedisHarness, None]:
    harness = RedisHarness()
    yield harness
    await harness.stop()


class TestRedisStreams:
    async def test_a_turned_off_connectors_backlog_drains_at_read_speed_without_dispatch(
        self, redis_harness: RedisHarness
    ) -> None:
        """The report: an off connector's backlog ahead of another connector on
        the same lane. Its events are acknowledged as they are read, a batch
        write at a time, and none of them takes a buffer slot or the handler."""
        graph = _graph()
        await redis_harness.produce(*(_envelope(f"off-{i}", OFF) for i in range(BACKLOG)))
        await redis_harness.produce(*(_envelope(f"on-{i}", ON) for i in range(5)))
        consumer = redis_harness.build(graph)
        watch = BufferWatch(consumer)
        handler = Handler()

        started = time.monotonic()
        await consumer.start(handler)
        await _until(redis_harness.settled)
        await _until(lambda: len(handler.seen) == 5)
        elapsed = time.monotonic() - started

        assert sorted(handler.seen) == [f"on-{i}" for i in range(5)]
        assert OFF not in watch.by_connector
        statuses = {r["indexingStatus"] for k, r in graph.records.items() if k.startswith("off-")}
        assert statuses == {ProgressStatus.AUTO_INDEX_OFF.value}
        # One write per read pass, not one per event.
        assert graph.calls["batch_update_nodes:records"] <= BACKLOG // 100 + 5
        assert graph.calls["update_node:records"] == 0
        print(f"redis: {BACKLOG} settled in {elapsed:.1f}s, "
              f"{graph.calls['batch_update_nodes:records']} batch writes")

    async def test_rebuild_and_enrichment_resume_events_still_reach_the_handler(
        self, redis_harness: RedisHarness
    ) -> None:
        graph = _graph(off_records=0)
        graph.add_record("rebuilt", OFF, indexingStatus=ProgressStatus.COMPLETED.value)
        graph.add_record(
            "resumed", OFF,
            indexingStatus=ProgressStatus.COMPLETED.value,
            extractionStatus=ProgressStatus.IN_PROGRESS.value,
        )
        graph.add_record("queued", OFF)
        await redis_harness.produce(
            _envelope("rebuilt", OFF, EventTypes.REINDEX_RECORD.value, vectorDbOnly=True),
            _envelope("resumed", OFF),
            _envelope("queued", OFF),
            _envelope("deleted", OFF, EventTypes.DELETE_RECORD.value),
        )
        handler = Handler()
        await redis_harness.build(graph).start(handler)

        await _until(redis_harness.settled)
        await _until(lambda: len(handler.seen) == 3)

        assert sorted(handler.seen) == ["deleted", "rebuilt", "resumed"]
        assert graph.records["queued"]["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value

    async def test_an_unreadable_connector_state_drops_nothing(self, redis_harness: RedisHarness) -> None:
        graph = _graph(off_records=20)
        graph.fail_reads.add("apps")
        await redis_harness.produce(*(_envelope(f"off-{i}", OFF) for i in range(20)))
        handler = Handler()
        await redis_harness.build(graph).start(handler)

        await _until(lambda: len(handler.seen) == 20)
        await _until(redis_harness.settled)

        assert graph.batch_updates == []

    async def test_a_removed_connectors_events_are_acknowledged_without_a_status_write(
        self, redis_harness: RedisHarness
    ) -> None:
        graph = _graph(off_records=0)
        for i in range(30):
            graph.add_record(f"gone-{i}", GONE)
        await redis_harness.produce(*(_envelope(f"gone-{i}", GONE) for i in range(30)))
        await redis_harness.produce(_envelope("on-0", ON))
        handler = Handler()
        await redis_harness.build(graph).start(handler)

        await _until(redis_harness.settled)
        await _until(lambda: len(handler.seen) == 1)

        assert handler.seen == ["on-0"]
        assert graph.batch_updates == []
        assert {graph.records[f"gone-{i}"]["indexingStatus"] for i in range(30)} == {ProgressStatus.QUEUED.value}


# --------------------------------------------------------------------------- Kafka


@pytest.fixture
def kafka_broker(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeKafkaBroker]:
    monkeypatch.setenv("MESSAGE_TIMEOUT_MS", "10")
    monkeypatch.setenv("MESSAGE_BATCH_SIZE_INDEXING", "100")
    monkeypatch.setenv("SHUTDOWN_TASK_TIMEOUT", "5")
    broker = FakeKafkaBroker()
    with patch.object(kafka_module, "AIOKafkaConsumer", broker.consumer_factory()):
        yield broker


class KafkaHarness:
    def __init__(self, broker: FakeKafkaBroker) -> None:
        self.broker = broker
        self.consumer: IndexingKafkaConsumer | None = None

    def produce(self, *envelopes: dict) -> None:
        for envelope in envelopes:
            self.broker.produce(TOPIC, envelope)

    def committed(self) -> int:
        return self.broker.committed_offset(GROUP, TOPIC) or 0

    def build(self, graph: FakeConnectorGraph | None, **fair: object) -> IndexingKafkaConsumer:
        self.consumer = IndexingKafkaConsumer(
            logging.getLogger("test"),
            KafkaConsumerConfig(
                topics=[TOPIC], client_id="indexing-test", group_id=GROUP,
                auto_offset_reset="earliest", enable_auto_commit=False,
                bootstrap_servers=["kafka:9092"],
            ),
            fair_scheduler_config=_fair(**fair),
        )
        if graph is not None:
            _attach(self.consumer, graph)
        return self.consumer

    async def stop(self) -> None:
        if self.consumer is not None:
            await self.consumer.stop()


@pytest.fixture
async def kafka_harness(kafka_broker: FakeKafkaBroker) -> AsyncGenerator[KafkaHarness, None]:
    harness = KafkaHarness(kafka_broker)
    yield harness
    await harness.stop()


class TestKafka:
    async def test_a_turned_off_connectors_backlog_drains_at_read_speed_without_dispatch(
        self, kafka_harness: KafkaHarness
    ) -> None:
        graph = _graph()
        kafka_harness.produce(*(_envelope(f"off-{i}", OFF) for i in range(BACKLOG)))
        kafka_harness.produce(*(_envelope(f"on-{i}", ON) for i in range(5)))
        consumer = kafka_harness.build(graph)
        watch = BufferWatch(consumer)
        handler = Handler()

        started = time.monotonic()
        await consumer.start(handler)
        await _until(lambda: kafka_harness.committed() == BACKLOG + 5)
        elapsed = time.monotonic() - started

        assert sorted(handler.seen) == [f"on-{i}" for i in range(5)]
        assert OFF not in watch.by_connector
        statuses = {r["indexingStatus"] for k, r in graph.records.items() if k.startswith("off-")}
        assert statuses == {ProgressStatus.AUTO_INDEX_OFF.value}
        assert graph.calls["batch_update_nodes:records"] <= BACKLOG // 100 + 5
        print(f"kafka: {BACKLOG} settled in {elapsed:.1f}s, "
              f"{graph.calls['batch_update_nodes:records']} batch writes")

    async def test_a_settled_offset_never_carries_the_commit_past_unfinished_work(
        self, kafka_harness: KafkaHarness
    ) -> None:
        graph = _graph(off_records=100)
        kafka_harness.produce(_envelope("on-0", ON))
        kafka_harness.produce(*(_envelope(f"off-{i}", OFF) for i in range(100)))
        handler = Handler(hold={"on-0"})
        # Parallel dispatch keeps reading the partition while on-0 is in
        # flight; serial dispatch pauses it until on-0 is done.
        await kafka_harness.build(graph, parallel_partitions=True).start(handler)

        await _until(lambda: handler.seen == ["on-0"])
        await _until(lambda: all(
            graph.records[f"off-{i}"]["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value
            for i in range(100)
        ))
        assert kafka_harness.committed() == 0

        handler.release.set()
        await _until(lambda: kafka_harness.committed() == 101)

    async def test_rebuild_and_enrichment_resume_events_still_reach_the_handler(
        self, kafka_harness: KafkaHarness
    ) -> None:
        graph = _graph(off_records=0)
        graph.add_record("rebuilt", OFF, indexingStatus=ProgressStatus.COMPLETED.value)
        graph.add_record(
            "resumed", OFF,
            indexingStatus=ProgressStatus.COMPLETED.value,
            extractionStatus=ProgressStatus.IN_PROGRESS.value,
        )
        graph.add_record("queued", OFF)
        kafka_harness.produce(
            _envelope("rebuilt", OFF, EventTypes.REINDEX_RECORD.value, vectorDbOnly=True),
            _envelope("resumed", OFF),
            _envelope("queued", OFF),
        )
        handler = Handler()
        await kafka_harness.build(graph).start(handler)

        await _until(lambda: kafka_harness.committed() == 3)

        assert sorted(handler.seen) == ["rebuilt", "resumed"]
        assert graph.records["queued"]["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value

    async def test_an_unreadable_connector_state_drops_nothing(self, kafka_harness: KafkaHarness) -> None:
        graph = _graph(off_records=20)
        graph.fail_reads.add("apps")
        kafka_harness.produce(*(_envelope(f"off-{i}", OFF) for i in range(20)))
        handler = Handler()
        await kafka_harness.build(graph).start(handler)

        await _until(lambda: kafka_harness.committed() == 20)

        assert len(handler.seen) == 20
        assert graph.batch_updates == []

    async def test_a_removed_connectors_events_are_committed_without_a_status_write(
        self, kafka_harness: KafkaHarness
    ) -> None:
        graph = _graph(off_records=0)
        for i in range(30):
            graph.add_record(f"gone-{i}", GONE)
        kafka_harness.produce(*(_envelope(f"gone-{i}", GONE) for i in range(30)))
        handler = Handler()
        await kafka_harness.build(graph).start(handler)

        await _until(lambda: kafka_harness.committed() == 30)

        assert handler.seen == []
        assert graph.batch_updates == []

    async def test_a_partition_revoked_while_the_filter_runs_is_left_to_its_next_owner(
        self, kafka_harness: KafkaHarness
    ) -> None:
        """The batch read before the revoke is stale: the revocation dropped
        its tracked offsets, and the next owner, here this consumer again,
        starts from the committed offset. Buffering it as well would run
        on-0 twice."""
        graph = _graph(off_records=10)
        kafka_harness.produce(*(_envelope(f"off-{i}", OFF) for i in range(10)))
        kafka_harness.produce(_envelope("on-0", ON))
        consumer = kafka_harness.build(graph)
        inner = consumer.connector_off_filter
        revoked_once: list[bool] = []

        class RevokingFilter:
            async def settle(self, messages):  # noqa: ANN202
                result = await inner.settle(messages)
                if not revoked_once:
                    revoked_once.append(True)
                    await consumer._on_partitions_revoked([TopicPartition(TOPIC, 0)])
                return result

        consumer.connector_off_filter = RevokingFilter()
        handler = Handler()
        await consumer.start(handler)

        await _until(lambda: graph.calls["compare_and_set_indexing_status"] >= 1)
        await asyncio.sleep(0.2)
        assert handler.seen == []
        assert kafka_harness.broker.consumers[0].commit_calls == []

        # Given back: as aiokafka does, the position restarts at the commit.
        await consumer._on_partitions_assigned([TopicPartition(TOPIC, 0)])
        kafka_harness.broker.consumers[0].position.pop(TopicPartition(TOPIC, 0), None)
        await _until(lambda: kafka_harness.committed() == 11)

        assert handler.seen == ["on-0"]

    async def test_a_rebalance_inside_the_poll_does_not_drop_what_the_poll_returns(
        self, kafka_harness: KafkaHarness
    ) -> None:
        """aiokafka runs the rebalance inside getmany() and hands back the new
        assignment's records; those are this consumer's to process."""
        graph = _graph(off_records=10)
        kafka_harness.produce(*(_envelope(f"off-{i}", OFF) for i in range(10)))
        kafka_harness.produce(_envelope("on-0", ON))
        consumer = kafka_harness.build(graph)
        handler = Handler()
        await consumer.start(handler)
        fake = kafka_harness.broker.consumers[0]
        getmany = fake.getmany
        rebalanced: list[bool] = []

        async def getmany_with_a_rebalance(*args, **kwargs):  # noqa: ANN202
            if not rebalanced:
                rebalanced.append(True)
                tp = TopicPartition(TOPIC, 0)
                await consumer._on_partitions_revoked([tp])
                await consumer._on_partitions_assigned([tp])
                fake.position.pop(tp, None)
            return await getmany(*args, **kwargs)

        fake.getmany = getmany_with_a_rebalance

        await _until(lambda: kafka_harness.committed() == 11)

        assert handler.seen == ["on-0"]


def _flip_to_in_progress_after_read(graph: FakeConnectorGraph, record_id: str) -> None:
    """Another delivery starts on ``record_id`` between the filter's read and its write."""
    read = graph.get_nodes_by_field_in

    async def read_then_flip(collection, *args, **kwargs):  # noqa: ANN202
        docs = await read(collection, *args, **kwargs)
        if collection == "records" and graph.records[record_id]["indexingStatus"] == ProgressStatus.QUEUED.value:
            graph.records[record_id]["indexingStatus"] = ProgressStatus.IN_PROGRESS.value
        return docs

    graph.get_nodes_by_field_in = read_then_flip


class TestARecordMovedOnBetweenReadAndWrite:
    async def test_redis_leaves_it_unacknowledged_for_the_normal_path(self, redis_harness: RedisHarness) -> None:
        graph = _graph(off_records=3)
        _flip_to_in_progress_after_read(graph, "off-1")
        await redis_harness.produce(*(_envelope(f"off-{i}", OFF) for i in range(3)))
        handler = Handler()
        await redis_harness.build(graph).start(handler)

        await _until(redis_harness.settled)
        await _until(lambda: handler.seen == ["off-1"])

        assert graph.records["off-1"]["indexingStatus"] == ProgressStatus.IN_PROGRESS.value
        assert graph.records["off-0"]["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value

    async def test_kafka_leaves_it_unresolved_for_the_normal_path(self, kafka_harness: KafkaHarness) -> None:
        graph = _graph(off_records=3)
        _flip_to_in_progress_after_read(graph, "off-1")
        kafka_harness.produce(*(_envelope(f"off-{i}", OFF) for i in range(3)))
        handler = Handler()
        await kafka_harness.build(graph).start(handler)

        await _until(lambda: kafka_harness.committed() == 3)

        assert handler.seen == ["off-1"]
        assert graph.records["off-1"]["indexingStatus"] == ProgressStatus.IN_PROGRESS.value
        assert graph.records["off-0"]["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value
