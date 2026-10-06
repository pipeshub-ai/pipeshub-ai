"""What the Kafka consumer reports as still waiting on each partition.

Only the broker is faked, by ``tests.support.fake_kafka``, which keeps Kafka's
rules for positions, committed offsets and manual assignment. The same reads
are checked against a real broker in
``tests/integration/messaging/test_kafka_fair_scheduling_it.py``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from aiokafka.structs import TopicPartition

from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer import backlog as backlog_module
from app.services.messaging.kafka.consumer.backlog import read_partition_backlog
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from tests.support.fake_kafka import FakeAIOKafkaConsumer, FakeKafkaBroker

if TYPE_CHECKING:
    from collections.abc import Iterator

TOPIC = "record-events"
GROUP = "records_consumer_group"
CLIENT = {"bootstrap_servers": "kafka:9092", "group_id": GROUP, "client_id": "indexing-backlog"}


@pytest.fixture
def broker() -> Iterator[FakeKafkaBroker]:
    broker = FakeKafkaBroker()
    with patch.object(backlog_module, "AIOKafkaConsumer", broker.consumer_factory()):
        yield broker


def _produce(broker: FakeKafkaBroker, partition: int, *timestamps: int) -> None:
    for timestamp in timestamps:
        broker.produce(TOPIC, {"eventType": "newRecord"}, partition=partition, timestamp_ms=timestamp)


def _commit(broker: FakeKafkaBroker, partition: int, offset: int) -> None:
    broker.committed[GROUP][TopicPartition(TOPIC, partition)] = offset


async def _read(broker: FakeKafkaBroker, partitions: list[int], reset: str = "earliest") -> dict[str, float]:
    return await read_partition_backlog(
        CLIENT, TOPIC, partitions, auto_offset_reset=reset, timeout_seconds=2.0
    )


class TestReadPartitionBacklog:
    async def test_the_record_at_the_committed_offset_is_the_oldest_still_waiting(
        self, broker: FakeKafkaBroker
    ) -> None:
        _produce(broker, 0, 1000, 2000, 3000)
        _commit(broker, 0, 1)

        assert await _read(broker, [0]) == {"record-events-0": 2000.0}

    async def test_a_partition_the_group_has_caught_up_on_is_left_out(self, broker: FakeKafkaBroker) -> None:
        _produce(broker, 0, 1000, 2000)
        _commit(broker, 0, 2)

        assert await _read(broker, [0]) == {}

    async def test_each_partition_is_reported_on_its_own(self, broker: FakeKafkaBroker) -> None:
        _produce(broker, 0, 1000, 2000)
        _produce(broker, 1, 5000, 6000, 7000)
        _produce(broker, 2, 9000)
        _commit(broker, 0, 2)
        _commit(broker, 1, 2)
        _commit(broker, 2, 0)

        assert await _read(broker, [0, 1, 2]) == {
            "record-events-1": 7000.0,
            "record-events-2": 9000.0,
        }

    async def test_a_group_that_never_committed_starts_from_the_beginning_when_it_reads_earliest(
        self, broker: FakeKafkaBroker
    ) -> None:
        _produce(broker, 0, 1000, 2000)

        assert await _read(broker, [0], reset="earliest") == {"record-events-0": 1000.0}

    async def test_a_group_that_never_committed_owes_nothing_when_it_reads_latest(
        self, broker: FakeKafkaBroker
    ) -> None:
        """Its consumer starts at the end of the log, so what is there is not its work."""
        _produce(broker, 0, 1000, 2000)

        assert await _read(broker, [0], reset="latest") == {}

    async def test_a_committed_offset_aged_out_of_the_log_falls_to_the_oldest_record_left(
        self, broker: FakeKafkaBroker
    ) -> None:
        _produce(broker, 0, 1000, 2000, 3000, 4000)
        _commit(broker, 0, 1)
        broker.log_start[TopicPartition(TOPIC, 0)] = 2

        assert await _read(broker, [0]) == {"record-events-0": 3000.0}

    async def test_it_looks_without_joining_committing_or_staying(self, broker: FakeKafkaBroker) -> None:
        _produce(broker, 0, 1000, 2000)
        _commit(broker, 0, 1)

        await _read(broker, [0])

        (probe,) = broker.consumers
        assert probe.group_id == GROUP
        assert probe.assigned_by_hand == [TopicPartition(TOPIC, 0)], "assigned by hand, never subscribed"
        assert probe.kwargs["enable_auto_commit"] is False
        assert probe.commit_calls == []
        assert broker.committed[GROUP] == {TopicPartition(TOPIC, 0): 1}
        assert probe.stopped

    async def test_a_partition_that_is_behind_but_cannot_be_read_is_an_error(
        self, broker: FakeKafkaBroker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Never mistaken for a partition that is caught up."""
        _produce(broker, 0, 1000, 2000)
        _commit(broker, 0, 1)
        monkeypatch.setattr(FakeAIOKafkaConsumer, "_fetch", lambda self, *_args: {})

        with pytest.raises(TimeoutError):
            await read_partition_backlog(
                CLIENT, TOPIC, [0], auto_offset_reset="earliest", timeout_seconds=0.3
            )

        assert broker.consumers[0].stopped

    async def test_a_record_without_a_timestamp_is_an_error(self, broker: FakeKafkaBroker) -> None:
        _produce(broker, 0, -1)

        with pytest.raises(RuntimeError, match="no timestamp"):
            await _read(broker, [0])

        assert broker.consumers[0].stopped


