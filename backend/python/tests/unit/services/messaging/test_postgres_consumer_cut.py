"""B15 (S-3a): the PostgreSQL indexing consumer cuts a record's attempt at its budget and resumes it, as Kafka and Redis Streams do.

The handler runs on the worker loop and the budget lives on the claim loop, so these tests use a real worker loop.
"""

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from logging import getLogger
from types import SimpleNamespace

import pytest

from app.modules.indexing.record_progress import current_progress
from app.services.messaging import record_budget
from app.services.messaging.config import (
    IndexingEvent,
    RedisStreamsConfig,
    messaging_env,
)
from app.services.messaging.consumer_concurrency import ProgressRequeue, StageAdmission
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.worker_loop import WorkerLoop

LOG = getLogger("pg-consumer-cut")
CFG = RedisStreamsConfig(topics=["t"], group_id="g")
BUDGET_S = 0.4


def _parsed() -> SimpleNamespace:
    return SimpleNamespace(payload={}, org_ctx=None, requestId="r")


class _Leases:
    """Records the lease traffic of one delivery."""

    def __init__(self, order: list | None = None) -> None:
        self.acquired: list[tuple[str, str]] = []
        self.released: list[tuple[str, str]] = []
        self.order = order
        self.lost_pools: set[str] = set()   # pools whose lease the manager reports as no longer ours

    async def try_acquire(self, pool: str, holder: str, *_a: object) -> bool:
        self.acquired.append((pool, holder))
        return True

    async def release(self, pool: str, holder: str) -> None:
        self.released.append((pool, holder))
        if self.order is not None:
            self.order.append("release")

    async def renew_many(self, leases: list, _ttl: float) -> dict:
        return {lease: lease[0] not in self.lost_pools for lease in leases}


@pytest.fixture
def worker() -> Iterator[WorkerLoop]:
    w = WorkerLoop(LOG, thread_name="test-cut-worker")
    yield w
    w.stop()


@pytest.fixture(autouse=True)
def short_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(messaging_env.__class__, "record_processing_timeout_for", lambda *_a, **_k: BUDGET_S, raising=False)


def _consumer(worker, handler, order: list | None = None) -> PgMessagingConsumer:
    c = PgMessagingConsumer(LOG, CFG, indexing=True, worker=worker)
    c.message_handler = handler
    c.concurrency_manager = _Leases(order)  # type: ignore[assignment]
    c.running = True
    return c


def _embedding_handler(
    *, beats: int, commits: list, order: list | None = None, unwind_s: float = 0.0, seen_threads: list | None = None
) -> Callable[[object], AsyncIterator[SimpleNamespace]]:
    """A handler that embeds ``beats`` batches of the record's points and then never finishes."""

    async def commit(seconds: float, uncounted: int) -> bool:
        commits.append((seconds, uncounted))
        if order is not None:
            order.append("commit")
        return True

    async def handler(_msg: object) -> AsyncIterator[SimpleNamespace]:
        p = current_progress()
        assert p is not None, "the worker loop's task has the record's progress"
        p.bind(record_id="rec", delivery_id="d1", prior_seconds=0.0, prior_uncounted=0, commit=commit)
        p.plan(10, 0)
        for _ in range(beats):
            p.beat(1)
            if seen_threads is not None:
                seen_threads.append(threading.current_thread().name)
        try:
            await asyncio.sleep(30)
        finally:
            if unwind_s:
                await asyncio.sleep(unwind_s)
            if order is not None:
                order.append("unwound")
        yield SimpleNamespace(event=IndexingEvent.INDEXING_COMPLETE)

    return handler


async def test_a_cut_after_progress_resumes_without_a_counted_attempt(worker) -> None:
    commits: list = []
    c = _consumer(worker, _embedding_handler(beats=9, commits=commits))  # about 90 % of the record embedded
    with pytest.raises(ProgressRequeue) as hand_back:
        await c._drive_indexing(_parsed(), "message:1")
    assert "9 of 10" in str(hand_back.value)
    assert [u for _s, u in commits] == [1]   # the conditional hand-back write, one uncounted cut on the chain


