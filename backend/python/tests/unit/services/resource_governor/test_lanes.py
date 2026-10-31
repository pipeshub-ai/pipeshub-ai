"""The lane budget: leases, tokens, processes, scopes, modes (T4, T5, T9, T12, T13,
T21, T23, T26, T32, T37 and the degraded-mode bounds).

Concurrency is asserted on the independent gauge in ``tests/unit/lane_gauge.py``
(each job body appends its own start and end to a file), never on the lease
table. Pools are wider than the limit so the thread count alone cannot satisfy
an assertion.
"""
from __future__ import annotations

import ast
import asyncio
import builtins
import errno
import io
import json
import os
import signal
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.services.resource_governor import lanes as lanes_module
from app.services.resource_governor.lanes import (
    LaneBudget,
    LaneBudgetClosed,
    LaneWaitCancelled,
    LaneWaitTimeout,
    in_lane_scope,
    lane_scope,
    open_lane_budget,
    plan_lanes,
)
from tests.unit.lane_gauge import (
    Mem,
    first_line,
    gauge_job,
    max_overlap,
    now,
    reap,
    start_role,
)

if TYPE_CHECKING:
    from collections.abc import Callable

GIB = 2**30


def plan(lanes: int) -> lanes_module.LanePlan:
    return plan_lanes(cpus=lanes * 2, heavy=lanes * 2)


# ---------------------------------------------------------------------------
# T4: one budget for every pool, by gauge
# ---------------------------------------------------------------------------