def _consumer_config(**overrides: object) -> KafkaConsumerConfig:
    fields: dict = {
        "topics": [TOPIC],
        "client_id": "indexing-1",
        "group_id": GROUP,
        "auto_offset_reset": "earliest",
        "enable_auto_commit": False,
        "bootstrap_servers": ["kafka:9092"],
    }
    fields.update(overrides)
    return KafkaConsumerConfig(**fields)


class TestIndexingConsumerLaneBacklog:
    def _consumer(self, broker: FakeKafkaBroker, **overrides: object) -> IndexingKafkaConsumer:
        consumer = IndexingKafkaConsumer(logging.getLogger("test"), _consumer_config(**overrides))
        # The live, subscribed consumer: only its topic metadata is used.
        consumer.consumer = FakeAIOKafkaConsumer(broker, (TOPIC,), group_id=GROUP)
        return consumer

    async def test_an_event_may_be_waiting_on_any_partition(self, broker: FakeKafkaBroker) -> None:
        """The broker's partitioner placed it; nothing here recomputes where."""
        _produce(broker, 0, 1000, 2000)
        _produce(broker, 1, 500, 600)
        _produce(broker, 2, 9000)
        _commit(broker, 0, 2)
        _commit(broker, 1, 1)
        consumer = self._consumer(broker)

        backlog = await consumer.lane_backlog(TOPIC)

        assert backlog.oldest_waiting_ms == {"record-events-1": 600.0, "record-events-2": 9000.0}
        assert backlog.oldest_waiting_for({"connectorId": "gitlab-1"}) == 600.0
        assert backlog.oldest_waiting_for({}) == 600.0

    async def test_nothing_is_waiting_once_every_partition_is_caught_up(self, broker: FakeKafkaBroker) -> None:
        _produce(broker, 0, 1000)
        _commit(broker, 0, 1)

        backlog = await self._consumer(broker).lane_backlog(TOPIC)

        assert backlog.oldest_waiting_for({"connectorId": "gitlab-1"}) is None

    async def test_the_group_s_own_reset_policy_decides_an_uncommitted_partition(
        self, broker: FakeKafkaBroker
    ) -> None:
        _produce(broker, 0, 1000)

        backlog = await self._consumer(broker, auto_offset_reset="latest").lane_backlog(TOPIC)

        assert backlog.oldest_waiting_ms == {}

    async def test_the_probe_connects_as_the_same_group_with_the_same_security_settings(
        self, broker: FakeKafkaBroker
    ) -> None:
        _produce(broker, 0, 1000)
        consumer = self._consumer(
            broker, ssl=True, sasl={"username": "svc", "password": "secret", "mechanism": "scram-sha-512"}
        )

        await consumer.lane_backlog(TOPIC)

        (probe,) = broker.consumers
        assert probe.kwargs["group_id"] == GROUP
        assert probe.kwargs["client_id"] == "indexing-1-backlog"
        assert probe.kwargs["security_protocol"] == "SASL_SSL"
        assert probe.kwargs["sasl_plain_username"] == "svc"
        assert probe.kwargs["enable_auto_commit"] is False
        assert "topics" not in probe.kwargs

    async def test_a_consumer_that_has_not_started_cannot_answer(self, broker: FakeKafkaBroker) -> None:
        consumer = self._consumer(broker)
        consumer.consumer = None

        with pytest.raises(RuntimeError, match="not started"):
            await consumer.lane_backlog(TOPIC)

    async def test_a_topic_without_partition_metadata_cannot_be_answered(self, broker: FakeKafkaBroker) -> None:
        with pytest.raises(RuntimeError, match="No partition metadata"):
            await self._consumer(broker).lane_backlog("some-other-topic")
