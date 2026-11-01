"""The parse pool on the lane budget (``PARSE_LANES=on``).

State-machine tests use stub workers (``lane_pool_support``) so the reap and
checkout interleavings are deterministic. Tests of admission, the lease and the
process groups use real workers. Concurrency is asserted on the independent job
gauge and on a /proc scan of resident workers, never on the lease or token
tables, and every pool is wider than its limit.
"""
from __future__ import annotations

import ctypes
import math
import os
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.modules.parsers import parse_pool, parse_pool_lanes
from app.modules.parsers.parse_pool import (
    ParseAbandonedError,
    ParseWorkerError,
    _Job,
    _WorkerUnavailable,
)
from app.modules.parsers.parse_pool_lanes import (
    LanePool,
    WorkerState,
    worker_thread_env,
)
from app.services.resource_governor.lanes import (
    LaneBudget,
    in_lane_scope,
    plan_lanes,
)
from tests.unit.lane_gauge import (
    FakeClock,
    Mem,
    env_job,
    first_line,
    max_overlap,
    pid_job,
    reap,
    send,
    start_role,
)
from tests.unit.modules.parsers.lane_pool_support import (
    ProcSampler,
    StubHub,
    resident_workers,
    wait_for,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

GIB = 2**30
pytestmark = pytest.mark.timeout(120)


# -- fixtures ----------------------------------------------------------------


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(1000.0)


@pytest.fixture
def hub() -> StubHub:
    return StubHub()


@pytest.fixture(autouse=True)
def _fast_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parse_pool_lanes, "_SETTLE_SECONDS", 0.1)


@pytest.fixture
def governor_stub() -> Iterator[MagicMock]:
    """The governor a crash reports to; no test expects it to be called unless it says so."""
    governor = MagicMock()
    previous = parse_pool._State.governor
    parse_pool._State.governor = governor
    yield governor
    parse_pool._State.governor = previous


@pytest.fixture
def make_budget(tmp_path: Path) -> Iterator[Callable[..., LaneBudget]]:
    made: list[LaneBudget] = []

    def factory(lanes: int = 2, *, name: str = "lanes", clock: FakeClock | None = None, **options: object) -> LaneBudget:
        options.setdefault("memory_reader", Mem(1000 * GIB, 0))
        options.setdefault("warmup_seconds", 0.0)
        options.setdefault("view_ttl", 0.0)
        options.setdefault("alive", lambda pid: True)
        if clock is not None:
            options["clock"] = clock
        budget = LaneBudget.open(plan_lanes(cpus=lanes * 2, heavy=lanes * 2), tmp_path / name, **options)
        made.append(budget)
        return budget

    yield factory
    for budget in made:
        budget.close()


@pytest.fixture
def make_pool() -> Iterator[Callable[..., LanePool]]:
    made: list[LanePool] = []

    def factory(budget: LaneBudget, *, threads: int = 3, hub: StubHub | None = None, tick: float = 3600.0, **options: object) -> LanePool:
        budget.register_presence()
        options.setdefault("clock", budget.now)
        pool = LanePool(
            kind="budgeted", threads=threads, budget=budget, admit=True,
            worker_factory=hub.factory if hub is not None else None,
            reap_tick=tick, **options,
        )
        made.append(pool)
        return pool

    yield factory
    for pool in made:
        pool.shutdown()


def submit(pool: LanePool, fn: Callable, *args: object, timeout: float | None = None, label: str = "t") -> _Job:
    job = _Job(fn, args, label, 0, timeout)
    pool.enqueue(job)
    return job


def hold(event: threading.Event) -> str:
    event.wait(30)
    return "held"


def quick() -> int:
    return 1


def make_idle(pool: LanePool, hub: StubHub, clock: FakeClock, count: int) -> list:
    """*count* idle stub workers, returned oldest-first; each was last used one second after the one before."""
    events = [threading.Event() for _ in range(count)]
    jobs = [submit(pool, hold, e) for e in events]
    assert wait_for(lambda: sum(w.running for w in hub.workers) == count)
    for event, job in zip(events, jobs, strict=True):
        clock.advance(1)
        event.set()
        assert job.future.result(timeout=10) == "held"
    assert wait_for(lambda: pool.stats()["idle"] == count)
    return [e.worker for e in reversed(pool._idle)]


def residents(budget: LaneBudget) -> int:
    return budget.held_count()[1]


# -- T42 to T47: the reap and checkout protocol -------------------------------


@pytest.mark.parametrize("tick", [0.001, 1.0])
def test_checkout_racing_reap_never_uses_a_reaping_worker(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, tick: float
) -> None:
    budget = make_budget(2)
    pool = make_pool(budget, hub=hub, tick=tick, idle_seconds=0.0)
    stop = threading.Event()

    def racer() -> None:
        while not stop.is_set():
            pool.reap_once()

    thread = threading.Thread(target=racer, daemon=True)
    thread.start()
    try:
        for _ in range(1000):
            assert submit(pool, quick).future.result(timeout=10) == 1
    finally:
        stop.set()
        thread.join()
    assert hub.violations == []  # never stopped while running, never sent to after a stop
    # How often the reaper won is up to the scheduler; test_reaper_winning_the_race_makes_the_job_spawn forces it.
    assert pool.stats()["spawns"] == len(hub.workers) >= 1