class TestOneBudget:
    def _stress(self, make: Callable[[], LaneBudget | None], pools: list[int], gauge: Path) -> None:
        deadline = time.monotonic() + 1.5

        def worker(budget: LaneBudget | None) -> None:
            while time.monotonic() < deadline:
                lease = None if budget is None else budget.acquire(timeout=5)
                try:
                    gauge_job(gauge, 0.03)
                finally:
                    if lease is not None:
                        lease.release()

        threads = []
        for width in pools:
            budget = make()
            threads += [threading.Thread(target=worker, args=(budget,)) for _ in range(width)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def test_three_pools_share_one_budget_by_gauge(
        self, make_budget: Callable[..., LaneBudget], tmp_path: Path
    ) -> None:
        gauge = tmp_path / "gauge"
        # Parse pool (4 threads), service-process raster (3), Docling (2): 9 against a limit of 3.
        self._stress(lambda: make_budget(3), [4, 3, 2], gauge)
        overlap = max_overlap(gauge)
        assert overlap == 3

    def test_negative_control_off_mode_oversubscribes(self, tmp_path: Path) -> None:
        gauge = tmp_path / "gauge"
        assert open_lane_budget(plan(3), local_share=1, environ={"PARSE_LANES": "off"}) is None
        self._stress(lambda: None, [4, 3, 2], gauge)
        assert max_overlap(gauge) > 3

    def test_a_brake_lowers_the_limit_the_gauge_sees(
        self, make_budget: Callable[..., LaneBudget], tmp_path: Path
    ) -> None:
        budget = make_budget(4)
        budget.report_incident()
        gauge = tmp_path / "gauge"
        self._stress(lambda: budget, [6], gauge)
        assert max_overlap(gauge) == 2

    def test_weighted_lease_takes_its_weight(self, make_budget: Callable[..., LaneBudget]) -> None:
        budget = make_budget(4)
        big = budget.try_acquire(weight=3)
        assert big is not None and budget.held_count()[0] == 3
        assert budget.try_acquire(weight=2) is None
        small = budget.try_acquire()
        assert small is not None
        big.release()
        big.release()
        small.release()
        assert budget.held_count() == (0, 0)

    def test_oversized_weight_runs_alone(self, make_budget: Callable[..., LaneBudget]) -> None:
        budget = make_budget(2)
        lease = budget.try_acquire(weight=5)
        assert lease is not None  # admitted alone, as the heavy slots do
        assert budget.try_acquire() is None

    def test_acquire_timeout_and_cancel(self, make_budget: Callable[..., LaneBudget]) -> None:
        budget = make_budget(1, clock=time.monotonic)
        held = budget.try_acquire()
        assert held is not None
        with pytest.raises(LaneWaitTimeout):
            budget.acquire(timeout=0.05)
        calls = iter([False, False, True])
        with pytest.raises(LaneWaitCancelled):
            budget.acquire(cancelled=lambda: next(calls))
        assert budget.stats()["waiting"] == 0
        held.release()

    def test_acquire_waits_for_a_release(self, make_budget: Callable[..., LaneBudget]) -> None:
        budget = make_budget(1, clock=time.monotonic)
        held = budget.try_acquire()
        assert held is not None
        threading.Timer(0.1, held.release).start()
        lease = budget.acquire(timeout=5)
        assert lease.waited_seconds >= 0.05
        lease.release()

    def test_acquire_async_does_not_block_the_loop(self, make_budget: Callable[..., LaneBudget]) -> None:
        budget = make_budget(1, clock=time.monotonic)
        held = budget.try_acquire()
        assert held is not None

        async def scenario() -> int:
            ticks = 0

            async def ticker() -> None:
                nonlocal ticks
                while True:
                    await asyncio.sleep(0.005)
                    ticks += 1

            task = asyncio.ensure_future(ticker())
            asyncio.get_running_loop().call_later(0.2, held.release)
            lease = await budget.acquire_async(timeout=5)
            lease.release()
            task.cancel()
            return ticks

        assert asyncio.run(scenario()) > 10


# ---------------------------------------------------------------------------
# T5, T26, T32: processes
# ---------------------------------------------------------------------------


class TestAcrossProcesses:
    def test_processes_in_one_domain_share_the_limit(self, lane_dir: Path, tmp_path: Path) -> None:
        gauge = tmp_path / "gauge"
        start_at = now() + 1.0
        args = {
            "dir": str(lane_dir), "lanes": 2, "gauge": str(gauge), "seconds": 3.0,
            "job_seconds": 0.05, "threads": 4, "start_at": start_at,
        }
        procs = [start_role("run", args) for _ in range(3)]
        try:
            results = []
            for proc in procs:
                results.append(json.loads(proc.stdout.read().strip().splitlines()[-1]))
                assert proc.wait(timeout=30) == 0
        finally:
            reap(*procs)
        assert max_overlap(gauge) == 2
        assert sum(r["done"] for r in results) > 30
        assert all(r["max_run"] <= 2 for r in results)  # held_count, under the mutex
        assert all(r["samples"] > 20 for r in results)

    def test_first_spawn_exemption_is_atomic_across_processes(self, lane_dir: Path) -> None:
        """Six processes retry behind a lock that is held for a moment, so they
        all see "no resident" before any of them is let in."""
        for round_no in range(3):
            directory = lane_dir / f"r{round_no}"
            directory.mkdir(parents=True)
            start_at = now() + 1.2
            args = {
                "dir": str(directory), "lanes": 8, "mem_limit": 10 * GIB, "mem_raw": int(9.5 * GIB),
                "ws": int(0.75 * GIB), "start_at": start_at, "stop_at": start_at + 1.0, "calls": 40,
            }
            blip = start_role("lock_blip", {"dir": str(directory), "start_at": start_at - 0.05, "seconds": 0.15})
            procs = [start_role("admit", args) for _ in range(6)]
            try:
                admitted = sum(sum(first_line(p)["admitted"]) for p in procs)
                for p in procs:
                    assert p.wait(timeout=20) == 0
                blip.wait(timeout=20)
            finally:
                reap(blip, *procs)
            assert admitted == 1, admitted

    def test_first_worker_exemption_atomic(self, make_budget: Callable[..., LaneBudget], mem: Mem) -> None:
        """200 rounds, resident set emptied each round, four budgets racing."""
        mem.at(0.95)
        budgets = [make_budget(8) for _ in range(4)]
        for _ in range(200):
            barrier = threading.Barrier(len(budgets))
            tickets: list = []

            def go(b: LaneBudget, barrier: threading.Barrier = barrier, tickets: list = tickets) -> None:
                barrier.wait()
                t = b.admit_spawn(int(0.75 * GIB))
                if t is not None:
                    tickets.append(t)

            threads = [threading.Thread(target=go, args=(b,)) for b in budgets]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert len(tickets) == 1
            tickets[0].release(started=False)

    def test_window_check_and_increment_is_atomic_across_processes(self, lane_dir: Path) -> None:
        holder = start_role(
            "hold_resident",
            {"dir": str(lane_dir), "lanes": 16, "mem_limit": 10 * GIB, "mem_raw": GIB},
        )
        try:
            assert first_line(holder)["admitted"] is True  # a resident exists: nobody is exempt
            start_at = now() + 1.2
            args = {
                "dir": str(lane_dir), "lanes": 16, "mem_limit": 10 * GIB, "mem_raw": GIB,
                "ws": int(0.01 * GIB), "start_at": start_at, "stop_at": start_at + 0.6,
            }
            procs = [start_role("admit", args) for _ in range(8)]
            try:
                admitted = sum(first_line(p)["admitted"][0] for p in procs)
                for p in procs:
                    assert p.wait(timeout=20) == 0
            finally:
                reap(*procs)
            # The holder took one of the allowance of max(2, 16 // 4) = 4.
            assert admitted == 3
        finally:
            reap(holder)

    def test_brake_is_shared_across_processes(self, make_budget: Callable[..., LaneBudget], lane_dir: Path) -> None:
        ours = make_budget(8, clock=time.monotonic)
        assert ours.limit() == 8
        proc = start_role("incident", {"dir": str(lane_dir), "lanes": 8})
        try:
            assert proc.wait(timeout=20) == 0
        finally:
            reap(proc)
        assert ours.limit() == 4
        gauge_held = []
        for _ in range(8):
            lease = ours.try_acquire()
            if lease is not None:
                gauge_held.append(lease)
        assert len(gauge_held) == 4

    def test_killed_holder_frees_its_leases(self, lane_dir: Path) -> None:
        proc = start_role(
            "hold_resident", {"dir": str(lane_dir), "lanes": 4, "mem_limit": 10 * GIB, "mem_raw": GIB}
        )
        budget = LaneBudget.open(plan(4), lane_dir, memory_reader=Mem(10 * GIB, GIB))
        try:
            assert first_line(proc) == {"admitted": True, "leased": True}
            assert budget.held_count() == (1, 1)
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=5)
            deadline = time.monotonic() + 1.0
            while budget.held_count() != (0, 0) and time.monotonic() < deadline:
                time.sleep(0.01)
            assert budget.held_count() == (0, 0)
        finally:
            reap(proc)
            budget.close()

    def test_degraded_resident_never_exceeds_plan_lanes(self, lane_dir: Path) -> None:
        """The ledger lock is unavailable everywhere; tokens still bound the domain."""
        import fcntl

        lane_dir.mkdir(parents=True)
        fd = os.open(lane_dir / "ledger.lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        budgets = [
            LaneBudget.open(
                plan(3), lane_dir, memory_reader=Mem(None, None), fallback_seconds=0.0, pid=7000 + i
            )
            for i in range(3)
        ]
        try:
            tickets = []
            for _ in range(5):
                for b in budgets:
                    t = b.admit_spawn(0)
                    if t is not None:
                        tickets.append(t)
            assert budgets[0].stats()["ledger"] == "degraded"
            assert budgets[0].stats()["slots"] == "shared"
            assert len(tickets) <= 3
            assert budgets[0].held_count()[1] == len(tickets) <= 3
        finally:
            for b in budgets:
                b.close()
            os.close(fd)

    def test_degraded_exemption_is_one_worker_not_one_per_process(self, lane_dir: Path) -> None:
        import fcntl

        lane_dir.mkdir(parents=True)
        fd = os.open(lane_dir / "ledger.lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        over = Mem(10 * GIB, int(9.5 * GIB))
        budgets = [
            LaneBudget.open(plan(8), lane_dir, memory_reader=over, fallback_seconds=0.0, pid=7100 + i)
            for i in range(4)
        ]
        try:
            admitted = [b.admit_spawn(int(0.75 * GIB)) for b in budgets]
            assert sum(t is not None for t in admitted) == 1
        finally:
            for b in budgets:
                b.close()
            os.close(fd)

    def test_live_process_count_drops_when_a_process_is_killed(self, lane_dir: Path) -> None:
        args = {"dir": str(lane_dir), "lanes": 8}
        procs = [start_role("live", args) for _ in range(3)]
        budget = None
        try:
            for p in procs:
                first_line(p)
            budget = LaneBudget.open(plan(8), lane_dir, memory_reader=Mem(None, None))
            assert budget._live_processes() == 4
            procs[0].kill()
            procs[0].wait(timeout=5)
            budget._live_cache = None
            assert budget._live_processes() == 3
            assert len(os.listdir(lane_dir / "presence")) == 3  # the dead one's file was removed
        finally:
            reap(*procs)
            if budget is not None:
                budget.close()

    def test_degraded_cap_is_the_plan_split_by_live_processes(self, lane_dir: Path) -> None:
        import fcntl

        procs = [start_role("live", {"dir": str(lane_dir), "lanes": 8}) for _ in range(3)]
        try:
            for p in procs:
                first_line(p)
            fd = os.open(lane_dir / "ledger.lock", os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            budget = LaneBudget.open(plan(8), lane_dir, memory_reader=Mem(None, None), fallback_seconds=0.0)
            try:
                budget.admit_spawn(0)
                assert budget.cap() == 2  # 8 lanes over four live processes
            finally:
                budget.close()
                os.close(fd)
        finally:
            reap(*procs)


# ---------------------------------------------------------------------------
# A stopped ledger-lock holder must not stall lease callers
# ---------------------------------------------------------------------------


class TestStoppedHolder:
    @staticmethod
    def _p99_ms(budget: LaneBudget, seconds: float) -> tuple[float, float]:
        lease_ms, count_ms = [], []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            start = time.perf_counter()
            lease = budget.try_acquire()
            lease_ms.append((time.perf_counter() - start) * 1000)
            if lease is not None:
                lease.release()
            start = time.perf_counter()
            budget.held_count()
            count_ms.append((time.perf_counter() - start) * 1000)
            time.sleep(0.005)
        lease_ms.sort()
        count_ms.sort()
        return lease_ms[int(0.99 * (len(lease_ms) - 1))], count_ms[int(0.99 * (len(count_ms) - 1))]

    def test_pollers_behind_a_stopped_holder_do_not_stall_leases(self, lane_dir: Path) -> None:
        budget = LaneBudget.open(plan(8), lane_dir, memory_reader=Mem(10 * GIB, GIB), fallback_seconds=0.5)
        holder = start_role("hold_lock", {"dir": str(lane_dir)})
        stop = threading.Event()

        def poll() -> None:
            while not stop.is_set():
                budget.admit_spawn(0)
                time.sleep(0.001)

        pollers = [threading.Thread(target=poll) for _ in range(10)]
        try:
            first_line(holder)
            os.kill(holder.pid, signal.SIGSTOP)
            for t in pollers:
                t.start()
            lease_before, count_before = self._p99_ms(budget, 0.4)  # not yet degraded
            time.sleep(0.8)
            assert budget.stats()["ledger"] == "degraded"
            lease_after, count_after = self._p99_ms(budget, 0.8)
        finally:
            stop.set()
            for t in pollers:
                t.join()
            os.kill(holder.pid, signal.SIGCONT)
            reap(holder)
            budget.close()
        # The lock budget is 20 ms; an unrelated caller must not wait for it at all.
        for value in (lease_before, count_before, lease_after, count_after):
            assert value < 80, (lease_before, count_before, lease_after, count_after)

    def test_a_degraded_process_tries_the_lock_once_per_call(self, lane_dir: Path) -> None:
        budget = LaneBudget.open(plan(8), lane_dir, memory_reader=Mem(10 * GIB, GIB), fallback_seconds=0.0)
        holder = start_role("hold_lock", {"dir": str(lane_dir)})
        try:
            first_line(holder)
            os.kill(holder.pid, signal.SIGSTOP)
            budget.admit_spawn(0)  # fails the lock: now degraded
            assert budget._ledger.degraded
            start = time.perf_counter()
            for _ in range(20):
                budget.admit_spawn(0)
            assert (time.perf_counter() - start) / 20 < 0.01  # no 20 ms retry loop per call
        finally:
            os.kill(holder.pid, signal.SIGCONT)
            reap(holder)
            budget.close()


# ---------------------------------------------------------------------------
# T9: files and descriptors
# ---------------------------------------------------------------------------


class TestDescriptors:
    def test_slot_fds_are_not_inherited(self, make_budget: Callable[..., LaneBudget], lane_dir: Path) -> None:
        import subprocess
        import sys

        make_budget(4)
        mine = [
            fd
            for fd in os.listdir("/proc/self/fd")
            if str(lane_dir) in _link(f"/proc/self/fd/{fd}")
        ]
        assert len(mine) > 100  # 2 slot sets of 64, plus mutexes, ledger, presence
        assert not any(os.get_inheritable(int(fd)) for fd in mine)
        out = subprocess.run(
            [sys.executable, "-c", "import os; print(' '.join(os.listdir('/proc/self/fd')))"],
            capture_output=True, text=True, close_fds=False, check=True,
        ).stdout.split()
        assert not set(out) & set(mine)

    def test_no_fork_without_exec_in_parser_modules(self) -> None:
        root = Path(__file__).resolve().parents[4] / "app" / "modules" / "parsers"
        offences = []
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = ast.unparse(node.func)
                args = [ast.unparse(a) for a in node.args]
                if name in ("os.fork", "os.forkpty"):
                    offences.append((path.name, name))
                if name.endswith(("get_context", "set_start_method")) and args and "fork" in args[0] and "forkserver" not in args[0]:
                    offences.append((path.name, name, args[0]))
                if name in ("multiprocessing.Process", "multiprocessing.Pool", "mp.Process"):
                    offences.append((path.name, name))
                if name.endswith("ProcessPoolExecutor") and not any(k.arg == "mp_context" for k in node.keywords):
                    offences.append((path.name, name))
        assert not offences, offences


def _link(path: str) -> str:
    try:
        return os.readlink(path)
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# T12: close
# ---------------------------------------------------------------------------


class TestClose:
    def test_close_while_waiting(self, make_budget: Callable[..., LaneBudget]) -> None:
        budget = make_budget(1, clock=time.monotonic)
        held = budget.try_acquire()
        assert held is not None
        outcome: list[BaseException | str] = []

        def waiter() -> None:
            try:
                budget.acquire(timeout=10)
                outcome.append("acquired")
            except BaseException as exc:
                outcome.append(exc)

        threads = [threading.Thread(target=waiter) for _ in range(4)]
        for t in threads:
            t.start()
        time.sleep(0.1)
        start = time.monotonic()
        budget.close()
        for t in threads:
            t.join(timeout=2)
        assert time.monotonic() - start < 1.0
        assert len(outcome) == 4
        assert all(isinstance(o, LaneBudgetClosed) for o in outcome), outcome
        held.release()  # after close: no EBADF
        assert budget.held_count() == (0, 0)
        with pytest.raises(LaneBudgetClosed):
            budget.try_acquire()
        with pytest.raises(LaneBudgetClosed):
            budget.admit_spawn(0)
        budget.close()

    def test_close_while_waiting_async(self, make_budget: Callable[..., LaneBudget]) -> None:
        budget = make_budget(1, clock=time.monotonic)
        held = budget.try_acquire()
        assert held is not None

        async def scenario() -> None:
            asyncio.get_running_loop().call_later(0.1, budget.close)
            with pytest.raises(LaneBudgetClosed):
                await budget.acquire_async(timeout=10)

        asyncio.run(scenario())
        held.release()

    def test_close_closes_every_descriptor(self, make_budget: Callable[..., LaneBudget], lane_dir: Path) -> None:
        def mine() -> int:
            return sum(str(lane_dir) in _link(f"/proc/self/fd/{fd}") for fd in os.listdir("/proc/self/fd"))

        before = mine()
        budget = make_budget(4)
        assert mine() > before
        budget.close()
        assert mine() == before

    def test_ticket_release_after_close_is_harmless(self, make_budget: Callable[..., LaneBudget], mem: Mem) -> None:
        mem.limit = mem.raw = None
        budget = make_budget(4)
        ticket = budget.admit_spawn(0)
        assert ticket is not None
        budget.close()
        ticket.release(started=False)
        ticket.worker_exited()


# ---------------------------------------------------------------------------
# T13: the nested scope
# ---------------------------------------------------------------------------


class TestNestedScope:
    def test_nested_scope_is_thread_local(self, make_budget: Callable[..., LaneBudget]) -> None:
        budget = make_budget(1)
        seen: dict[str, bool] = {}
        inside, done = threading.Event(), threading.Event()

        def lane_thread() -> None:
            with lane_scope():
                seen["lane"] = in_lane_scope()
                inside.set()
                done.wait(5)
            seen["after"] = in_lane_scope()

        thread = threading.Thread(target=lane_thread)
        thread.start()
        inside.wait(5)
        seen["other"] = in_lane_scope()
        other = budget.try_acquire()
        assert other is not None and not other.nested  # another thread still needs a lane
        done.set()
        thread.join()
        assert seen == {"lane": True, "other": False, "after": False}
        other.release()

    def test_a_lane_holder_taking_the_budget_again_does_not_deadlock(
        self, make_budget: Callable[..., LaneBudget]
    ) -> None:
        budget = make_budget(1)
        result = []

        def job() -> None:
            lease = budget.acquire(timeout=2)
            with lane_scope():
                nested = budget.acquire(timeout=0.5)  # the raster call inside a lane
                result.append(nested.nested)
                assert budget.held_count()[0] == 1
                nested.release()
            lease.release()

        thread = threading.Thread(target=job)
        thread.start()
        thread.join(5)
        assert result == [True]

    def test_gather_children_do_not_inherit(self) -> None:
        seen: list[bool] = []

        async def child() -> None:
            seen.append(in_lane_scope())

        async def parent() -> None:
            with lane_scope():  # no await inside, as the lane thread does
                assert in_lane_scope()
            await asyncio.gather(child(), child())

        asyncio.run(parent())
        assert seen == [False, False]

    def test_a_scope_in_one_thread_is_invisible_to_a_loop_in_another(self) -> None:
        seen: list[bool] = []
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever)
        thread.start()

        async def child() -> None:
            seen.append(in_lane_scope())

        async def many() -> None:
            await asyncio.gather(child(), child())

        try:
            with lane_scope():
                asyncio.run_coroutine_threadsafe(many(), loop).result(5)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join()
            loop.close()
        assert seen == [False, False]

    def test_release_needs_no_context_of_the_acquirer(self, make_budget: Callable[..., LaneBudget]) -> None:
        budget = make_budget(2)
        lease = budget.try_acquire()
        assert lease is not None
        thread = threading.Thread(target=lease.release)
        thread.start()
        thread.join()
        assert budget.held_count() == (0, 0)

        async def scenario() -> None:
            held = await budget.acquire_async()
            await asyncio.get_running_loop().run_in_executor(None, held.release)

        asyncio.run(scenario())
        assert budget.held_count() == (0, 0)


# ---------------------------------------------------------------------------
# T23, T37: modes
# ---------------------------------------------------------------------------


class TestModes:
    def test_off_mode_opens_nothing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        calls: list[tuple[str, tuple]] = []

        def spy(label: str, real: Callable) -> Callable:
            def wrapper(*args: object, **kwargs: object) -> object:
                calls.append((label, args[:1]))
                return real(*args, **kwargs)

            return wrapper

        monkeypatch.setattr(builtins, "open", spy("open", builtins.open))
        monkeypatch.setattr(io, "open", spy("io.open", io.open))
        monkeypatch.setattr(os, "open", spy("os.open", os.open))
        monkeypatch.setattr(os, "mkdir", spy("os.mkdir", os.mkdir))
        before = sorted(os.listdir(tmp_path))
        for environ in ({}, {"PARSE_LANES": "off"}, {"PARSE_LANES": ""}, {"PARSE_LANES": "ON-ish"}):
            assert open_lane_budget(plan(8), local_share=2, environ=environ) is None
        assert lanes_module.parse_lanes_mode({}) == "off"
        assert calls == []
        assert sorted(os.listdir(tmp_path)) == before

    def test_on_mode_uses_the_domain_directory(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
        budget = open_lane_budget(plan(4), local_share=1, environ={"PARSE_LANES": "on"}, memory_reader=Mem())
        assert budget is not None
        try:
            stats = budget.stats()
            assert stats["slots"] == "shared" and stats["ledger"] == "ok"
            (directory,) = [p for p in tmp_path.iterdir() if p.name.startswith("pipeshub-cpu-lanes-v1-64")]
            assert {"run", "resident", "ledger", "ledger.lock", "presence"} <= {p.name for p in directory.iterdir()}
        finally:
            budget.close()

    @pytest.mark.parametrize(
        "failure",
        [PermissionError(errno.EACCES, "denied"), OSError(errno.EMFILE, "too many open files"), RuntimeError("boom")],
        ids=["permission", "emfile", "unexpected"],
    )
    def test_lane_construction_failures_degrade(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failure: Exception
    ) -> None:
        def broken(*args: object, **kwargs: object) -> None:
            raise failure

        monkeypatch.setattr("app.services.resource_governor.lanes.SharedSlots", broken)
        with caplog.at_level("INFO"):
            budget = open_lane_budget(plan(8), local_share=2, environ={"PARSE_LANES": "on"}, memory_reader=Mem())
        assert budget is not None
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(errors) == 1
        stats = budget.stats()
        assert stats["slots"] == "local" and stats["ledger"] == "none"
        assert budget.limit() == 2  # today's width: this process's share
        first, second, third = budget.try_acquire(), budget.try_acquire(), budget.try_acquire()
        assert first and second and third is None
        assert budget.admit_spawn(0) is not None  # and it still starts workers
        budget.close()

    def test_unwritable_directory_degrades(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        blocker = tmp_path / "file"
        blocker.write_text("x")
        with caplog.at_level("ERROR"):
            budget = open_lane_budget(
                plan(4), local_share=1, environ={"PARSE_LANES": "on"}, directory=blocker / "sub", memory_reader=Mem()
            )
        assert budget is not None and budget.stats()["slots"] == "local"
        budget.close()

    def test_degraded_process_reports_local(self, make_budget: Callable[..., LaneBudget]) -> None:
        shared = make_budget(4)
        local = LaneBudget.local(plan(4), memory_reader=Mem())
        assert shared.stats()["slots"] == "shared"
        assert local.stats()["slots"] == "local"
        local.close()

    def test_stats_shape(self, make_budget: Callable[..., LaneBudget]) -> None:
        stats = make_budget(4).stats()
        assert {
            "mode", "plan", "limit", "cap", "run_in_use_domain", "resident_domain",
            "spawns_in_window", "waiting", "wait_seconds_p95", "slots", "ledger",
            "explicit_private", "unleased",
        } <= set(stats)
        assert stats["unleased"] == {"interactive": 1, "svg_renderers_max": 4}

    def test_replan_after_a_failed_probe(self, make_budget: Callable[..., LaneBudget], clock) -> None:
        answers = iter([None, plan(6)])
        budget = make_budget(1, plan_refresh=lambda: next(answers))
        assert budget.plan.lanes == 1
        clock.advance(61)
        assert budget.plan.lanes == 1  # still failing
        clock.advance(61)
        assert budget.plan.lanes == 6
        clock.advance(61)
        assert budget.plan.lanes == 6
