"""WatermarkCommitter on its own: at most one commit in flight per partition, every waiter resolved on every exit.

The fake commit is gated per call on an ``asyncio.Event``, so every ordering below is forced, never timed. A bound is
0.05 s only where nothing is ever going to open the gate.
"""
from __future__ import annotations

import asyncio
import gc
import logging

import pytest
from aiokafka import TopicPartition

from app.services.messaging.kafka.consumer.watermark_committer import (
    CommitDropped,
    WatermarkCommitter,
)
from tests.unit.services.messaging.test_kafka_fair_finish_wakes_loop import _until

P0 = TopicPartition("record-events", 0)
P1 = TopicPartition("record-events", 1)
SHORT = 0.05
LONG = 30.0


class FakeBroker:
    """Each commit call is recorded when it starts and waits until its partition's gate is open."""

    def __init__(self) -> None:
        self.started: list[dict[TopicPartition, int]] = []
        self.committed: list[dict[TopicPartition, int]] = []
        self.gates: dict[TopicPartition, asyncio.Event] = {P0: asyncio.Event(), P1: asyncio.Event()}
        self.errors: dict[TopicPartition, list[Exception]] = {P0: [], P1: []}

    async def commit(self, offsets: dict[TopicPartition, int]) -> None:
        (tp,) = offsets
        self.started.append(dict(offsets))
        await self.gates[tp].wait()
        if self.errors[tp]:
            raise self.errors[tp].pop(0)
        self.committed.append(dict(offsets))


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def _ask(committer: WatermarkCommitter, tp: TopicPartition, offset: int) -> asyncio.Task[None]:
    return asyncio.ensure_future(committer.commit({tp: offset}))


async def test_waiters_during_a_commit_in_flight_share_one_next_commit_with_the_highest_value() -> None:
    broker = FakeBroker()
    committer = WatermarkCommitter(broker.commit, LONG)
    first = _ask(committer, P0, 1)
    await _until(lambda: broker.started)
    later = [_ask(committer, P0, offset) for offset in (3, 2, 5, 4, 6)]
    await _settle()
    assert broker.started == [{P0: 1}]

    broker.gates[P0].set()
    await asyncio.gather(first, *later)
    assert broker.started == [{P0: 1}, {P0: 6}]
    assert broker.committed == [{P0: 1}, {P0: 6}]


async def test_a_waiter_merged_into_the_next_commit_does_not_return_with_the_one_in_flight() -> None:
    broker = FakeBroker()
    commits_done: list[int] = []

    async def commit(offsets: dict[TopicPartition, int]) -> None:
        await broker.commit(offsets)
        commits_done.append(offsets[P0])
        if offsets[P0] == 1:
            broker.gates[P0].clear()  # hold the next one

    committer = WatermarkCommitter(commit, LONG)
    first = _ask(committer, P0, 1)
    await _until(lambda: broker.started)
    second = _ask(committer, P0, 2)
    broker.gates[P0].set()
    await first
    await _until(lambda: len(broker.started) == 2)
    assert not second.done()
    broker.gates[P0].set()
    await second
    assert commits_done == [1, 2]


async def test_partitions_commit_independently_and_fail_independently() -> None:
    broker = FakeBroker()
    committer = WatermarkCommitter(broker.commit, LONG)
    held = _ask(committer, P0, 1)
    await _until(lambda: broker.started)
    broker.errors[P1].append(RuntimeError("p1 refused"))
    other = _ask(committer, P1, 7)
    await _until(lambda: len(broker.started) == 2)
    assert broker.started[1] == {P1: 7}  # sent while p0 is still held
    broker.gates[P1].set()
    with pytest.raises(RuntimeError, match="p1 refused"):
        await other
    assert not held.done()
    broker.gates[P0].set()
    await held


async def test_a_failed_commit_fails_each_of_its_waiters_and_the_next_ask_sends_afresh(
    caplog: pytest.LogCaptureFixture,
) -> None:
    broker = FakeBroker()
    committer = WatermarkCommitter(broker.commit, LONG)
    first = _ask(committer, P0, 1)
    await _until(lambda: broker.started)
    merged = [_ask(committer, P0, 2), _ask(committer, P0, 3)]
    error = RuntimeError("commit refused")
    broker.errors[P0] += [RuntimeError("first refused"), error]
    broker.gates[P0].set()
    with pytest.raises(RuntimeError, match="first refused"):
        await first
    results = await asyncio.gather(*merged, return_exceptions=True)
    assert results == [error, error]

    await committer.commit({P0: 4})
    assert broker.started == [{P0: 1}, {P0: 3}, {P0: 4}]
    assert broker.committed == [{P0: 4}]
    with caplog.at_level(logging.ERROR, logger="asyncio"):
        gc.collect()
    assert "never retrieved" not in caplog.text


async def test_a_waiter_that_is_cancelled_cancels_neither_the_commit_nor_the_others() -> None:
    broker = FakeBroker()
    committer = WatermarkCommitter(broker.commit, LONG)
    first = _ask(committer, P0, 1)
    await _until(lambda: broker.started)
    gone, staying = _ask(committer, P0, 2), _ask(committer, P0, 3)
    await _settle()
    gone.cancel()
    first.cancel()
    broker.gates[P0].set()
    await staying
    assert broker.committed == [{P0: 1}, {P0: 3}]
    assert gone.cancelled() and first.cancelled()


