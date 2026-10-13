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
A consumer given partitions with ``assign`` reads exactly those and, as in
aiokafka, can look up the group's committed offsets without joining the group.
An empty ``getmany`` waits up to ``timeout_ms`` for a record, as a real poll
does, and returns as soon as one is produced. Returning at once instead lets
a consumer loop spin on the event loop and starve its own worker thread of
the GIL, which made timing-based tests flaky on slow runners.
Records are real ``aiokafka.structs.ConsumerRecord`` objects, so the code
under test sees the same shape it gets in production.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from aiokafka.structs import ConsumerRecord, TopicPartition

if TYPE_CHECKING:
    from collections.abc import Callable


class FakeKafkaBroker:
    """Partition logs plus committed offsets per consumer group."""

    def __init__(self) -> None:
        self.logs: dict[TopicPartition, list[bytes]] = defaultdict(list)
        self.timestamps: dict[TopicPartition, list[int]] = defaultdict(list)
        # First offset still retained; lower ones have been aged out of the log.
        self.log_start: dict[TopicPartition, int] = defaultdict(int)
        self.committed: dict[str, dict[TopicPartition, int]] = defaultdict(dict)
        self.consumers: list[FakeAIOKafkaConsumer] = []
        self._waiters: set[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = set()

    def produce(
        self, topic: str, value: dict | str | bytes, partition: int = 0, timestamp_ms: int = 0
    ) -> int:
        """Append one record and return its offset. Dicts are sent as JSON."""
        if isinstance(value, dict):
            value = json.dumps(value).encode("utf-8")
        elif isinstance(value, str):
            value = value.encode("utf-8")
        tp = TopicPartition(topic, partition)
        self.logs[tp].append(value)
        self.timestamps[tp].append(timestamp_ms)
        self.notify()
        return len(self.logs[tp]) - 1

    def notify(self) -> None:
        """Wake every poll waiting for records. Safe from any thread."""
        for loop, event in list(self._waiters):
            with suppress(RuntimeError):  # that poll's loop has closed
                loop.call_soon_threadsafe(event.set)

    async def wait_for_records(self, timeout: float) -> None:
        loop = asyncio.get_running_loop()
        waiter = (loop, asyncio.Event())
        self._waiters.add(waiter)
        try:
            with suppress(TimeoutError):
                await asyncio.wait_for(waiter[1].wait(), timeout)
        finally:
            self._waiters.discard(waiter)

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
        self.assigned_by_hand: list[TopicPartition] | None = None
        self.position: dict[TopicPartition, int] = {}
        self.paused_partitions: set[TopicPartition] = set()
        self.started = False
        self.stopped = False
        self.commit_calls: list[dict[TopicPartition, int]] = []

    def _assigned(self) -> list[TopicPartition]:
        if self.assigned_by_hand is not None:
            return list(self.assigned_by_hand)
        return [tp for tp in list(self.broker.logs) if tp.topic in self.topics]

    def subscribe(self, topics: list[str], listener: object = None) -> None:
        self.topics = list(topics)
        self.listener = listener

    def assign(self, partitions: list[TopicPartition]) -> None:
        """Manual assignment: these partitions, and no group membership."""
        self.assigned_by_hand = list(partitions)

    def partitions_for_topic(self, topic: str) -> set[int] | None:
        partitions = {tp.partition for tp in list(self.broker.logs) if tp.topic == topic}
        return partitions or None

    async def committed(self, tp: TopicPartition) -> int | None:
        return self.broker.committed[self.group_id].get(tp)

    async def end_offsets(self, partitions: list[TopicPartition]) -> dict[TopicPartition, int]:
        return {tp: len(self.broker.logs[tp]) for tp in partitions}

    async def beginning_offsets(self, partitions: list[TopicPartition]) -> dict[TopicPartition, int]:
        return {tp: self.broker.log_start[tp] for tp in partitions}

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
        self.broker.notify()

    def paused(self) -> set[TopicPartition]:
        return set(self.paused_partitions)

    def seek(self, tp: TopicPartition, offset: int) -> None:
        self.position[tp] = offset
        self.broker.notify()

    async def commit(self, offsets: dict[TopicPartition, int] | None = None) -> None:
        offsets = dict(offsets or {})
        self.commit_calls.append(offsets)
        self.broker.committed[self.group_id].update(offsets)

    async def getmany(
        self, *partitions: TopicPartition, timeout_ms: int = 0, max_records: int | None = None
    ) -> dict[TopicPartition, list[ConsumerRecord]]:
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while True:
            batch = self._fetch(max_records, set(partitions))
            remaining = deadline - asyncio.get_running_loop().time()
            if batch or remaining <= 0:
                break
            await self.broker.wait_for_records(remaining)
        if not batch:
            await asyncio.sleep(0)
        return batch

    def _fetch(
        self, max_records: int | None, only: set[TopicPartition] | None = None
    ) -> dict[TopicPartition, list[ConsumerRecord]]:
        batch: dict[TopicPartition, list[ConsumerRecord]] = {}
        budget = max_records if max_records is not None else 10**9
        for tp in self._assigned():
            if tp in self.paused_partitions or budget <= 0 or (only and tp not in only):
                continue
            start = self.position.get(tp)
            if start is None:
                start = self.broker.committed[self.group_id].get(tp, 0)
            if start < self.broker.log_start[tp]:
                # Out of range: the reset policy decides, as on a real broker.
                if self.kwargs.get("auto_offset_reset") == "earliest":
                    start = self.broker.log_start[tp]
                else:
                    start = len(self.broker.logs[tp])
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
                    timestamp=self.broker.timestamps[tp][offset],
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
        return batch