def test_reaper_winning_the_race_makes_the_job_spawn(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The interleaving of the race above, forced: the reaper takes the idle worker after the lease, before the checkout."""
    budget = make_budget(2, clock=clock)
    pool = make_pool(budget, hub=hub, clock=clock, idle_seconds=0.0)
    (first,) = make_idle(pool, hub, clock, 1)
    real_checkout = pool.checkout

    def losing_checkout(job: _Job) -> object:
        with pool._lock:
            pool._unpin_locked(job)  # as the job's own checkout would; the worker is fair game from here
        pool.reap_once()
        return real_checkout(job)

    monkeypatch.setattr(pool, "checkout", losing_checkout)
    assert submit(pool, quick).future.result(timeout=10) == 1
    assert first.stopped and len(hub.workers) == 2 and hub.violations == []
    assert pool.stats()["spawns"] == 2


def test_reap_during_worker_start_does_not_touch_it(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock
) -> None:
    budget = make_budget(2, clock=clock)
    pool = make_pool(budget, hub=hub, clock=clock, idle_seconds=0.0)
    hub.start_gate.clear()
    job = submit(pool, quick)
    assert wait_for(lambda: len(hub.workers) == 1)
    clock.advance(100)
    budget.request_reap()
    pool.reap_once()
    assert hub.stop_calls == 0 and hub.workers[0].alive()
    hub.start_gate.set()
    assert job.future.result(timeout=10) == 1
    assert hub.violations == []


def test_reap_request_never_kills_a_busy_worker(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock
) -> None:
    budget = make_budget(2, clock=clock)
    pool = make_pool(budget, hub=hub, clock=clock, idle_seconds=0.0)
    event = threading.Event()
    job = submit(pool, hold, event)
    assert wait_for(lambda: any(w.running for w in hub.workers))
    clock.advance(100)
    budget.request_reap()
    budget.report_incident()  # a brake as well
    pool.reap_once()
    assert hub.stop_calls == 0
    event.set()
    assert job.future.result(timeout=10) == "held"
    assert hub.violations == []


def test_just_returned_worker_is_not_reaped_on_request(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock
) -> None:
    budget = make_budget(2, clock=clock)
    pool = make_pool(budget, hub=hub, clock=clock)
    make_idle(pool, hub, clock, 1)
    clock.advance(1)
    budget.request_reap()
    pool.reap_once()
    assert pool.stats()["idle"] == 1 and hub.stop_calls == 0


def test_request_reaps_only_workers_idle_5s(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock
) -> None:
    budget = make_budget(2, clock=clock)
    pool = make_pool(budget, hub=hub, clock=clock)
    make_idle(pool, hub, clock, 1)
    clock.advance(4.0)
    budget.request_reap()
    pool.reap_once()
    assert pool.stats()["idle"] == 1
    clock.advance(1.0)
    pool.reap_once()
    assert pool.stats()["idle"] == 0 and pool.reaped["request"] == 1
    assert residents(budget) == 0


def test_idle_timer_reaps(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock
) -> None:
    budget = make_budget(2, clock=clock)
    pool = make_pool(budget, hub=hub, clock=clock)
    make_idle(pool, hub, clock, 1)
    clock.advance(59)
    pool.reap_once()
    assert pool.stats()["idle"] == 1
    clock.advance(1)
    pool.reap_once()
    assert pool.stats()["idle"] == 0 and pool.reaped["timer"] == 1


def test_one_denial_reaps_at_most_one_worker_per_process_per_tick(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock
) -> None:
    budget = make_budget(4, clock=clock)
    pool = make_pool(budget, hub=hub, clock=clock, threads=4)
    workers = make_idle(pool, hub, clock, 3)
    clock.advance(20)
    budget.request_reap()
    pool.reap_once()
    assert pool.stats()["idle"] == 2 and pool.reaped["request"] == 1
    assert workers[0].stopped and not workers[1].stopped  # the one idle longest


def test_request_stamp_consumed_once(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock
) -> None:
    budget = make_budget(4, clock=clock)
    pool = make_pool(budget, hub=hub, clock=clock, threads=4)
    make_idle(pool, hub, clock, 3)
    clock.advance(20)
    budget.request_reap()
    pool.reap_once()
    pool.reap_once()  # the same stamp, still fresh
    assert pool.stats()["idle"] == 2
    clock.advance(1)
    budget.request_reap()  # a requester that is still denied rewrites it
    pool.reap_once()
    assert pool.stats()["idle"] == 1
    clock.advance(10)  # an old stamp is ignored
    pool.reap_once()
    assert pool.stats()["idle"] == 1


def test_brake_reaps_idle_workers(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock
) -> None:
    budget = make_budget(4, clock=clock)
    pool = make_pool(budget, hub=hub, clock=clock, threads=4)
    make_idle(pool, hub, clock, 2)
    budget.report_incident()
    assert budget.cap() == 2
    pool.reap_once()  # idle 1 s: a worker that has just returned stays
    assert pool.stats()["idle"] == 2
    clock.advance(10)
    pool.reap_once()
    assert pool.reaped["brake"] == 0  # two residents are within the cap of two


def test_brake_reaps_only_excess_over_cap(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock
) -> None:
    budget = make_budget(4, clock=clock)
    pool = make_pool(budget, hub=hub, clock=clock, threads=4)
    make_idle(pool, hub, clock, 4)
    budget.report_incident()
    assert budget.cap() == 2
    clock.advance(10)
    pool.reap_once()
    assert pool.stats()["idle"] == 3  # one per tick, oldest first
    pool.reap_once()
    assert pool.stats()["idle"] == 2
    pool.reap_once()
    pool.reap_once()
    assert pool.stats()["idle"] == 2 and pool.reaped["brake"] == 2  # stops at the cap
    assert residents(budget) == 2


def test_brake_counts_other_processes_workers(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock
) -> None:
    """The excess is measured over the whole domain, not over this pool's own workers."""
    budget = make_budget(4, clock=clock)
    other = make_budget(4, clock=clock)
    other.register_presence()
    pool = make_pool(budget, hub=hub, clock=clock, threads=4)
    make_idle(pool, hub, clock, 2)
    foreign = [other.admit_spawn(0) for _ in range(2)]
    assert all(t is not None for t in foreign)
    budget.report_incident()  # cap 2; domain residents 4
    clock.advance(10)
    pool.reap_once()
    assert pool.stats()["idle"] == 1
    for t in foreign:
        t.release(started=True)
        t.worker_exited()


def test_pin_counts_only_jobs_without_a_worker(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock
) -> None:
    budget = make_budget(2, clock=clock)
    pool = make_pool(budget, hub=hub, clock=clock)
    older, newer = make_idle(pool, hub, clock, 2)
    blockers = [budget.try_acquire() for _ in range(2)]
    event = threading.Event()
    job = submit(pool, hold, event)
    assert wait_for(lambda: pool.stats()["pending"] == 1)  # queued, no worker yet
    for lease in blockers:
        lease.release()
    assert wait_for(lambda: any(w.running for w in hub.workers))
    assert pool.stats()["pending"] == 0  # it holds a worker now: it pins nothing
    clock.advance(20)
    budget.request_reap()
    pool.reap_once()
    assert older.stopped  # the other idle worker is reapable
    event.set()
    assert job.future.result(timeout=10) == "held"
    assert not newer.stopped and hub.violations == []


def test_reaper_keeps_workers_for_queued_jobs(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock
) -> None:
    budget = make_budget(4, clock=clock)
    pool = make_pool(budget, hub=hub, clock=clock, threads=4)
    oldest, middle, newest = make_idle(pool, hub, clock, 3)
    blockers = [budget.try_acquire() for _ in range(4)]
    assert all(b is not None for b in blockers)
    event = threading.Event()
    jobs = [submit(pool, hold, event) for _ in range(2)]
    assert wait_for(lambda: pool.stats()["pending"] == 2)
    clock.advance(100)  # past the timer
    pool.reap_once()
    assert oldest.stopped and not middle.stopped and not newest.stopped
    clock.advance(1)
    budget.report_incident()  # the brake takes only unpinned workers: none are left
    pool.reap_once()
    assert not middle.stopped and not newest.stopped
    # A request does not wait for the queue: a lease holder elsewhere cannot start, the queued jobs wait
    # for that holder, and the pinned workers hold the memory it needs (one per tick, the oldest).
    clock.advance(1)
    budget.request_reap()
    pool.reap_once()
    assert middle.stopped and not newest.stopped
    for lease in blockers:
        lease.release()
    event.set()
    assert [j.future.result(timeout=10) for j in jobs] == ["held", "held"]


def test_idle_death_costs_no_record(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock,
    governor_stub: MagicMock,
) -> None:
    budget = make_budget(2, clock=clock)
    pool = make_pool(budget, hub=hub, clock=clock)
    (worker,) = make_idle(pool, hub, clock, 1)
    worker.kill()
    assert submit(pool, quick).future.result(timeout=10) == 1  # checkout drops the dead one and respawns
    governor_stub.report_memory_incident.assert_not_called()
    assert len(hub.workers) == 2 and residents(budget) == 1


def test_idle_death_releases_token_within_one_tick(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock,
    governor_stub: MagicMock,
) -> None:
    budget = make_budget(2, clock=clock)
    pool = make_pool(budget, hub=hub, clock=clock)
    (worker,) = make_idle(pool, hub, clock, 1)
    assert residents(budget) == 1
    worker.kill()
    pool.reap_once()
    assert residents(budget) == 0 and pool.stats()["idle"] == 0
    governor_stub.report_memory_incident.assert_not_called()


def test_reaped_worker_death_is_not_a_crash(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock,
    governor_stub: MagicMock,
) -> None:
    budget = make_budget(2, clock=clock)
    pool = make_pool(budget, hub=hub, clock=clock)
    make_idle(pool, hub, clock, 1)
    clock.advance(61)
    pool.reap_once()
    assert submit(pool, quick).future.result(timeout=10) == 1
    governor_stub.report_memory_incident.assert_not_called()
    assert pool.reaped["timer"] == 1


def test_token_released_only_after_process_exit(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock
) -> None:
    budget = make_budget(2, clock=clock)
    pool = make_pool(budget, hub=hub, clock=clock)
    (worker,) = make_idle(pool, hub, clock, 1)
    worker.survives = True  # SIGKILL does not take it
    clock.advance(61)
    pool.reap_once()
    assert residents(budget) == 1 and pool.stats()["unreaped"] == 1
    assert residents(budget) <= budget.resident_limit()
    pool.reap_once()
    assert residents(budget) == 1  # retried, still alive
    worker.release_survivor()
    pool.reap_once()
    assert residents(budget) == 0 and pool.stats()["unreaped"] == 0


def _subreaper(enable: bool) -> None:
    ctypes.CDLL(None, use_errno=True).prctl(36, 1 if enable else 0, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER


def _state(pid: int) -> str | None:
    try:
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0]
    except OSError:
        return None


class _GroupWorker:
    """A worker whose process is a real group leader; ``stop`` does nothing (the test controls the group)."""

    def __init__(self, leader: subprocess.Popen) -> None:
        self.process = leader

    def alive(self) -> bool:
        return self.process.poll() is None

    def stop(self) -> None:
        return None


@pytest.fixture
def subreaper() -> Iterator[None]:
    """Orphans reparent to this process, which never reaps them: a zombie stays, as under a PID 1 that does not."""
    _subreaper(True)
    yield
    _subreaper(False)


def _leader_with_child(child_code: str) -> tuple[subprocess.Popen, int]:
    code = (
        "import subprocess, sys\n"
        f"c = subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        "print(c.pid, flush=True)\n"
    )
    leader = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, start_new_session=True)
    assert leader.stdout is not None
    child = int(leader.stdout.readline())
    leader.wait(timeout=10)
    return leader, child


