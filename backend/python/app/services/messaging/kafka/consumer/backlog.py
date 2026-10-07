"""Reading a consumer group's backlog off Kafka.

See ``app.services.messaging.lanes.backlog`` for what the result means.
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from aiokafka import AIOKafkaConsumer, TopicPartition  # type: ignore

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["partition_lane", "read_partition_backlog"]

_EARLIEST = "earliest"


def partition_lane(topic: str, partition: int) -> str:
    return f"{topic}-{partition}"


async def read_partition_backlog(
    client_config: dict[str, Any],
    topic: str,
    partitions: "Iterable[int]",
    *,
    auto_offset_reset: str,
    timeout_seconds: float,
) -> dict[str, float]:
    """Per partition, the timestamp of the oldest record the group has not committed past.

    The committed offset is the first record the group has not finished with,
    so its timestamp is the age of the oldest work still waiting there. A
    partition whose committed offset is at the end of the log is left out.

    Reads through a short-lived consumer of its own, assigned by hand: it asks
    for the group's committed offsets but never joins the group, so it cannot
    trigger a rebalance, and it never commits. The live consumer's position is
    not touched. One offset lookup per partition, and one fetch per partition
    that is behind.

    Raises if any partition that is behind cannot be read in time, so a
    partial answer is never mistaken for "caught up".
    """
    deadline = time.monotonic() + timeout_seconds
    partitions_to_read = [TopicPartition(topic, p) for p in sorted(partitions)]
    probe = AIOKafkaConsumer(
        **client_config,
        enable_auto_commit=False,
        # Where the probe lands if the committed offset has already been aged
        # out of the log: the oldest record still there is the oldest waiting.
        auto_offset_reset=_EARLIEST,
    )
    # Stopped outside the time limit, so running out of time cannot leave the
    # probe half closed.
    try:
        async with asyncio.timeout(timeout_seconds):
            await probe.start()
            probe.assign(partitions_to_read)
            # Paused before the first await, so nothing is fetched until each
            # partition has been pointed at the offset that matters.
            probe.pause(*partitions_to_read)

            committed = dict(
                zip(
                    partitions_to_read,
                    await asyncio.gather(
                        *(probe.committed(tp) for tp in partitions_to_read)
                    ),
                    strict=True,
                )
            )
            end = await probe.end_offsets(partitions_to_read)
            # A partition the group has never committed on starts wherever the
            # live consumer's reset policy puts it.
            uncommitted = [tp for tp, offset in committed.items() if offset is None]
            if uncommitted and auto_offset_reset == _EARLIEST:
                start_of_log = await probe.beginning_offsets(uncommitted)
            else:
                start_of_log = {tp: end[tp] for tp in uncommitted}

            behind: set[TopicPartition] = set()
            for tp in partitions_to_read:
                offset = committed[tp] if committed[tp] is not None else start_of_log[tp]
                if offset < end[tp]:
                    probe.seek(tp, offset)
                    behind.add(tp)
            probe.resume(*behind)

            oldest: dict[str, float] = {}
            while behind:
                remaining_ms = int((deadline - time.monotonic()) * 1000)
                if remaining_ms <= 0:
                    raise TimeoutError(
                        f"No record read from {len(behind)} partition(s) of {topic} "
                        "that the group is behind on"
                    )
                batch = await probe.getmany(
                    *behind, timeout_ms=min(remaining_ms, 1000), max_records=len(behind)
                )
                for tp, records in batch.items():
                    if not records or tp not in behind:
                        continue
                    timestamp = records[0].timestamp
                    if timestamp is None or timestamp < 0:
                        raise RuntimeError(
                            f"Record at {tp.topic}-{tp.partition}@{records[0].offset} "
                            "carries no timestamp"
                        )
                    oldest[partition_lane(tp.topic, tp.partition)] = float(timestamp)
                    probe.pause(tp)
                    behind.discard(tp)
            return oldest
    finally:
        await probe.stop()