async def test_a_cut_without_progress_stays_a_counted_timeout(worker) -> None:
    commits: list = []
    c = _consumer(worker, _embedding_handler(beats=0, commits=commits))
    with pytest.raises(TimeoutError):
        await c._drive_indexing(_parsed(), "message:1")
    assert commits == []


async def test_the_cut_is_settled_after_the_handler_has_unwound(worker) -> None:
    commits: list = []
    order: list = []
    c = _consumer(worker, _embedding_handler(beats=3, commits=commits, order=order, unwind_s=0.3))
    with pytest.raises(ProgressRequeue):
        await c._drive_indexing(_parsed(), "message:1")
    assert order == ["unwound", "commit"]    # the claim loop read the progress only after drain() ended


async def test_the_leases_are_released_on_a_cut(worker) -> None:
    c = _consumer(worker, _embedding_handler(beats=2, commits=[]))
    with pytest.raises(ProgressRequeue):
        await c._drive_indexing(_parsed(), "message:1")
    leases = c.concurrency_manager
    assert leases.acquired and sorted(leases.released) == sorted(leases.acquired)  # type: ignore[attr-defined]
    assert not c._leases


async def test_a_lost_record_lease_is_not_a_cut(worker) -> None:
    c = _consumer(worker, _embedding_handler(beats=2, commits=(commits := [])))
    real = c._take_record_lease

    async def take(parsed: object, key: str) -> tuple[str, str] | None:
        slot = await real(parsed, key)
        c._lost_leases.add(slot)   # the lease renewal reported it lost while the attempt ran out of time
        return slot

    c._take_record_lease = take  # type: ignore[method-assign]
    with pytest.raises(TimeoutError):
        await c._drive_indexing(_parsed(), "message:1")
    assert commits == []


async def test_a_cut_is_handed_back_with_the_attempt_refunded_and_a_delay(worker, monkeypatch) -> None:
    """Through ``_decide``: nack with a refund, no dead letter, a delay at least the configured one on every repeat."""
    commits: list = []
    c = _consumer(worker, _embedding_handler(beats=4, commits=commits))
    c.stage_admission = StageAdmission(stage="s", limit=1)  # type: ignore[assignment]  # the pause applies only with a stage's admission
    nacks: list[dict] = []
    dead: list = []

    async def process(_d: object, **_k: object) -> tuple[bool, bool, None, str]:
        await c._drive_indexing(_parsed(), "message:1")
        return True, False, None, ""

    async def nack(_d: object, delay: float, _err: object, **kw: object) -> bool:
        nacks.append({"delay": delay, **kw})
        return True

    async def abandon(*a: object, **k: object) -> None:
        dead.append(a)

    async def no_precision(_d: object) -> bool:
        return False

    monkeypatch.setattr(c, "_process", process)
    monkeypatch.setattr(c, "_nack", nack)
    monkeypatch.setattr(c, "_abandon", abandon)
    monkeypatch.setattr(c, "_hand_back_for_precision", no_precision)
    d = SimpleNamespace(topic="t", message_id=7, attempts=1, value=b"{}")
    for _ in range(3):
        assert await c._decide(d) == "requeued"
    assert dead == []
    assert all(n.get("refund") is True for n in nacks)
    delays = [n["delay"] for n in nacks]
    assert min(delays) >= messaging_env.embed_cut_requeue_delay_seconds > 0
    assert all(x >= 1.0 for x in delays)   # the row is hidden for seconds: a hot reclaim loop cannot spin
    assert len(commits) == 3 and all(u == 1 for _s, u in commits)
    assert c._paused_until == 0                                  # one record's cut does not stop the consumer claiming


async def test_deadline_changes_from_the_worker_loop_go_through_the_owner_loop(worker, monkeypatch) -> None:
    """``Timeout.reschedule`` is only ever called on the claim loop's thread, however many batches the worker loop reports."""
    claim_thread = threading.current_thread().name
    moved_on: list[str] = []
    real = asyncio.Timeout.reschedule

    def spy(self: asyncio.Timeout, when: float | None) -> object:
        moved_on.append(threading.current_thread().name)
        return real(self, when)

    monkeypatch.setattr(asyncio.Timeout, "reschedule", spy)
    worker_threads: list[str] = []
    c = _consumer(worker, _embedding_handler(beats=5, commits=[], seen_threads=worker_threads))
    with pytest.raises(ProgressRequeue):
        await c._drive_indexing(_parsed(), "message:1")
    assert worker_threads and set(worker_threads) == {"test-cut-worker"}
    assert moved_on and set(moved_on) == {claim_thread}