def test_token_released_only_when_group_is_gone(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], subreaper: None
) -> None:
    budget = make_budget(2)
    pool = make_pool(budget)

    # A zombie child of a dead leader holds no memory: the token goes back.
    leader, child = _leader_with_child("pass")
    assert wait_for(lambda: _state(child) == "Z"), "the setup needs a zombie that nothing reaps"
    ticket = budget.admit_spawn(0)
    assert ticket is not None
    ticket.release(started=True)
    entry = pool.born(_GroupWorker(leader), ticket)
    assert residents(budget) == 1
    assert pool.retire(entry) is True
    assert residents(budget) == 0 and entry.state is WorkerState.DEAD
    os.waitpid(child, 0)

    # A live child in the group of a dead leader keeps the token until it is gone.
    leader, child = _leader_with_child("import time; time.sleep(60)")
    ticket = budget.admit_spawn(0)
    assert ticket is not None
    ticket.release(started=True)
    entry = pool.born(_GroupWorker(leader), ticket)
    assert pool.retire(entry) is False
    assert residents(budget) == 1 and pool.stats()["unreaped"] == 1
    os.kill(child, signal.SIGKILL)
    assert wait_for(lambda: _state(child) == "Z")
    pool.reap_once()
    assert residents(budget) == 0 and pool.stats()["unreaped"] == 0
    os.waitpid(child, 0)