async def test_close_waits_up_to_the_bound_never_cancels_the_commit_in_flight_and_drops_what_waits() -> None:
    broker = FakeBroker()
    committer = WatermarkCommitter(broker.commit, LONG)
    in_flight = _ask(committer, P0, 1)
    await _until(lambda: broker.started)
    waiting = _ask(committer, P0, 2)
    other_held = _ask(committer, P1, 5)
    await _until(lambda: len(broker.started) == 2)
    other_waiting = _ask(committer, P1, 6)
    await _settle()
    committer._timeout = SHORT  # for the close only: the waiters above are armed with the long bound

    await committer.close([P0])

    with pytest.raises(CommitDropped):
        await waiting
    assert not in_flight.done() and not other_held.done() and not other_waiting.done()
    broker.gates[P0].set()
    broker.gates[P1].set()
    await asyncio.gather(in_flight, other_held, other_waiting)
    assert {P0: 2} not in broker.started
    assert broker.committed == [{P0: 1}, {P1: 5}, {P1: 6}] or broker.committed == [{P1: 5}, {P0: 1}, {P1: 6}]


async def test_close_returns_once_the_commits_asked_for_are_sent_and_commits_them() -> None:
    broker = FakeBroker()
    committer = WatermarkCommitter(broker.commit, LONG)
    first = _ask(committer, P0, 1)
    await _until(lambda: broker.started)
    second = _ask(committer, P0, 2)
    closing = asyncio.ensure_future(committer.close(None))
    await _settle()
    assert not closing.done()
    broker.gates[P0].set()
    await closing
    assert broker.committed == [{P0: 1}, {P0: 2}]
    await asyncio.gather(first, second)


async def test_a_lower_value_asked_while_a_higher_one_is_queued_never_commits_lower() -> None:
    broker = FakeBroker()
    committer = WatermarkCommitter(broker.commit, LONG)
    first = _ask(committer, P0, 1)
    await _until(lambda: broker.started)
    later = [_ask(committer, P0, 9), _ask(committer, P0, 4)]
    broker.gates[P0].set()
    await asyncio.gather(first, *later)
    assert [c[P0] for c in broker.committed] == [1, 9]


async def test_a_sender_cancelled_mid_commit_drops_every_waiter_and_the_next_ask_sends() -> None:
    broker = FakeBroker()
    committer = WatermarkCommitter(broker.commit, LONG)
    in_flight = _ask(committer, P0, 1)
    await _until(lambda: broker.started)
    waiting = _ask(committer, P0, 2)
    await _settle()

    committer._sending[P0].cancel()

    for waiter in (in_flight, waiting):
        with pytest.raises(CommitDropped):
            await waiter
    broker.gates[P0].set()
    await committer.commit({P0: 3})
    assert broker.committed == [{P0: 3}]


async def test_a_sender_cancelled_before_its_first_step_drops_its_waiter_and_the_next_ask_sends() -> None:
    broker = FakeBroker()
    committer = WatermarkCommitter(broker.commit, LONG)
    waiter = _ask(committer, P0, 1)
    await asyncio.sleep(0)  # the ask has run; the sender it started has not
    assert broker.started == []
    committer._sending[P0].cancel()

    with pytest.raises(CommitDropped):
        await waiter
    broker.gates[P0].set()
    await committer.commit({P0: 2})
    assert broker.committed == [{P0: 2}]


async def test_an_ask_after_the_sender_ended_but_before_its_callback_ran_starts_a_new_sender() -> None:
    broker = FakeBroker()
    broker.gates[P0].set()
    committer = WatermarkCommitter(broker.commit, LONG)
    asked_in_the_window: list[asyncio.Future[None]] = []
    real_sender_done = committer._sender_done

    def ask_first(tp: TopicPartition, task: asyncio.Task[None]) -> None:
        if not asked_in_the_window:  # the sender is done; its callback has not run yet
            asked_in_the_window.append(committer._ask(P0, 2))
        real_sender_done(tp, task)

    committer._sender_done = ask_first  # type: ignore[method-assign]
    await committer.commit({P0: 1})
    await _until(lambda: asked_in_the_window)
    async with asyncio.timeout(LONG):
        await asked_in_the_window[0]
    assert broker.committed == [{P0: 1}, {P0: 2}]


async def test_a_wait_past_the_bound_raises_timeout_and_leaves_the_commit_running() -> None:
    broker = FakeBroker()
    committer = WatermarkCommitter(broker.commit, SHORT)
    with pytest.raises(TimeoutError):
        await committer.commit({P0: 1})
    assert broker.started == [{P0: 1}]
    sender = committer._sending[P0]
    assert not sender.done()
    broker.gates[P0].set()
    await sender
    assert broker.committed == [{P0: 1}]
    await committer.commit({P0: 2})
    assert broker.committed == [{P0: 1}, {P0: 2}]


async def test_a_reply_that_lands_with_the_timer_counts_as_committed(monkeypatch: pytest.MonkeyPatch) -> None:
    # A frozen loop wakes with the timer and the reply both due; the timer can win.
    broker = FakeBroker()
    broker.gates[P0].set()
    committer = WatermarkCommitter(broker.commit, LONG)
    real_wait_for = asyncio.wait_for

    async def timer_wins(awaitable, timeout):  # noqa: ANN202
        await real_wait_for(awaitable, timeout)
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", timer_wins)
    await committer.commit({P0: 1})  # does not raise
    assert broker.committed == [{P0: 1}]


async def test_a_commit_the_owner_refuses_as_dropped_resolves_its_waiters_with_it() -> None:
    async def stopped(_offsets: dict[TopicPartition, int]) -> None:
        raise CommitDropped

    committer = WatermarkCommitter(stopped, LONG)
    with pytest.raises(CommitDropped):
        await committer.commit({P0: 1})
    assert committer._sending == {} or committer._sending[P0].done()


async def test_nothing_to_commit_returns_at_once() -> None:
    committer = WatermarkCommitter(FakeBroker().commit, SHORT)
    await committer.commit({})
    await committer.close(None)
