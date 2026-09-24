"""Kafka per-partition commit watermark for out-of-order dispatch.

**Why this exists (correctness, not optimization):** the DRR scheduler can
dispatch offset 52 from a partition before offset 50, which is still
buffered, has been dispatched. Naively committing ``offset + 1`` per
completed message would then commit past offset 50 while it is still
sitting in the scheduler -- on a crash or rebalance the consumer group
resumes from the committed offset and offset 50 is silently lost. This
tracks every offset that has entered the scheduler and only reports a new
committable watermark once every offset up to it has reached a terminal
outcome.

Every tracked offset must reach exactly one of two ends:

- :meth:`mark_done` -- this delivery is finished with (processed, re-queued,
  dead-lettered, or superseded by a duplicate delivery that holds the
  record lease). The watermark may advance past it.
- :meth:`mark_redeliver` -- this delivery did *not* finish and must be
  redelivered. The offset becomes a floor: the watermark stops at it until
  the offset is read again and re-tracked.

An offset that reaches neither pins the watermark forever, so
:meth:`stale_offsets` exists as the last-resort escape: the consumer sweeps
for offsets held longer than a dwell budget and force-resolves them rather
than letting one stuck delivery stall every commit on the partition.

That escape is deliberately narrow. It applies only to offsets that were
actually **dispatched** (:meth:`mark_dispatched`) and never came back --
the pin scenario. An offset that is merely buffered awaiting its turn, or
that was refused for want of buffer room and seeked back for re-reading, is
not stuck: it floors the watermark, but force-committing past it would skip
a message that was never processed at all.

Single-threaded contract: like ``DRRScheduler``, every method here is called
only from the consumer's main event loop -- the Kafka consumer bridges
resolution calls made from the worker thread onto the main loop before
touching this class (mirroring the existing ``_commit_offset`` pattern), so
no internal locking is needed.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from logging import Logger
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from aiokafka import TopicPartition  # type: ignore

__all__ = ["PartitionOffsetTracker", "StaleOffset"]


class StaleOffset(NamedTuple):
    tp: "TopicPartition"
    offset: int
    age_seconds: float


class PartitionOffsetTracker:
    """Tracks in-flight offsets per partition and computes the commit
    watermark: the highest offset it is safe to commit ``+ 1``.
    """

    def __init__(
        self,
        logger: Logger | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._logger = logger
        self._clock = clock
        # offset -> when it was dispatched, or None while it is only
        # buffered / awaiting re-read. Both float the watermark; only the
        # dispatched ones are candidates for the dwell sweep.
        self._outstanding: dict["TopicPartition", dict[int, float | None]] = {}
        # Offsets that must be redelivered: they floor the watermark exactly
        # like an outstanding offset, but they are not waiting on any
        # in-flight work, so the dwell sweep reports them separately.
        self._redeliver: dict["TopicPartition", dict[int, float]] = {}
        self._high_water_seen: dict["TopicPartition", int] = {}
        self._committed: dict["TopicPartition", int] = {}

    def track(self, tp: "TopicPartition", offset: int) -> None:
        """Record that ``offset`` has entered the scheduler buffer (or is
        about to be resolved inline, e.g. an unparseable message) and is not
        yet safe to commit past.

        Clears any redelivery floor for the offset and resets it to
        not-yet-dispatched: a re-read is a fresh delivery attempt, so it must
        not inherit the dwell clock of the attempt before it.
        """
        self._outstanding.setdefault(tp, {})[offset] = None
        redeliver = self._redeliver.get(tp)
        if redeliver is not None:
            redeliver.pop(offset, None)
        self._high_water_seen[tp] = max(self._high_water_seen.get(tp, -1), offset)

    def mark_dispatched(self, tp: "TopicPartition", offset: int) -> None:
        """Record that ``offset`` has been handed to a worker.

        This is what arms the dwell sweep for it. Until then the offset is
        buffered or waiting to be re-read, and a sweep must not commit past
        it -- nothing has tried to process it yet.
        """
        outstanding = self._outstanding.get(tp)
        if outstanding is not None and offset in outstanding:
            outstanding[offset] = self._clock()

    def mark_done(self, tp: "TopicPartition", offset: int) -> int | None:
        """Record that this delivery of ``offset`` is finished with.

        Returns the new committable watermark (highest contiguous resolved
        offset + 1) if it advanced past the last one reported, else ``None``
        -- so the caller only issues a commit when there is genuine progress.
        """
        self._resolve(tp, offset, "mark_done")
        return self._advance(tp)

    def mark_redeliver(
        self,
        tp: "TopicPartition",
        offset: int,
        *,
        awaiting_reread: bool = False,
    ) -> int | None:
        """Record that this delivery of ``offset`` did *not* finish and the
        message must be redelivered (shutdown, or an abandoned dispatch).

        The offset becomes a watermark floor rather than being forgotten:
        forgetting it would let a later completion commit past an unprocessed
        message and lose it.

        ``awaiting_reread`` says the caller has also rewound the partition, so
        this offset comes back on its own within this process. Such a floor
        carries no dwell clock -- it is not stuck, it is queued -- and the
        sweep must not force-commit past it, exactly as it must not for an
        offset that is merely buffered. Every other floor *does* age: on
        Kafka the read position is already past it, so it is only redelivered
        by a restart or rebalance and would otherwise pin the partition's
        commits until then, which is the situation the sweep exists for.
        """
        held_since = self._resolve(tp, offset, "mark_redeliver")
        self._redeliver.setdefault(tp, {})[offset] = (
            None if awaiting_reread else held_since
        )
        return self._advance(tp)

    def clear_partition_floor(self, tp: "TopicPartition", offset: int) -> None:
        """Drop a redelivery floor without treating the offset as resolved."""
        redeliver = self._redeliver.get(tp)
        if redeliver is not None:
            redeliver.pop(offset, None)

    def _resolve(
        self, tp: "TopicPartition", offset: int, caller: str
    ) -> float:
        """Drop ``offset`` from the outstanding set, returning when it was
        first tracked. Resolving an offset that was never tracked means a
        caller resolved something the watermark never knew about, which is
        how offsets get silently skipped -- loud rather than lenient."""
        outstanding = self._outstanding.get(tp)
        tracked = outstanding is not None and offset in outstanding
        held_since = outstanding.pop(offset, None) if outstanding is not None else None

        # A redelivery floor is cleared here too, not just the outstanding
        # entry. The dwell sweep resolves what it finds via mark_done, and a
        # floor it left in place would keep pinning the watermark -- the
        # escape hatch would report an escape without making one.
        redeliver = self._redeliver.get(tp)
        if redeliver is not None and offset in redeliver:
            floored_since = redeliver.pop(offset)
            if held_since is None:
                held_since = floored_since
            tracked = True

        self._high_water_seen[tp] = max(self._high_water_seen.get(tp, -1), offset)
        if not tracked:
            if self._logger is not None:
                self._logger.error(
                    "%s called for untracked offset %s-%s; the commit "
                    "watermark never saw this offset enter the buffer",
                    caller,
                    tp,
                    offset,
                )
        return held_since if held_since is not None else self._clock()

    def _advance(self, tp: "TopicPartition") -> int | None:
        floors: list[int] = []
        outstanding = self._outstanding.get(tp)
        if outstanding:
            floors.append(min(outstanding))
        redeliver = self._redeliver.get(tp)
        if redeliver:
            floors.append(min(redeliver))

        if floors:
            watermark = min(floors)
        else:
            watermark = self._high_water_seen.get(tp, -1) + 1

        previous = self._committed.get(tp)
        if previous is not None and watermark <= previous:
            return None
        self._committed[tp] = watermark
        return watermark

    def stale_offsets(self, max_dwell_seconds: float) -> list[StaleOffset]:
        """Dispatched-but-unresolved offsets held longer than the dwell budget.

        Only offsets that a worker actually took (``mark_dispatched``) or that
        were handed back for redelivery are candidates. Offsets still sitting
        in the buffer, or refused and seeked back for re-reading, carry no
        dwell clock: they float the watermark, but they have not been
        processed, so committing past them would skip them outright.
        """
        now = self._clock()
        stale: list[StaleOffset] = []
        for source in (self._outstanding, self._redeliver):
            for tp, offsets in source.items():
                for offset, held_since in offsets.items():
                    if held_since is None:
                        continue
                    age = now - held_since
                    if age >= max_dwell_seconds:
                        stale.append(StaleOffset(tp, offset, age))
        return stale

    def watermark_lag(self, tp: "TopicPartition") -> int:
        """Offsets read but not yet committed past, for the lag metric. A
        number that only grows is the signature of a pinned watermark.

        Measured against the *committable* floor rather than the last
        watermark actually reported: a partition that has read offsets but
        not yet resolved any of them has never reported a watermark, and
        reading ``_committed`` there would report zero lag for the very
        state the metric exists to surface.
        """
        high_water = self._high_water_seen.get(tp)
        if high_water is None:
            return 0

        floors: list[int] = []
        outstanding = self._outstanding.get(tp)
        if outstanding:
            floors.append(min(outstanding))
        redeliver = self._redeliver.get(tp)
        if redeliver:
            floors.append(min(redeliver))

        floor = min(floors) if floors else high_water + 1
        return max(0, high_water + 1 - floor)

    @property
    def tracked_partitions(self) -> list["TopicPartition"]:
        return list(self._high_water_seen.keys())

    def revoke(self, tp: "TopicPartition") -> None:
        """Drop all tracked state for a partition, e.g. on rebalance
        revocation -- its buffered offsets are being redelivered to
        whichever replica the partition lands on next."""
        self._outstanding.pop(tp, None)
        self._redeliver.pop(tp, None)
        self._high_water_seen.pop(tp, None)
        self._committed.pop(tp, None)