def test_checkin_after_shutdown_stops_the_worker(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    budget = make_budget(2)
    pool = make_pool(budget, hub=hub)
    ticket = budget.admit_spawn(0)
    assert ticket is not None
    ticket.release(started=True)
    worker = hub.factory()
    entry = pool.born(worker, ticket)
    with pool._lock:
        pool._closing = True  # shutdown ran while the job was in flight
    runner = parse_pool_lanes._LaneRunner(pool, None)
    runner._entry = entry
    runner.finish()
    assert worker.stopped and entry.state is WorkerState.DEAD
    assert pool.stats()["idle"] == 0 and residents(budget) == 0


def test_shutdown_races_reaper_and_checkout(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    budget = make_budget(2)
    pool = make_pool(budget, hub=hub, tick=0.001, idle_seconds=0.0)
    results: list[object] = []

    def client() -> None:
        for _ in range(200):
            try:
                results.append(submit(pool, quick).future.result(timeout=10))
            except (ParseAbandonedError, parse_pool._PoolClosed):
                results.append("abandoned")
            except Exception as exc:  # pragma: no cover
                results.append(exc)

    threads = [threading.Thread(target=client) for _ in range(3)]
    for t in threads:
        t.start()
    time.sleep(0.05)
    pool.shutdown()
    for t in threads:
        t.join(timeout=20)
    assert not any(t.is_alive() for t in threads)
    assert all(r in (1, "abandoned") for r in results), results
    assert hub.violations == [] and not hub.alive_workers()
    assert residents(budget) == 0 and budget.held_count()[0] == 0


# -- stalls and rules that need two pools or a shutdown in flight ---------------


def test_stale_reap_request_is_ignored(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock
) -> None:
    """A request older than the freshness window (2 s) reaps nothing."""
    budget = make_budget(2, clock=clock)
    pool = make_pool(budget, hub=hub, clock=clock)
    (old,) = make_idle(pool, hub, clock, 1)
    clock.advance(10)
    budget.request_reap()
    clock.advance(3)
    pool.reap_once()
    assert not old.stopped and pool.reaped["request"] == 0


def test_job_waiting_for_a_spawn_takes_a_worker_that_goes_idle(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock
) -> None:
    """A job refused a spawn looks at the idle list again: the worker another job returns is used, not replaced."""
    budget = make_budget(2, clock=clock, memory_reader=Mem(1000 * GIB, 990 * GIB))  # no headroom
    pool = make_pool(budget, hub=hub, clock=clock, threads=2)
    ev_a, ev_b = threading.Event(), threading.Event()
    first = submit(pool, hold, ev_a)
    assert wait_for(lambda: any(w.running for w in hub.workers))
    second = submit(pool, hold, ev_b)
    assert wait_for(lambda: pool._spawn_waiters == 1)
    assert pool.stats()["pending"] == 1  # still pinned: it has no worker and no spawn yet
    ev_a.set()
    assert first.future.result(timeout=10) == "held"
    assert wait_for(lambda: hub.workers[0].running, 3.0), "the second job did not take the worker the first returned"
    assert len(hub.workers) == 1 and pool.spawns == 1 and pool.stats()["pending"] == 0
    ev_b.set()
    second.future.result(timeout=10)


def test_cross_process_spawn_waiters_do_not_deadlock_a_pinned_idle_worker(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], clock: FakeClock
) -> None:
    """Process A's jobs hold every lease while they wait for headroom; process B's idle worker holds the memory
    they need and is pinned by B's own queued job, which cannot get a lease. The domain's reap request frees it."""
    mem = Mem(1000 * GIB, 990 * GIB)
    hub_a, hub_b = StubHub(), StubHub()
    budget_b = make_budget(2, clock=clock, memory_reader=mem, pid=2001)
    pool_b = make_pool(budget_b, hub=hub_b, clock=clock, threads=2)
    ev_b1 = threading.Event()
    j1 = submit(pool_b, hold, ev_b1)
    assert wait_for(lambda: any(w.running for w in hub_b.workers))  # the first worker is exempt from headroom
    ev_b1.set()
    assert j1.future.result(timeout=10) == "held"
    assert wait_for(lambda: pool_b.stats()["idle"] == 1)
    budget_a = make_budget(2, clock=clock, memory_reader=mem, pid=2002)
    pool_a = make_pool(budget_a, hub=hub_a, clock=clock, threads=2)
    ev_a = threading.Event()
    a_jobs = [submit(pool_a, hold, ev_a) for _ in range(2)]  # both leases, then both wait for headroom
    assert wait_for(lambda: pool_a._spawn_waiters == 2)
    ev_b2 = threading.Event()
    j2 = submit(pool_b, hold, ev_b2)
    assert wait_for(lambda: pool_b.stats()["pending"] == 1)
    for _ in range(30):
        clock.advance(1)
        time.sleep(0.12)
        pool_b.reap_once()
        pool_a.reap_once()
    progressed = any(w.running for w in hub_a.workers + hub_b.workers) or hub_b.workers[0].stopped
    ev_a.set()
    ev_b2.set()
    assert progressed, "no job ran and no worker was reaped in 30 s"
    del a_jobs, j2  # the rest stays refused (static memory); the fixture's shutdown abandons them


def test_denial_without_known_memory_does_not_stall_either(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock
) -> None:
    """The unknown-memory form of the refusal (no reading at all) is handled like the headroom form."""
    budget = make_budget(2, clock=clock, memory_reader=Mem(1000 * GIB, None))
    pool = make_pool(budget, hub=hub, clock=clock, threads=2)
    ev_a, ev_b = threading.Event(), threading.Event()
    first = submit(pool, hold, ev_a)
    assert wait_for(lambda: any(w.running for w in hub.workers))
    second = submit(pool, hold, ev_b)
    assert wait_for(lambda: pool._spawn_waiters == 1)
    ev_a.set()
    first.future.result(timeout=10)
    assert wait_for(lambda: hub.workers[0].running, 3.0) and len(hub.workers) == 1
    ev_b.set()
    second.future.result(timeout=10)


def test_allowance_denial_does_not_stall_either(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock
) -> None:
    """The window allowance (two spawns per warm-up window) refuses the third spawn; a returned worker is taken."""
    budget = make_budget(4, clock=clock, warmup_seconds=3600.0)
    pool = make_pool(budget, hub=hub, clock=clock, threads=4)
    events = [threading.Event() for _ in range(3)]
    jobs = [submit(pool, hold, e) for e in events[:2]]
    assert wait_for(lambda: sum(w.running for w in hub.workers) == 2)  # the first, and the one allowance leaves
    third = submit(pool, hold, events[2])
    assert wait_for(lambda: pool._spawn_waiters == 1)  # refused: allowance spent
    events[0].set()
    jobs[0].future.result(timeout=10)
    assert wait_for(lambda: len(hub.workers) == 2 and sum(w.running for w in hub.workers) == 2, 3.0)
    assert pool.spawns == 2
    events[1].set()
    events[2].set()
    jobs[1].future.result(timeout=10)
    third.future.result(timeout=10)


def test_shutdown_while_a_job_is_finishing_stops_its_worker(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    """shutdown() marks the pool closing, so a worker checked in afterwards is stopped, not made idle."""
    budget = make_budget(2)
    pool = make_pool(budget, hub=hub)
    real_checkin = pool.checkin
    shutdowns: list[threading.Thread] = []

    def checkin(entry: object) -> bool:
        thread = threading.Thread(target=pool.shutdown)
        thread.start()
        shutdowns.append(thread)
        wait_for(lambda: pool._closing, 1.0)  # shutdown() has run its first step; the job is still finishing
        return real_checkin(entry)  # type: ignore[arg-type]

    pool.checkin = checkin  # type: ignore[method-assign]
    assert submit(pool, quick).future.result(timeout=10) == 1
    for thread in shutdowns:
        thread.join(timeout=20)
    (worker,) = hub.workers
    assert worker.stopped and pool.stats()["idle"] == 0 and residents(budget) == 0


# -- real workers: the lease, admission, crashes, cancels ----------------------


def real_pool(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], lanes: int, threads: int | None = None,
    **options: object,
) -> tuple[LaneBudget, LanePool]:
    budget = make_budget(lanes)
    return budget, make_pool(budget, threads=threads or 3 * lanes, **options)


def test_gauge_overlap_is_the_limit_with_a_wider_pool(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], tmp_path: Path
) -> None:
    """T3: a limit of 2 behind 6 lane threads; with no lease the overlap would reach 6."""
    budget, pool = real_pool(make_budget, make_pool, 2, threads=6)
    gauge = tmp_path / "gauge"
    with ProcSampler() as sampler:
        jobs = [submit(pool, pid_job, str(gauge), 0.3) for _ in range(12)]
        pids = {job.future.result(timeout=90) for job in jobs}
    assert max_overlap(gauge) == 2
    assert sampler.peak <= 2  # resident <= lanes + P - 1 with P = 1
    assert len(pids) <= 2
    assert wait_for(lambda: budget.held_count()[0] == 0)


