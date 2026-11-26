"""The member budget ledger: reserve once, contention waits, death reclaim, memory counted once, order, LLM cap."""

from __future__ import annotations

import asyncio
import contextlib
import gc
import multiprocessing as mp
import os
import signal
import sys
import threading
import time
from collections import Counter
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from app.capacity import member_budget as mb
from app.capacity.budget_policy import AdmissionPolicy, Demand, Family, plan_floors
from app.capacity.member_budget import AdmissionTimeout, CancelToken, MemberBudget
from tests.support import ledger_procs as lp

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from types import FrameType

GIB = 1 << 30
POLICY = AdmissionPolicy()


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    d = tmp_path / "ledger"
    d.mkdir(mode=0o700)
    return d


def open_budget(ledger: Path, c: int = 18, mem: int = 64 * GIB, llm: int = 24, service: str = "t", cpus: int = 16) -> MemberBudget:
    return MemberBudget(ledger, cpu_tokens=c, mem_bytes=mem, floors=plan_floors(cpus, c, None, 1.0, POLICY),
                        llm_cap=llm, service=service)


def test_the_hot_block_fits_with_every_head_and_the_largest_state() -> None:
    from app.capacity.budget_policy import WaiterHead

    hot = mb._Hot.fresh()
    hot.heads = [WaiterHead(i, "small", Family.DOCLING, 2, 1 << 30, 5.5, 10**13, 200) for i in range(mb.MAX_HEADS)]
    hot.ctl_state = b"x" * mb.STATE_BYTES
    assert len(hot.encode()) <= mb.HOT
    back = mb._decode(hot.encode().ljust(mb.HOT, b"\0"))
    assert back is not None
    assert len(back.heads) == mb.MAX_HEADS
    assert back.ctl_state == hot.ctl_state


def test_reserve_and_release_are_counted_once_and_release_is_idempotent(ledger: Path) -> None:
    b = open_budget(ledger)
    p = b.try_reserve(Demand(Family.DOCLING, 2, GIB, "small"))
    assert p is not None
    snap = b.snapshot()
    assert snap["used"] == 2 and snap["families"]["docling"]["used"] == 2 and snap["mem_charged"] == GIB
    p.release()
    p.release()
    snap = b.snapshot()
    assert snap["used"] == 0 and snap["mem_charged"] == 0


def test_a_permit_is_a_context_manager(ledger: Path) -> None:
    b = open_budget(ledger)
    with b.try_reserve(Demand(Family.PARSE, 1, 0, "small")):
        assert b.snapshot()["used"] == 1
    assert b.snapshot()["used"] == 0


def test_admitted_weight_never_exceeds_c_across_handles(ledger: Path) -> None:
    handles = [open_budget(ledger, service=f"s{i}") for i in range(4)]
    held = []
    for i in range(200):
        p = handles[i % 4].try_reserve(Demand(Family.PARSE, 1, 0, "small"))
        if p is not None:
            held.append(p)
        assert handles[0].snapshot()["used"] <= 18
    assert len(held) <= 18


def test_an_empty_ledger_admits_one_unit_larger_than_c(ledger: Path) -> None:
    b = open_budget(ledger, c=4)
    p = b.try_reserve(Demand(Family.DOCLING, 6, GIB, "large"))
    assert p is not None


def test_reserve_requires_a_deadline_or_a_cancel_token(ledger: Path) -> None:
    b = open_budget(ledger)
    with pytest.raises(ValueError, match="deadline or a cancel token"):
        b.reserve_blocking(Demand(Family.PARSE, 1))
    with pytest.raises(ValueError, match="deadline or a cancel token"):
        asyncio.run(b.reserve(Demand(Family.PARSE, 1)))


def _soon(predicate: Callable[[], bool]) -> bool:
    end = time.monotonic() + 10
    while not predicate() and time.monotonic() < end:
        time.sleep(0.005)
    return predicate()