async def test_record_budget_reschedule_from_another_thread_is_posted_to_its_loop() -> None:
    seen: list[str] = []
    async with asyncio.timeout(60) as t:
        armed = record_budget.RecordBudget(t, 60)
        real = t.reschedule
        t.reschedule = lambda when: (seen.append(threading.current_thread().name), real(when))[1]  # type: ignore[method-assign]
        loop = asyncio.get_running_loop()
        target = loop.time() + 120
        await asyncio.to_thread(armed.reschedule, target)
        await asyncio.sleep(0.05)
        assert seen == [threading.current_thread().name]
        assert t.when() == target and armed.when() == target
        assert armed.expired() is False


async def test_the_leases_are_released_only_after_the_handler_has_unwound(worker) -> None:
    """A counted timeout (no progress) and a cut (progress): the handler's cleanup writes the record, so the record lease outlives it."""
    for beats, expected in ((0, TimeoutError), (3, ProgressRequeue)):
        order: list = []
        c = _consumer(worker, _embedding_handler(beats=beats, commits=[], order=order, unwind_s=0.6), order)
        with pytest.raises(expected):
            await c._drive_indexing(_parsed(), "message:1")
        assert order.index("unwound") < order.index("release"), (beats, order)
        assert order.count("release") == 2 and not c._leases


async def test_a_handler_that_outlasts_the_unwind_bound_keeps_its_leases_and_says_so(worker, monkeypatch, caplog) -> None:
    from app.services.messaging.postgres import consumer as mod
    from app.utils import indexing_timing as timing

    monkeypatch.setattr(mod, "_UNWIND_WAIT_S", 0.2)
    noted: list[dict] = []
    monkeypatch.setattr(timing, "note", lambda **f: noted.append(f) or True)
    commits: list = []
    c = _consumer(worker, _embedding_handler(beats=3, commits=commits, unwind_s=1.0))
    parsed = SimpleNamespace(payload={"recordId": "rec-9"}, org_ctx=None, requestId="r")
    with caplog.at_level("WARNING"), pytest.raises(TimeoutError):
        await c._drive_indexing(parsed, "message:1")
    assert commits == []   # not read while the handler may still change it: counted
    assert {"unwind_timeout_s": 0.2} in noted   # on the record's index.end line
    assert c.concurrency_manager.released == []   # type: ignore[attr-defined]  # not released while it may write: they expire
    assert not c._leases                          # and are no longer renewed
    # The timeout is re-raised and index.end writes it: a second WARNING here would log one failure twice.
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING and "rec-9" in r.getMessage()]


async def test_a_lease_lost_while_waiting_for_the_unwind_is_handed_back_as_held(worker) -> None:
    """The renewal cancels the delivery inside the wait for the handler's cleanup: it takes the same hand-back as any lost lease."""
    from app.services.messaging.postgres.consumer import RecordHeld

    order: list = []
    c = _consumer(worker, _embedding_handler(beats=3, commits=[], order=order, unwind_s=1.0), order)
    run = asyncio.create_task(c._drive_indexing(_parsed(), "message:1"))
    await asyncio.sleep(BUDGET_S + 0.3)       # the budget has ended; the handler is still cleaning up
    assert not run.done() and "unwound" not in order
    c.concurrency_manager.lost_pools.add(next(p for p, _h in c.concurrency_manager.acquired if p.startswith("record:")))  # type: ignore[attr-defined]
    await c._renew_leases()                   # what the claim heartbeat does: cancels the task that holds the lease
    with pytest.raises(RecordHeld):
        await asyncio.wait_for(run, 5)
    assert order.index("unwound") < order.index("release")   # even then the cleanup is waited for before the leases go