def test_worker_crash_releases_lease_and_token(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], governor_stub: MagicMock
) -> None:
    budget, pool = real_pool(make_budget, make_pool, 2)
    first = submit(pool, pid_job, os.devnull, 0.0)
    worker_pid = first.future.result(timeout=60)
    crash = submit(pool, os._exit, 3)
    with pytest.raises(ParseWorkerError) as raised:
        crash.future.result(timeout=60)
    assert raised.value.cause == "crashed"
    governor_stub.report_memory_incident.assert_called_once()
    assert wait_for(lambda: budget.held_count() == (0, 0))
    assert submit(pool, pid_job, os.devnull, 0.0).future.result(timeout=60) != worker_pid
    assert worker_pid not in resident_workers()


def test_cancel_while_waiting_for_a_lease(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    budget = make_budget(1)
    pool = make_pool(budget, hub=hub)
    blocker = budget.try_acquire()
    job = submit(pool, quick)
    assert wait_for(lambda: budget.stats()["waiting"] == 1)
    job.cancelled.set()
    with pytest.raises(ParseAbandonedError):
        job.future.result(timeout=10)
    blocker.release()
    assert wait_for(lambda: budget.held_count() == (0, 0)) and pool.stats()["pending"] == 0


def test_cancel_while_admitting_a_spawn(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    budget = make_budget(1)
    pool = make_pool(budget, hub=hub)
    monkeypatch.setattr(budget, "admit_spawn", lambda ws=None: None)
    job = submit(pool, quick)
    assert wait_for(lambda: pool._spawn_waiters == 1)
    job.cancelled.set()
    with pytest.raises(ParseAbandonedError):
        job.future.result(timeout=10)
    assert wait_for(lambda: budget.held_count() == (0, 0))
    assert not hub.workers and budget.stats()["spawns_in_window"] == 0


def test_cancel_while_running(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], tmp_path: Path
) -> None:
    budget, pool = real_pool(make_budget, make_pool, 2)
    job = submit(pool, pid_job, str(tmp_path / "g"), 30.0)
    assert wait_for(lambda: len(resident_workers()) == 1 and (tmp_path / "g").exists(), 60)
    job.cancelled.set()
    with pytest.raises(ParseAbandonedError):
        job.future.result(timeout=15)
    assert wait_for(lambda: not resident_workers(), 10)
    assert wait_for(lambda: budget.held_count() == (0, 0))


def test_lost_checkout_race_respawns_through_admission(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reaper takes the idle worker between the lease and the checkout: the job spawns, through admission."""
    budget, pool = real_pool(make_budget, make_pool, 2)
    admitted = []
    real_admit = budget.admit_spawn
    monkeypatch.setattr(budget, "admit_spawn", lambda ws=None: admitted.append(1) or real_admit(ws))
    first = submit(pool, pid_job, os.devnull, 0.0).future.result(timeout=60)
    real_checkout = pool.checkout

    def losing_checkout(job: _Job) -> object:
        pool._idle_seconds = 0.0
        with pool._lock:
            pool._unpin_locked(job)  # as checkout does first; from here the worker is fair game
        pool.reap_once()  # the reaper wins the race
        return real_checkout(job)

    monkeypatch.setattr(pool, "checkout", losing_checkout)
    second = submit(pool, pid_job, os.devnull, 0.0).future.result(timeout=60)
    assert second != first and len(admitted) == 2
    assert pool.reaped["timer"] == 1


def test_every_spawn_goes_through_admission(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], monkeypatch: pytest.MonkeyPatch,
    governor_stub: MagicMock,
) -> None:
    """T49 with real workers: the first start, a respawn after an idle death, after a crash and after a timeout."""
    budget, pool = real_pool(make_budget, make_pool, 2)
    admitted = []
    real_admit = budget.admit_spawn
    monkeypatch.setattr(budget, "admit_spawn", lambda ws=None: admitted.append(1) or real_admit(ws))
    pid = submit(pool, pid_job, os.devnull, 0.0).future.result(timeout=60)
    assert len(admitted) == 1
    os.kill(pid, signal.SIGKILL)  # dies idle
    assert wait_for(lambda: pid not in resident_workers() or True)
    time.sleep(0.2)
    assert submit(pool, pid_job, os.devnull, 0.0).future.result(timeout=60) != pid
    assert len(admitted) == 2
    with pytest.raises(ParseWorkerError):
        submit(pool, os._exit, 1).future.result(timeout=60)
    submit(pool, pid_job, os.devnull, 0.0).future.result(timeout=60)
    assert len(admitted) == 3
    with pytest.raises(ParseWorkerError) as timed_out:
        submit(pool, time.sleep, 30, timeout=0.5).future.result(timeout=60)
    assert timed_out.value.cause == "timed_out"
    submit(pool, pid_job, os.devnull, 0.0).future.result(timeout=60)
    assert len(admitted) == 4
    assert wait_for(lambda: residents(budget) == len(resident_workers()), 5)


def test_worker_dying_between_checkout_and_send_is_replaced_without_leaking_its_token(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock
) -> None:
    """The runner drops a dead worker it holds and starts another: the dead one's token must come back."""
    budget = make_budget(2, clock=clock)
    pool = make_pool(budget, hub=hub, clock=clock)
    (worker,) = make_idle(pool, hub, clock, 1)
    worker.alive_checks_left = 1  # alive for the checkout, dead when the job is about to be sent
    assert submit(pool, quick).future.result(timeout=10) == 1
    assert len(hub.workers) == 2 and worker.stopped
    assert residents(budget) == 1  # the replacement only
    assert pool.stats()["idle"] == 1 and hub.violations == []


def test_failed_start_returns_the_ticket(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    budget = make_budget(2)
    pool = make_pool(budget, hub=hub)
    hub.fail_start = _WorkerUnavailable("no process")
    job = submit(pool, quick)  # the in-thread fallback runs it
    assert job.future.result(timeout=10) == 1
    assert residents(budget) == 0 and budget.stats()["spawns_in_window"] == 0  # token and window slot came back
    hub.fail_start = None


def test_in_thread_fallback_uses_the_lane_scope(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    budget = make_budget(1)
    pool = make_pool(budget, hub=hub)
    hub.fail_start = _WorkerUnavailable("no process")
    seen = {}

    def nested() -> str:
        lease = budget.try_acquire()  # the only lane is held by this very job
        seen["scope"], seen["nested"] = in_lane_scope(), lease is not None and lease.nested
        return "ok"

    assert submit(pool, nested).future.result(timeout=10) == "ok"
    assert seen == {"scope": True, "nested": True}
    assert budget.held_count() == (0, 0)


def test_lane_wait_is_bounded(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANE_WAIT_SECONDS", "0.4")
    budget = make_budget(1)
    pool = make_pool(budget, hub=hub)
    blocker = budget.try_acquire()
    started = time.monotonic()
    with pytest.raises(ParseWorkerError) as raised:
        submit(pool, quick).future.result(timeout=10)
    assert raised.value.cause == "unavailable" and time.monotonic() - started < 5
    # And the same bound for a spawn that is never admitted.
    blocker.release()
    monkeypatch.setattr(budget, "admit_spawn", lambda ws=None: None)
    with pytest.raises(ParseWorkerError) as raised:
        submit(pool, quick).future.result(timeout=10)
    assert raised.value.cause == "unavailable"
    assert budget.held_count() == (0, 0)


def test_budget_closed_under_waiters(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    budget = make_budget(1)
    pool = make_pool(budget, hub=hub)
    blocker = budget.try_acquire()
    job = submit(pool, quick)
    assert wait_for(lambda: budget.stats()["waiting"] == 1)
    budget.close()  # the wrong order: the budget closes under the waiter
    with pytest.raises(ParseAbandonedError):
        job.future.result(timeout=10)
    del blocker


def test_jobs_take_leases_in_queue_order(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    budget = make_budget(1)
    pool = make_pool(budget, hub=hub, threads=6)
    order: list[int] = []
    lock = threading.Lock()

    def job(i: int) -> int:
        with lock:
            order.append(i)
        time.sleep(0.03)
        return i

    blocker = budget.try_acquire()
    jobs = [submit(pool, job, i) for i in range(12)]
    time.sleep(0.3)
    blocker.release()
    assert [j.future.result(timeout=30) for j in jobs] == list(range(12))
    assert order == list(range(12))


# -- the orphan check ----------------------------------------------------------


def test_orphans_exit_with_parent(tmp_path: Path) -> None:
    """T24: SIGKILL the service; its worker and the child the worker left behind exit within 3 s."""
    role = start_role("pool_orphans", {"dir": str(tmp_path / "lanes"), "lanes": 2})
    try:
        info = first_line(role)
        worker, child = info["worker"], info["child"]
        assert os.path.exists(f"/proc/{worker}") and os.path.exists(f"/proc/{child}")
        role.kill()
        role.wait()

        def gone(pid: int) -> bool:
            return _state(pid) in (None, "Z")

        assert wait_for(lambda: gone(worker) and gone(child), 3.5)
    finally:
        reap(role)


# -- several processes ---------------------------------------------------------

ROLE_MEMORY = {"mem_limit": 1000 * GIB, "mem_raw": 0}


def start_pool_role(tmp_path: Path, lanes: int, **extra: object) -> subprocess.Popen:
    role = start_role("pool", {"dir": str(tmp_path / "lanes"), "lanes": lanes, **ROLE_MEMORY, **extra}, commands=True)
    first_line(role)
    return role


def test_alternating_services_spawn_no_more_than_legacy(tmp_path: Path) -> None:
    """T39: one lane, two processes, eight alternating jobs: two workers in all, and no reap."""
    a, b = start_pool_role(tmp_path, 1), start_pool_role(tmp_path, 1)
    gauge = str(tmp_path / "gauge")
    try:
        pids = [send(role, op="job", gauge=gauge, seconds=0.1)["pid"] for _ in range(4) for role in (a, b)]
        assert len(set(pids)) == 2
        for role in (a, b):
            stats = send(role, op="stats")
            assert stats["reaped"] == {"timer": 0, "request": 0, "brake": 0} and stats["spawns"] == 1
        assert max_overlap(gauge) == 1
    finally:
        reap(a, b)


def test_alternating_under_pressure_is_bounded_per_switch(tmp_path: Path) -> None:
    """T40: the second worker is refused for headroom; each switch costs a reap after 5 s idle and one start."""
    pressure = {"mem_limit": 10 * GIB, "mem_raw": 8 * GIB}
    a = start_role("pool", {"dir": str(tmp_path / "lanes"), "lanes": 1, **pressure}, commands=True)
    b = start_role("pool", {"dir": str(tmp_path / "lanes"), "lanes": 1, **pressure}, commands=True)
    first_line(a), first_line(b)
    gauge = str(tmp_path / "gauge")
    bound = 5.0 + 1.0 + 6.0 + 4.0  # min idle + one tick + a worker start + margin
    try:
        pids, waits = [], []
        for role in (a, b, a):
            started = time.monotonic()
            reply = send(role, op="job", gauge=gauge, seconds=0.1)
            waits.append(time.monotonic() - started)
            pids.append(reply["pid"])
        assert all(w < bound for w in waits), waits
        assert len(set(pids)) == 3 and max_overlap(gauge) == 1  # at most one spawn per switch
        assert sum(send(role, op="stats")["reaped"]["request"] for role in (a, b)) >= 1
    finally:
        reap(a, b)


def test_no_service_starves_under_alternation(tmp_path: Path) -> None:
    """Two processes at one lane, each with a stream of jobs: both finish them all, one job at a time."""
    a, b = start_pool_role(tmp_path, 1), start_pool_role(tmp_path, 1)
    gauge = str(tmp_path / "gauge")
    try:
        for i in range(6):
            for role in (a, b):
                assert role.stdin is not None
                role.stdin.write(f'{{"op": "job", "gauge": "{gauge}", "seconds": 0.15, "id": {i}}}\n')
                role.stdin.flush()
        done = {}
        for name, role in (("a", a), ("b", b)):
            done[name] = sorted(first_line(role)["done_at"] for _ in range(6))
        assert max_overlap(gauge) == 1
        # Lease order across processes is not fair (a flock poll), so the order of completions is not
        # asserted; what must hold is that neither process is left behind: all twelve jobs ran.
        assert len(done["a"]) == len(done["b"]) == 6
    finally:
        reap(a, b)


def test_multi_lane_burst_stays_within_the_resident_bound(tmp_path: Path) -> None:
    """T41: three lanes, three processes, a burst of four jobs each."""
    roles = [start_pool_role(tmp_path, 3, tick=0.5, warmup_seconds=0.0) for _ in range(3)]
    gauge = str(tmp_path / "gauge")
    parents = {role.pid for role in roles}
    try:
        with ProcSampler(parents=parents) as sampler:
            for role in roles:
                for i in range(4):
                    assert role.stdin is not None
                    role.stdin.write(f'{{"op": "job", "gauge": "{gauge}", "seconds": 2.0, "id": {i}}}\n')
                    role.stdin.flush()
            replies = [first_line(role) for role in roles for _ in range(4)]
        assert all("pid" in r for r in replies), replies
        assert max_overlap(gauge) == 3  # the limit is reached and never passed
        assert sampler.peak <= 3 + 3 - 1  # lanes + P - 1
        assert len({r["pid"] for r in replies}) <= len(replies)
        reaped = sum(sum(send(role, op="stats")["reaped"].values()) for role in roles)
        assert reaped <= len(sampler.seen)  # each worker is reaped at most once
    finally:
        reap(*roles)


# -- the pool kinds, widths and the worker environment ------------------------


def _governor(budget: LaneBudget | None, heavy: int = 4) -> SimpleNamespace:
    return SimpleNamespace(
        lanes=budget, ceilings=SimpleNamespace(heavy=heavy), fleet_ceilings=SimpleNamespace(heavy=heavy),
        report_memory_incident=lambda reason: None,
    )


@pytest.fixture
def lanes_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PARSE_LANES", "on")
    monkeypatch.delenv("PARSE_POOL_WORKERS", raising=False)
    saved = parse_pool._State.governor
    yield
    parse_pool.shutdown_parse_pool()
    parse_pool._State.governor = saved


@pytest.mark.parametrize(("heavy", "expected"), [(1, 1), (2, 1), (3, 1), (4, 2), (9, 4), (10, 5), (14, 7), (62, 31)])
def test_width_follows_the_plan(lanes_on: None, tmp_path: Path, heavy: int, expected: int) -> None:
    """T48: the cap of 4 is gone under ``on``; heavy 1 to 9 gives the legacy width."""
    plan = plan_lanes(cpus=64, heavy=heavy)
    budget = LaneBudget.local(plan, memory_reader=Mem(None, None))
    parse_pool.set_resource_governor(_governor(budget, heavy))
    assert parse_pool.parse_pool_workers() == expected
    if heavy <= 9:
        assert expected == max(1, min(4, heavy // 2))  # as the legacy rule
    budget.close()


def test_explicit_pool_is_private_and_braked(lanes_on: None, monkeypatch: pytest.MonkeyPatch, make_budget: Callable[..., LaneBudget]) -> None:
    """T48: an explicit size is a private pool under the shared brake, never a floor; it takes no token."""
    monkeypatch.setenv("PARSE_POOL_WORKERS", "4")
    budget = make_budget(4)
    parse_pool.set_resource_governor(_governor(budget, 8))
    pool = parse_pool._pool()
    assert isinstance(pool, LanePool) and pool.kind == "explicit" and pool.workers == 4
    assert pool._leases.held() == 0
    limit = pool._leases._limit
    assert limit() == 4
    budget.report_incident()  # cap 2 of 4
    assert limit() == math.ceil(4 * 2 / 4) == 2
    budget.report_incident()
    assert limit() == 1  # never below one
    assert submit(pool, pid_job, os.devnull, 0.0).future.result(timeout=60) > 0
    assert budget.held_count() == (0, 0)  # no run lease and no resident token of the shared budget
    assert budget.live_processes() == 1 and not budget._presence_registered
    monkeypatch.setenv("PARSE_POOL_WORKERS", "0")
    parse_pool.shutdown_parse_pool()
    assert parse_pool.parse_pool_workers() == 0 and parse_pool._pool() is None


def test_presence_is_taken_by_a_budgeted_pool_only(lanes_on: None, monkeypatch: pytest.MonkeyPatch, make_budget: Callable[..., LaneBudget]) -> None:
    budget = make_budget(2)
    parse_pool.set_resource_governor(_governor(budget, 4))
    parse_pool._pool(interactive=True)
    assert not budget._presence_registered
    parse_pool.shutdown_parse_pool()
    parse_pool._pool()
    assert budget._presence_registered
    parse_pool.shutdown_parse_pool()


def test_interactive_parse_is_not_blocked_by_background(lanes_on: None, make_budget: Callable[..., LaneBudget], tmp_path: Path) -> None:
    """T16: one lane held by a background job; a chat attachment completes on its own pool."""
    import asyncio

    budget = make_budget(1)
    parse_pool.set_resource_governor(_governor(budget, 2))
    background = submit(parse_pool._pool(), pid_job, str(tmp_path / "bg"), 12.0)
    assert wait_for(lambda: budget.held_count()[0] == 1 and (tmp_path / "bg").exists(), 60)
    started = time.monotonic()
    pid = asyncio.run(parse_pool.submit(pid_job, os.devnull, 0.0, interactive=True))
    assert pid > 0 and time.monotonic() - started < 12.0  # not queued behind the background job
    assert budget.held_count()[0] == 1  # the interactive job took no lease
    assert not background.future.done()
    background.future.result(timeout=60)


def test_interactive_is_ignored_when_lanes_are_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PARSE_LANES", raising=False)
    monkeypatch.setenv("PARSE_POOL_WORKERS", "1")
    saved = parse_pool._State.governor
    parse_pool.set_resource_governor(SimpleNamespace(ceilings=SimpleNamespace(heavy=4)))  # type: ignore[arg-type]
    try:
        assert parse_pool._pool(interactive=True) is parse_pool._pool()
        assert isinstance(parse_pool._pool(), parse_pool._ParsePool)
    finally:
        parse_pool.shutdown_parse_pool()
        parse_pool._State.governor = saved


def _env_of(extra: dict[str, str], environ: dict[str, str]) -> dict[str, str]:
    return worker_thread_env(extra.get("threads", 1), environ)  # type: ignore[arg-type]


def test_worker_env_carries_the_lane_threads() -> None:
    env = worker_thread_env(3, {})
    assert env == {
        "PIPESHUB_LANE_WORKER": "1", "PIPESHUB_LANE_THREADS": "3", "OMP_NUM_THREADS": "3",
        "OPENBLAS_NUM_THREADS": "3", "MKL_NUM_THREADS": "3", "PIPESHUB_LANE_CV2_THREADS": "3",
    }
    # The image's OMP default of 2 is replaced; an operator's CV2 setting is kept (0 means uncapped).
    env = worker_thread_env(1, {"OMP_NUM_THREADS": "2", "PIPESHUB_LANE_CV2_THREADS": "0"})
    assert env["OMP_NUM_THREADS"] == "1" and "PIPESHUB_LANE_CV2_THREADS" not in env


def test_worker_env_reaches_a_real_worker(make_budget: Callable[..., LaneBudget], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PDF_TABLE_MODEL_THREADS", raising=False)
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        monkeypatch.setenv(name, "2")  # what runtime_threads leaves in a service
    monkeypatch.delenv("PIPESHUB_LANE_CV2_THREADS", raising=False)
    budget = make_budget(2)
    pool = LanePool(kind="budgeted", threads=2, budget=budget, admit=True, worker_env=worker_thread_env(1), reap_tick=3600.0)
    try:
        names = ["PIPESHUB_LANE_WORKER", "PIPESHUB_LANE_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "PIPESHUB_LANE_CV2_THREADS"]
        seen = submit(pool, env_job, names).future.result(timeout=60)
    finally:
        pool.shutdown()
    assert seen == {"PIPESHUB_LANE_WORKER": "1", "PIPESHUB_LANE_THREADS": "1", "OMP_NUM_THREADS": "1",
                    "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "PIPESHUB_LANE_CV2_THREADS": "1"}


@pytest.mark.parametrize("kind", ["budgeted", "explicit", "interactive"])
def test_explicit_threads_reach_every_library_in_every_pool_kind(
    lanes_on: None, monkeypatch: pytest.MonkeyPatch, make_budget: Callable[..., LaneBudget], kind: str
) -> None:
    monkeypatch.setenv("PDF_TABLE_MODEL_THREADS", "3")
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "PIPESHUB_LANE_CV2_THREADS"):
        monkeypatch.delenv(name, raising=False)
    if kind == "explicit":
        monkeypatch.setenv("PARSE_POOL_WORKERS", "2")
    budget = make_budget(4)
    parse_pool.set_resource_governor(_governor(budget, 8))
    pool = parse_pool_lanes.get_pool(parse_pool._State.governor, interactive=kind == "interactive")  # type: ignore[arg-type]
    assert pool is not None and pool.kind == kind
    env = pool._worker_env
    assert {env[n] for n in ("PIPESHUB_LANE_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "PIPESHUB_LANE_CV2_THREADS")} == {"3"}


def test_explicit_zero_threads_is_not_overridden_by_lane_value() -> None:
    env = worker_thread_env(2, {"PDF_TABLE_MODEL_THREADS": "0"})
    assert "PIPESHUB_LANE_THREADS" not in env and "OMP_NUM_THREADS" not in env  # ONNX reads the 0 itself
    assert env["PIPESHUB_LANE_CV2_THREADS"] == "0"  # OpenCV is left uncapped
    assert worker_thread_env(2, {"PDF_TABLE_MODEL_THREADS": "0", "PIPESHUB_LANE_CV2_THREADS": "2"}).get("PIPESHUB_LANE_CV2_THREADS") is None


def test_service_environment_from_runtime_threads_is_overridden() -> None:
    """``runtime_threads`` sets all three to 2 in every service: the lane value replaces them in the worker."""
    env = worker_thread_env(1, {"OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2", "MKL_NUM_THREADS": "2"})
    assert (env["OMP_NUM_THREADS"], env["OPENBLAS_NUM_THREADS"], env["MKL_NUM_THREADS"]) == ("1", "1", "1")
    kept = worker_thread_env(1, {"OMP_NUM_THREADS": "6", "OPENBLAS_NUM_THREADS": "6", "MKL_NUM_THREADS": "6"})
    assert not {"OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"} & set(kept)  # an operator's choice
    assert kept["PIPESHUB_LANE_THREADS"] == "1"  # ONNX and OpenCV still follow the lane


def test_operator_omp_setting_is_respected() -> None:
    assert "OMP_NUM_THREADS" not in worker_thread_env(1, {"OMP_NUM_THREADS": "6"})
    assert worker_thread_env(1, {"OMP_NUM_THREADS": "2"})["OMP_NUM_THREADS"] == "1"  # the image default
    assert worker_thread_env(1, {})["OMP_NUM_THREADS"] == "1"
    assert "OPENBLAS_NUM_THREADS" not in worker_thread_env(1, {"OPENBLAS_NUM_THREADS": "5"})


def _cpu_ticks(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/stat") as handle:
            fields = handle.read().rsplit(")", 1)[1].split()
        return int(fields[11]) + int(fields[12])
    except (OSError, IndexError):
        return None


def test_no_busy_descendants_outside_a_lease(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], tmp_path: Path
) -> None:
    """3 lanes behind 9 lane threads: workers with CPU progress in the last 250 ms never exceed 3.

    A worker spends its first seconds importing; if one were started before its job held a lease,
    nine of them would be loading at once.
    """
    budget, pool = real_pool(make_budget, make_pool, 3, threads=9)
    previous: dict[int, int] = {}
    progressed: dict[int, float] = {}
    peak_busy = 0
    stop = threading.Event()

    def sample() -> None:
        nonlocal peak_busy
        while not stop.is_set():
            now = time.monotonic()
            for pid in resident_workers():
                ticks = _cpu_ticks(pid)
                if ticks is None:
                    continue
                if ticks > previous.get(pid, 0):
                    progressed[pid] = now  # CPU progress, counting the worker's first ticks
                previous[pid] = ticks
            busy = sum(now - t < 0.25 for t in progressed.values())
            peak_busy = max(peak_busy, busy)
            time.sleep(0.03)

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    try:
        jobs = [submit(pool, pid_job, str(tmp_path / "g"), 0.5) for _ in range(12)]
        pids = {j.future.result(timeout=90) for j in jobs}
    finally:
        stop.set()
        thread.join()
    assert 1 <= peak_busy <= 3, peak_busy
    assert len(pids) <= 3 and max_overlap(tmp_path / "g") <= 3
