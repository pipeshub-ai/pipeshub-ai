"""Reading past a connector that is at its cap, on Kafka.

A partition is a lane, and a lane is shared by several connectors. When one of
them has a large backlog at the head of the lane, its messages fill its share
of the scheduler buffer and then, parked in memory with their whole payload,
the rest of the buffer, and reading stops. Another connector's messages further
down the same partition are never read until that backlog drains.

Instead, a message whose connector is at its cap is remembered by position
only -- partition and offset -- and reading carries on. Its offset stays
tracked in the commit watermark, so nothing is committed past it. When the
connector has room again, its remembered messages are fetched back, oldest
first, by a reader of its own that is assigned partitions by hand: it never
joins the consumer group, never commits, and never moves the main consumer's
position.
"""
from __future__ import annotations

import asyncio
from collections import deque
from typing import TYPE_CHECKING, Any

from aiokafka import AIOKafkaConsumer, TopicPartition  # type: ignore

if TYPE_CHECKING:
    from collections.abc import Iterable
    from logging import Logger

    from aiokafka.structs import ConsumerRecord  # type: ignore

    from app.services.messaging.scheduling.interface import FairnessKey

__all__ = ["OffsetFetcher", "RememberedOffsets"]

Position = tuple["TopicPartition", int]


class RememberedOffsets:
    """Per connector, the positions of its messages read but not yet buffered,
    oldest first, within one budget across every connector.

    Main-loop only, like the scheduler and the offset tracker.
    """

    def __init__(self, budget: int) -> None:
        self.budget = max(0, budget)
        self._by_entity: dict[FairnessKey, deque[Position]] = {}
        self._total = 0

    @property
    def total(self) -> int:
        return self._total

    @property
    def has_room(self) -> bool:
        return self._total < self.budget

    def holds(self, entity: FairnessKey) -> bool:
        return bool(self._by_entity.get(entity))

    def count(self, entity: FairnessKey) -> int:
        queue = self._by_entity.get(entity)
        return len(queue) if queue else 0

    def entities(self) -> list[FairnessKey]:
        return list(self._by_entity)

    def remember(self, entity: FairnessKey, tp: TopicPartition, offset: int) -> bool:
        """False when the budget is spent; the caller falls back to stopping the lane."""
        if not self.has_room:
            return False
        self._by_entity.setdefault(entity, deque()).append((tp, offset))
        self._total += 1
        return True

    def peek(self, entity: FairnessKey, count: int) -> list[Position]:
        queue = self._by_entity.get(entity)
        if not queue or count <= 0:
            return []
        return [queue[i] for i in range(min(count, len(queue)))]

    def head(self, entity: FairnessKey) -> Position | None:
        queue = self._by_entity.get(entity)
        return queue[0] if queue else None

    def pop(self, entity: FairnessKey) -> Position:
        queue = self._by_entity[entity]
        position = queue.popleft()
        self._total -= 1
        if not queue:
            del self._by_entity[entity]
        return position

    def restore_front(self, entity: FairnessKey, positions: list[Position]) -> None:
        """Put taken positions back at the head, in their order."""
        queue = self._by_entity.setdefault(entity, deque())
        queue.extendleft(reversed(positions))
        self._total += len(positions)

    def drop_partitions(self, partitions: Iterable[TopicPartition]) -> int:
        """Forget every position on these partitions (revoked: their new owner
        reads them again from the committed offset). Returns how many."""
        gone = set(partitions)
        dropped = 0
        for entity in list(self._by_entity):
            queue = self._by_entity[entity]
            kept = deque(p for p in queue if p[0] not in gone)
            dropped += len(queue) - len(kept)
            if kept:
                self._by_entity[entity] = kept
            else:
                del self._by_entity[entity]
        self._total -= dropped
        return dropped

    def clear(self) -> None:
        self._by_entity.clear()
        self._total = 0


class OffsetFetcher:
    """Reads chosen offsets back from Kafka without touching the group.

    One consumer, started on first use and kept, assigned by hand to whichever
    partitions a fetch needs. ``group_id`` is None, so it cannot join the group,
    trigger a rebalance or commit anything.
    """

    _MAX_RECORDS_PER_FETCH = 500

    def __init__(
        self,
        client_config: dict[str, Any],
        logger: Logger,
        *,
        poll_timeout_ms: int = 1000,
    ) -> None:
        self._client_config = client_config
        self._logger = logger
        self._poll_timeout_ms = poll_timeout_ms
        self._consumer: AIOKafkaConsumer | None = None

    async def _reader(self) -> AIOKafkaConsumer:
        if self._consumer is None:
            consumer = AIOKafkaConsumer(
                **self._client_config,
                group_id=None,
                enable_auto_commit=False,
                auto_offset_reset="earliest",
            )
            await consumer.start()
            self._consumer = consumer
        return self._consumer

    async def fetch(
        self, wanted: dict[TopicPartition, list[int]]
    ) -> tuple[dict[Position, ConsumerRecord], set[Position]]:
        """The records at these offsets, and the offsets that no longer exist.

        An offset below the partition's first retained offset has been deleted
        by retention, and so has one a fetch stepped over. An offset that could
        not be read in time is in neither result and is simply asked for again.
        """
        found: dict[Position, ConsumerRecord] = {}
        gone: set[Position] = set()
        if not wanted:
            return found, gone
        reader = await self._reader()
        partitions = sorted(wanted, key=lambda tp: (tp.topic, tp.partition))
        reader.assign(partitions)
        beginning = await reader.beginning_offsets(partitions)
        for tp in partitions:
            offsets = sorted(set(wanted[tp]))
            first_kept = beginning.get(tp, 0)
            for offset in offsets:
                if offset < first_kept:
                    gone.add((tp, offset))
            pending = [o for o in offsets if o >= first_kept]
            while pending:
                start = pending[0]
                reader.seek(tp, start)
                batch = await reader.getmany(
                    tp,
                    timeout_ms=self._poll_timeout_ms,
                    max_records=min(self._MAX_RECORDS_PER_FETCH, pending[-1] - start + 1),
                )
                records = batch.get(tp) or []
                if not records:
                    break
                by_offset = {record.offset: record for record in records}
                last = records[-1].offset
                still: list[int] = []
                for offset in pending:
                    if offset in by_offset:
                        found[(tp, offset)] = by_offset[offset]
                    elif offset <= last:
                        # Stepped over: deleted between the retention check
                        # and the fetch, or never there.
                        gone.add((tp, offset))
                    else:
                        still.append(offset)
                pending = still
        return found, gone

    async def close(self) -> None:
        consumer, self._consumer = self._consumer, None
        if consumer is not None:
            try:
                await asyncio.wait_for(consumer.stop(), timeout=10.0)
            except Exception as e:
                self._logger.debug("Could not stop the fetch-back reader cleanly: %s", e)