def test_a_wait_ends_at_the_deadline_and_leaves_no_waiter(ledger: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The wait's clock is frozen and moved by the test: it must still wait a hair before the deadline, and stop at it."""
    b = open_budget(ledger, c=4)
    first = b.try_reserve(Demand(Family.PARSE, 4, 0, "small"))
    assert first is not None
    now = [1000.0]
    monkeypatch.setattr(mb, "time", SimpleNamespace(monotonic=lambda: now[0], time=time.time, sleep=time.sleep))
    checks = [0]
    real_check = MemberBudget._check_stop

    def counted(*args: object) -> None:
        checks[0] += 1
        real_check(*args)

    monkeypatch.setattr(MemberBudget, "_check_stop", staticmethod(counted))
    raised: list[AdmissionTimeout] = []
    stop = CancelToken()  # ends the wait at teardown when a broken deadline would not

    def wait() -> None:
        with contextlib.suppress(mb.Cancelled):
            try:
                b.reserve_blocking(Demand(Family.PARSE, 1, 0, "small"), deadline_s=0.2, cancel=stop)
            except AdmissionTimeout as exc:
                raised.append(exc)

    waiter = threading.Thread(target=wait, daemon=True)
    waiter.start()
    try:
        assert _soon(lambda: b.snapshot()["families"]["parse"]["waiting"] == 1)
        now[0] = 1000.0 + 0.199
        seen = checks[0]
        assert _soon(lambda: checks[0] >= seen + 3 or not waiter.is_alive())  # it has looked at the clock since it moved
        assert waiter.is_alive() and not raised, "the wait ended before its deadline"
        now[0] = 1000.0 + 0.2
        waiter.join(10)
        assert raised, "the wait ran past its deadline"
        assert b.snapshot()["families"]["parse"]["waiting"] == 0
    finally:
        stop.cancel()
        waiter.join(1)  # cancel ends a healthy waiter at once; a deadlocked one must not push the test past pytest's timeout


def test_a_wait_stops_on_cancel_and_a_waiter_is_granted_when_capacity_frees(ledger: Path) -> None:
    b = open_budget(ledger, c=4)
    first = b.try_reserve(Demand(Family.PARSE, 4, 0, "small"))
    token = CancelToken()
    threading.Timer(0.1, token.cancel).start()
    with pytest.raises(mb.Cancelled):
        b.reserve_blocking(Demand(Family.PARSE, 1, 0, "small"), cancel=token)

    async def run() -> float:
        loop = asyncio.get_running_loop()
        loop.call_later(0.1, first.release)
        t = time.monotonic()
        permit = await b.reserve(Demand(Family.PARSE, 1, 0, "small"), deadline_s=5)
        assert permit.cpu == 1
        return time.monotonic() - t

    # wall-clock-ok: the release lands at 0.1 s; the 1 s ceiling is ten times that, far under the 5 s deadline it must beat
    assert 0.08 <= asyncio.run(run()) < 1.0


# ---- memory counted once ------------------------------------------------------------------------


def test_memory_is_charged_max_of_actual_and_estimate_once(ledger: Path) -> None:
    b = open_budget(ledger, mem=10 * GIB)
    p = b.try_reserve(Demand(Family.DOCLING, 2, 3 * GIB, "small"))
    q = b.try_reserve(Demand(Family.DOCLING, 2, 3 * GIB, "small"))
    assert b.snapshot()["mem_available"] == 4 * GIB  # two estimates, nothing observed yet
    p.observe_rss(1 * GIB)  # below its estimate: still charged the estimate
    b.refresh_memory()
    assert b.snapshot()["mem_available"] == 4 * GIB
    q.observe_rss(5 * GIB)  # above: charged 5 GiB, not 3 + 5
    b.refresh_memory()
    snap = b.snapshot()
    assert snap["mem_available"] == 2 * GIB and snap["mem_excess"] == 2 * GIB
    q.release()
    assert b.snapshot()["mem_excess"] == 0 and b.snapshot()["mem_available"] == 7 * GIB
    p.release()


def test_a_unit_that_does_not_fit_in_memory_waits_and_the_baseline_counts(ledger: Path) -> None:
    b = open_budget(ledger, mem=10 * GIB)
    b.set_baseline(6 * GIB)
    first = b.try_reserve(Demand(Family.PARSE, 1, 3 * GIB, "small"))
    assert first is not None
    assert b.try_reserve(Demand(Family.PARSE, 1, 2 * GIB, "small")) is None  # 10 - 6 - 3 = 1 GiB left
    first.release()
    assert b.try_reserve(Demand(Family.PARSE, 1, 2 * GIB, "small")) is not None


def test_a_holder_that_died_returns_its_excess_memory_too(ledger: Path) -> None:
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe()
    b = open_budget(ledger, c=18, mem=10 * GIB)
    proc = ctx.Process(target=lp.hold_and_wait, args=(str(ledger), "docling", 2, child))
    proc.start()
    assert parent.recv() is True
    os.kill(proc.pid, signal.SIGKILL)
    proc.join()
    assert b.snapshot()["mem_charged"] == GIB  # still charged until a reserver needs the capacity
    assert b.reserve_blocking(Demand(Family.DOCLING, 18, 9 * GIB, "small"), deadline_s=3) is not None


# ---- order: class priority, head of line, owner first ---------------------------------------------


def test_small_in_one_handle_is_served_before_large_in_another(ledger: Path) -> None:
    a, c = open_budget(ledger, c=8, service="a"), open_budget(ledger, c=8, service="b")
    held = a.try_reserve(Demand(Family.PARSE, 8, 0, "large"))
    assert held is not None
    big_head = c.register_head(Demand(Family.PARSE, 1, 0, "large"))
    small_head = a.register_head(Demand(Family.DOCLING, 2, GIB, "small"))
    order = [h.cls for h in a.grant_plan().order]
    assert order == ["small", "large"]
    assert a.snapshot()["waiting_by_class"] == {"small": 1, "large": 1}  # one ledger, two handles
    a.withdraw_head(small_head)
    c.withdraw_head(big_head)


def test_the_oldest_small_waiter_keeps_its_tokens_against_later_big_takers(ledger: Path) -> None:
    """Head of line: tokens a blocked small Docling unit needs are not given to a large unit that arrives later."""
    b = open_budget(ledger, c=18)
    hold = [b.try_reserve(Demand(Family.PARSE, 1, 0, "small")) for _ in range(17)]
    assert all(hold)
    head = b.register_head(Demand(Family.DOCLING, 2, GIB, "small"))
    # One token is free; the small head needs two, so a later large unit may not take the free one.
    assert b.try_reserve(Demand(Family.PARSE, 1, 0, "large")) is None
    hold[0].release()
    hold[1].release()
    assert b.try_take(Demand(Family.DOCLING, 2, GIB, "small"), head) is not None


def test_big_work_never_takes_the_held_reserve(ledger: Path) -> None:
    b = open_budget(ledger, c=18)
    floors = b.floors()
    permits = []
    while (p := b.try_reserve(Demand(Family.PARSE, 1, 0, "huge"))) is not None:
        permits.append(p)
    assert len(permits) <= 18 - floors.total
    # ... and the latency class still gets in.
    assert b.try_reserve(Demand(Family.DOCLING, 2, GIB, "small")) is not None


def test_owner_first_reclaim_counts_a_family_below_its_floor(ledger: Path) -> None:
    b = open_budget(ledger, c=18)
    floors = b.floors()
    # Embed borrows the whole reserve while docling idles ...
    embeds = []
    while (p := b.try_reserve(Demand(Family.EMBED, 2, 0, "small"))) is not None:
        embeds.append(p)
    assert b.snapshot()["critical_used"] == 18  # nothing big waits, so the latency class lends itself the whole reserve
    # ... and the docling owner, waiting, is first in line for the next freed token.
    head_d = b.register_head(Demand(Family.DOCLING, 2, GIB, "small"))
    head_e = b.register_head(Demand(Family.EMBED, 2, 0, "small"))
    assert b.grant_plan().order[0].family is Family.DOCLING
    assert floors.of(Family.DOCLING) > 0
    b.withdraw_head(head_d)
    b.withdraw_head(head_e)


def test_set_floors_is_container_wide_and_fenced_to_the_controller(ledger: Path) -> None:
    a, c = open_budget(ledger, service="a"), open_budget(ledger, service="b")
    epoch = a.elect_controller(15)
    bigger = plan_floors(16, 18, None, 3.0, POLICY)
    assert c.set_floors(bigger, epoch=epoch) is False  # not the controller
    assert a.set_floors(bigger, epoch=epoch) is True
    assert c.floors().of(Family.DOCLING) == bigger.of(Family.DOCLING)


# ---- LLM member cap -------------------------------------------------------------------------------


def test_llm_per_member_all_services(ledger: Path) -> None:
    """8 cells plus the parsing and extraction services: the calls in flight in the container never pass the member cap."""
    cap = 72  # 24 per LLM-calling service with USE_PARSING_SERVICE=true
    handles = [open_budget(ledger, llm=cap, service=f"cell{i}") for i in range(8)]
    handles += [open_budget(ledger, llm=cap, service="parsing"), open_budget(ledger, llm=cap, service="extraction")]
    held = []
    for i in range(500):
        p = handles[i % len(handles)].try_reserve(Demand(Family.LLM, 0))
        if p is not None:
            held.append(p)
        assert handles[0].snapshot()["llm"]["used"] <= cap
    assert len(held) == cap
    held.pop().release()
    assert handles[3].try_reserve(Demand(Family.LLM, 0)) is not None
    assert handles[0].snapshot()["used"] == 0  # LLM calls hold no CPU tokens


def test_the_llm_cap_is_fixed_by_the_first_process(ledger: Path) -> None:
    first = open_budget(ledger, llm=24)
    second = open_budget(ledger, llm=72)
    assert first.llm_cap == second.llm_cap == 24


# ---- death, fork, torn writes ---------------------------------------------------------------------


def test_death_reclaim(ledger: Path) -> None:
    """A killed permit holder's tokens and memory come back when the next reserver needs them."""
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe()
    b = open_budget(ledger, c=6)
    proc = ctx.Process(target=lp.hold_and_wait, args=(str(ledger), "docling", 6, child), kwargs={})
    os.environ["LEDGER_C"] = "6"
    try:
        proc.start()
    finally:
        del os.environ["LEDGER_C"]
    assert parent.recv() is True
    assert b.try_reserve(Demand(Family.PARSE, 1, 0, "small")) is None  # the holder has all 6
    os.kill(proc.pid, signal.SIGKILL)
    proc.join()
    t = time.monotonic()
    p = b.reserve_blocking(Demand(Family.PARSE, 1, 0, "small"), deadline_s=3)
    # wall-clock-ok: a dead holder's share is taken back at once (ms); the failure is waiting out the 3 s deadline
    assert p is not None and time.monotonic() - t < 1.0
    snap = b.snapshot()
    assert snap["used"] == 1 and snap["processes"] == 1


def test_a_forked_child_does_not_hold_its_parents_lock_alive(ledger: Path) -> None:
    """A child that reserves and dies without releasing has its own slot: its tokens come back, the parent's stay."""
    b = open_budget(ledger, c=18)
    p = b.try_reserve(Demand(Family.PARSE, 4, 0, "small"))
    assert p is not None
    pid = os.fork()
    if pid == 0:  # the child inherited the descriptors; it must take a slot of its own
        try:
            ok = b.try_reserve(Demand(Family.PARSE, 2, 0, "small")) is not None
        finally:
            os._exit(0 if ok else 1)
    _, status = os.waitpid(pid, 0)
    assert os.WEXITSTATUS(status) == 0
    b.elect_controller(15)  # a scan: the child's slot is free, because its lock died with it
    snap = b.snapshot()
    assert snap["used"] == 4 and snap["processes"] == 1
    p.release()
    assert b.snapshot()["used"] == 0


def test_a_torn_write_leaves_the_previous_copy_valid(ledger: Path) -> None:
    b = open_budget(ledger)
    a = b.try_reserve(Demand(Family.PARSE, 3, 0, "small"))
    assert a is not None
    gen = b.snapshot()["generation"]
    # The newest copy is half written: its CRC fails, so readers use the previous one.
    newest = (gen % 2) * mb.HOT
    fd = os.open(ledger / "ledger", os.O_RDWR)
    try:
        os.pwrite(fd, b"\xff" * 64, newest + 100)
    finally:
        os.close(fd)
    snap = b.snapshot()
    assert snap["generation"] == gen - 1  # the previous generation
    assert snap["used"] in (0, 3)  # a consistent state, not a mix


def test_release_after_close_is_harmless(ledger: Path) -> None:
    b = open_budget(ledger)
    p = b.try_reserve(Demand(Family.PARSE, 1, 0, "small"))
    b.close()
    p.release()
    other = open_budget(ledger, service="o")
    assert other.snapshot()["used"] == 0


def test_a_restart_with_other_sizes_and_nobody_alive_starts_a_fresh_ledger(ledger: Path) -> None:
    a = open_budget(ledger, c=18)
    a.try_reserve(Demand(Family.PARSE, 5, 0, "small"))
    a.close()
    b = open_budget(ledger, c=36)
    assert b.cpu_tokens == 36 and b.snapshot()["used"] == 0


def test_an_unsafe_directory_is_refused(tmp_path: Path) -> None:
    d = tmp_path / "open"
    d.mkdir()
    d.chmod(0o777)
    with pytest.raises(RuntimeError, match="not a safe ledger directory"):
        open_budget(d)


# ---- many processes -------------------------------------------------------------------------------


def _run_hammer(ledger: Path, procs: int, seconds: float) -> list[tuple[int, int]]:
    ctx = mp.get_context("spawn")
    pipes = [ctx.Pipe() for _ in range(procs)]
    start = time.time() + 3.0
    ps = [ctx.Process(target=lp.hammer, args=(str(ledger), seconds, i + 1, pipes[i][1], start)) for i in range(procs)]
    for p in ps:
        p.start()
    results = [pipes[i][0].recv() for i in range(procs)]
    for p in ps:
        p.join(30)
    return results


def test_ledger_multiprocess(ledger: Path) -> None:
    """Sixteen processes reserve and release: nothing is refused for contention, and nothing is left held."""
    results = _run_hammer(ledger, 16, 1.0)
    assert sum(r[0] for r in results) > 0
    assert sum(r[1] for r in results) == 0, "a refusal with C=160 and at most 32 tokens held is a contention refusal"
    b = lp.open_budget(str(ledger))
    snap = b.snapshot()
    assert snap["used"] == 0 and snap["processes"] == 1


def _cpus() -> int:
    """CPUs this process may use (not every platform has ``sched_getaffinity``)."""
    return len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)


