"""Kafka offset commits, at most one in flight per partition.

aiokafka sends one commit at a time per consumer (``GroupCoordinator._commit_lock``), so committing once per finished
record queues commits without bound when records finish faster than the broker answers: under parallel dispatch the
queue reached thousands, and every record waited seconds for its own commit. Here a watermark asked for while its
partition's commit is in flight waits for the next commit, which carries the highest watermark asked for meanwhile, so
a partition never has more than one commit in flight and one waiting.

Callers pass watermarks from ``PartitionOffsetTracker``: this decides when to commit, never what is safe to commit.
Main loop only, like the tracker.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.core.org_context import spawn_detached

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection

    from aiokafka import TopicPartition  # type: ignore

__all__ = ["CommitDropped", "WatermarkCommitter"]


class CommitDropped(Exception):
    """Given up before it was sent (its partition was revoked or the consumer stopped): nothing was committed."""


def _fail(future: asyncio.Future[None], error: BaseException) -> None:
    if not future.done():
        future.set_exception(error)
        future.exception()  # retrieved: a waiter that gave up must not leave "exception was never retrieved"


class WatermarkCommitter:
    def __init__(self, commit: Callable[[dict[TopicPartition, int]], Awaitable[object]], timeout: float) -> None:
        self._commit = commit
        self._timeout = timeout
        self._next: dict[TopicPartition, tuple[int, asyncio.Future[None]]] = {}
        self._sending: dict[TopicPartition, asyncio.Task[None]] = {}

    async def commit(self, offsets: dict[TopicPartition, int]) -> None:
        """Return once each partition has committed at or past its offset. Raises the first failure, CommitDropped,
        or TimeoutError after the bound; the commit itself carries on."""
        asked = [self._ask(tp, offset) for tp, offset in offsets.items()]
        try:
            await asyncio.wait_for(asyncio.gather(*(asyncio.shield(f) for f in asked)), self._timeout)
        except TimeoutError:
            # A frozen loop wakes with the timer and the replies both due, and the timer can win (as in loop_bridge).
            if all(f.done() and f.exception() is None for f in asked):
                return
            raise

    async def close(self, partitions: Collection[TopicPartition] | None) -> None:
        """Wait up to the bound for the commits asked for on ``partitions`` (all when None), then drop what is still
        waiting: sent later, it would carry an old assignment's watermark under the next generation. The commit in
        flight cannot be recalled and is never cancelled."""
        senders = [task for tp, task in self._sending.items() if partitions is None or tp in partitions]
        if senders:
            await asyncio.wait(senders, timeout=self._timeout)
        for tp in [tp for tp in self._next if partitions is None or tp in partitions]:
            _fail(self._next.pop(tp)[1], CommitDropped())

    def _ask(self, tp: TopicPartition, offset: int) -> asyncio.Future[None]:
        queued = self._next.get(tp)
        future = queued[1] if queued else asyncio.get_running_loop().create_future()
        self._next[tp] = (max(offset, queued[0]) if queued else offset, future)
        sender = self._sending.get(tp)
        # done(), not presence: a sender that ended (or was cancelled before its first step) has not run its
        # done-callback yet, and an ask in that window must start a new one.
        if sender is None or sender.done():
            sender = self._sending[tp] = spawn_detached(self._send(tp), name="kafka-commit")
            sender.add_done_callback(lambda task, tp=tp: self._sender_done(tp, task))
        return future

    async def _send(self, tp: TopicPartition) -> None:
        sent: asyncio.Future[None] | None = None
        try:
            while (queued := self._next.pop(tp, None)) is not None:
                offset, sent = queued
                try:
                    await self._commit({tp: offset})
                except Exception as e:
                    _fail(sent, e)
                else:
                    sent.set_result(None)
                sent = None
        finally:
            if sent is not None:  # cancelled mid-commit
                _fail(sent, CommitDropped())

    def _sender_done(self, tp: TopicPartition, task: asyncio.Task[None]) -> None:
        # Also runs for a sender cancelled before its first step, whose body (and finally) never ran.
        if self._sending.get(tp) is not task:
            return  # a newer sender owns the partition
        del self._sending[tp]
        if task.cancelled() and tp in self._next:
            _fail(self._next.pop(tp)[1], CommitDropped())
