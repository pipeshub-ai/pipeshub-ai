"""In-memory stand-in for a Kafka broker and ``aiokafka.AIOKafkaConsumer``.

Mocking ``getmany``/``commit`` call by call cannot catch delivery bugs,
because the bugs live in how the broker behaves between calls. This fake
keeps the three rules that matter, as aiokafka implements them:

- ``getmany`` hands out records from the consumer's *position* and moves the
  position past them straight away. Nothing is redelivered on the next poll
  just because it was not committed.
- ``commit`` only records the group's committed offset. It never moves the
  position.
- A consumer that starts (a restart, or another replica after a rebalance)
  begins at the group's committed offset, or at the start of the log.

``seek`` moves the position, which is how a consumer asks for a redelivery.
Records are real ``aiokafka.structs.ConsumerRecord`` objects, so the code
under test sees the same shape it gets in production.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from aiokafka.structs import ConsumerRecord, TopicPartition

if TYPE_CHECKING:
    from collections.abc import Callable


class FakeKafkaBroker:
    """Partition logs plus committed offsets per consumer group."""

    def __init__(self) -> None:
        self.logs: dict[TopicPartition, list[bytes]] = defaultdict(list)
        self.committed: dict[str, dict[TopicPartition, int]] = defaultdict(dict)
        self.consumers: list[FakeAIOKafkaConsumer] = []

    def produce(self, topic: str, value: dict | str | bytes, partition: int = 0) -> int:
        """Append one record and return its offset. Dicts are sent as JSON."""
        if isinstance(value, dict):
            value = json.dumps(value).encode("utf-8")
        elif isinstance(value, str):
            value = value.encode("utf-8")
        tp = TopicPartition(topic, partition)
        self.logs[tp].append(value)
        return len(self.logs[tp]) - 1

    def committed_offset(self, group_id: str, topic: str, partition: int = 0) -> int | None:
        return self.committed[group_id].get(TopicPartition(topic, partition))

    def consumer_factory(self) -> Callable[..., FakeAIOKafkaConsumer]:
        """A drop-in for the ``AIOKafkaConsumer`` class, bound to this broker."""
        broker = self

        def factory(*topics: str, **kwargs: Any) -> FakeAIOKafkaConsumer:  # noqa: ANN401 - mirrors AIOKafkaConsumer's kwargs
            consumer = FakeAIOKafkaConsumer(broker, topics, **kwargs)
            broker.consumers.append(consumer)
            return consumer

        return factory


class FakeAIOKafkaConsumer:
    def __init__(self, broker: FakeKafkaBroker, topics: tuple[str, ...], **kwargs: Any) -> None:  # noqa: ANN401
        self.broker = broker
        self.topics = list(topics)
        self.group_id: str = kwargs["group_id"]
        self.kwargs = kwargs
        self.position: dict[TopicPartition, int] = {}
        self.paused_partitions: set[TopicPartition] = set()
        self.started = False
        self.stopped = False
        self.commit_calls: list[dict[TopicPartition, int]] = []

    def _assigned(self) -> list[TopicPartition]:
        return [tp for tp in list(self.broker.logs) if tp.topic in self.topics]

    def subscribe(self, topics: list[str], listener: object = None) -> None:
        self.topics = list(topics)
        self.listener = listener

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    def assignment(self) -> set[TopicPartition]:
        return set(self._assigned())

    def pause(self, *partitions: TopicPartition) -> None:
        self.paused_partitions.update(partitions)

    def resume(self, *partitions: TopicPartition) -> None:
        self.paused_partitions.difference_update(partitions)

    def paused(self) -> set[TopicPartition]:
        return set(self.paused_partitions)

    def seek(self, tp: TopicPartition, offset: int) -> None:
        self.position[tp] = offset

    async def commit(self, offsets: dict[TopicPartition, int] | None = None) -> None:
        offsets = dict(offsets or {})
        self.commit_calls.append(offsets)
        self.broker.committed[self.group_id].update(offsets)

    async def getmany(
        self, *partitions: TopicPartition, timeout_ms: int = 0, max_records: int | None = None
    ) -> dict[TopicPartition, list[ConsumerRecord]]:
        batch: dict[TopicPartition, list[ConsumerRecord]] = {}
        budget = max_records if max_records is not None else 10**9
        for tp in self._assigned():
            if tp in self.paused_partitions or budget <= 0:
                continue
            start = self.position.get(tp)
            if start is None:
                start = self.broker.committed[self.group_id].get(tp, 0)
            log = self.broker.logs[tp]
            end = min(len(log), start + budget)
            if end <= start:
                self.position[tp] = start
                continue
            batch[tp] = [
                ConsumerRecord(
                    topic=tp.topic,
                    partition=tp.partition,
                    offset=offset,
                    timestamp=0,
                    timestamp_type=0,
                    key=None,
                    value=log[offset],
                    checksum=None,
                    serialized_key_size=0,
                    serialized_value_size=len(log[offset]),
                    headers=(),
                )
                for offset in range(start, end)
            ]
            budget -= end - start
            self.position[tp] = end
        if not batch:
            # A real poll blocks for up to timeout_ms; yield so the test's
            # event loop keeps moving without spinning.
            await asyncio.sleep(0)
        return batch