def _skip_on_small_hosts() -> None:
    if _cpus() < 4 and "LEDGER_MIN_PAIRS_PER_S" not in os.environ:
        pytest.skip(f"{_cpus()} CPUs cannot run 16 contending processes; set LEDGER_MIN_PAIRS_PER_S to run it on a smaller host")


def _pair_cost(budget: MemberBudget) -> tuple[int, int, int, Counter[str]]:
    """The Python calls, builtin calls, bytecode instructions and system calls (by name) of one reserve+release pair."""

    def pair() -> None:
        permit = budget.try_reserve(Demand(Family.EMBED, 2, 0, "small"))
        assert permit is not None
        permit.release()

    work: Counter[str] = Counter()
    system: Counter[str] = Counter()

    def profile(_frame: object, event: str, arg: object) -> None:
        work[event] += 1
        if event == "c_call":
            name = f"{getattr(arg, '__module__', None) or ''}.{getattr(arg, '__name__', '?')}"
            if name.startswith(("posix.", "os.", "_io.", "fcntl.", "time.", "select.")):
                system[name] += 1

    def trace(frame: FrameType, event: str, _arg: object) -> object:
        if event == "call":
            frame.f_trace_opcodes = True
        elif event == "opcode":
            work["opcode"] += 1  # a loop over the process table that makes no call still shows here
        return trace

    pair()
    for _ in range(2):  # Python 3.12 counts a code object's instructions from the run after it was first traced
        work.clear()
        system.clear()
        previous_profile, previous_trace, collecting = sys.getprofile(), sys.gettrace(), gc.isenabled()
        gc.disable()  # a collection inside one measured pair would run weak-reference callbacks in that pair only
        sys.setprofile(profile)
        sys.settrace(trace)
        try:
            pair()
        finally:
            sys.settrace(previous_trace)
            sys.setprofile(previous_profile)
            if collecting:
                gc.enable()
    return work["call"], work["c_call"], work["opcode"], system


def test_ledger_throughput(ledger: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A reserve+release pair, admission logic included, is two short ledger transactions whose cost does not grow
    with the processes attached: no scan of the process table, no extra lock round trip, no wait or sync.

    Counted, not timed: a pairs-per-second floor across 16 processes failed on a busy runner (4668 < 5000) with
    nothing slow in the ledger."""
    with contextlib.ExitStack() as handles:
        one = open_budget(ledger, service="one")
        handles.callback(one.close)
        alone = _pair_cost(one)
        for i in range(128):  # 129 attached fill three occupancy words, so per-word work differs from one process
            handles.callback(open_budget(ledger, service=f"p{i}").close)
        assert one.snapshot()["processes"] == 129
        crowded = _pair_cost(one)
        print(f"pair alone {alone}\npair with 129 attached {crowded}")
        assert crowded == alone, "a reserve+release pair costs more with more processes attached"
        system = crowded[3]
        assert system["fcntl.flock"] == 4, "one ledger lock and unlock per reserve and per release"
        synced = ("posix.open", "_io.open", "posix.fsync", "posix.fdatasync", "time.sleep")
        assert not any(system[name] for name in synced), system

        # One read of the whole slot table costs the same at any size: the reads must stay off it.
        reads: list[tuple[int, int]] = []
        real_pread = os.pread
        monkeypatch.setattr(os, "pread", lambda fd, n, at: reads.append((at, n)) or real_pread(fd, n, at))
        permit = one.try_reserve(Demand(Family.EMBED, 2, 0, "small"))
        assert permit is not None
        permit.release()
        monkeypatch.undo()
        assert reads and all(at + n <= mb.TABLE_OFFSET or at >= mb.CHANGE_OFFSET for at, n in reads), reads


def test_no_sample_shows_more_than_c_held(ledger: Path) -> None:
    ctx = mp.get_context("spawn")
    pipes = [ctx.Pipe() for _ in range(6)]
    start = time.time() + 3.0
    ps = [ctx.Process(target=lp.hammer, args=(str(ledger), 1.5, i + 1, pipes[i][1], start)) for i in range(6)]
    for p in ps:
        p.start()
    watcher = lp.open_budget(str(ledger), "watch")
    worst = 0
    while time.time() < start + 1.5:
        worst = max(worst, watcher.snapshot()["used"])
    for i, p in enumerate(ps):
        pipes[i][0].recv()
        p.join(30)
    assert 0 <= worst <= lp.C


def test_the_small_file_floor_holds_under_bulk_load(ledger: Path) -> None:
    """Eight processes keep large units flowing; a burst of 20 small Docling units still gets its tokens at once,
    because large work never takes the held reserve (C = 160, floors 82, so large work stops at 78)."""
    import concurrent.futures as cf

    _skip_on_small_hosts()  # eight loader processes and wall-clock bounds: scheduling delay on a small host is not a ledger defect
    ctx = mp.get_context("spawn")
    until = time.time() + 40  # generous: the window must outlast the loaders' start-up on a slow host
    pipes = [ctx.Pipe() for _ in range(8)]
    procs = [ctx.Process(target=lp.bulk_loader, args=(str(ledger), until, pipes[i][1], 1.0), daemon=True) for i in range(8)]
    for p in procs:
        p.start()
    try:
        for parent, _ in pipes:
            assert parent.recv() is True
        b = lp.open_budget(str(ledger), "small")
        deadline = time.monotonic() + 6
        while b.snapshot()["big_used"] < 70 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert 70 <= b.snapshot()["big_used"] <= 160 - b.floors().total, "bulk load is not on the ledger"

        def one(_: int) -> float:
            t = time.monotonic()
            permit = b.reserve_blocking(Demand(Family.DOCLING, 2, 1 << 30, "small"), deadline_s=5)
            wait = time.monotonic() - t
            time.sleep(0.5)
            permit.release()
            return wait

        with cf.ThreadPoolExecutor(20) as pool:
            waits = sorted(pool.map(one, range(20)))
        p95 = waits[int(0.95 * len(waits)) - 1]
        print(f"small docling token wait under bulk load: p95={p95:.3f}s max={waits[-1]:.3f}s big_used={b.snapshot()['big_used']}/160")
        assert p95 < 0.5, f"small token wait p95 {p95:.2f}s under bulk load: {waits}"
        assert b.snapshot()["big_used"] <= 160 - b.floors().total
    finally:
        for p in procs:
            if p.is_alive():
                p.kill()
            p.join(5)
