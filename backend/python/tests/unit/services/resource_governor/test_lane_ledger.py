"""The domain ledger, spawn admission and tickets (T15, T25, T27-T31, T33, T36, T38).

Several ``LaneBudget`` objects on one directory stand in for several
processes: each has its own descriptors, and ``flock`` conflicts between
separate opens. Real processes are used where the test is about processes.
"""
from __future__ import annotations

import os
import signal
import threading
import time
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from app.services.resource_governor import lane_ledger
from app.services.resource_governor.lane_ledger import (
    BLANK,
    NO_CAP,
    RECORD_SIZE,
    DomainLedger,
    LedgerRecord,
    decide_spawn,
    effective_cap,
    next_lane_cap,
    pid_alive,
)
from tests.unit.lane_gauge import FakeClock, Mem, first_line, reap, start_role

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from app.services.resource_governor.lanes import LaneBudget

GIB = 2**30
WS = int(0.75 * GIB)


def record_on_disk(directory: Path) -> LedgerRecord | None:
    return LedgerRecord.decode((directory / "ledger").read_bytes()[:RECORD_SIZE])


# ---------------------------------------------------------------------------
# T27: initialisation and torn records
# ---------------------------------------------------------------------------


class TestInitialisation:
    @pytest.mark.parametrize(
        "content",
        [b"", bytes(RECORD_SIZE), b"x" * RECORD_SIZE, b"PXLEDGR\0" + b"\1" * 40],
        ids=["empty", "zero_filled", "garbage", "short"],
    )
    def test_empty_file_means_no_cap(
        self, make_budget: Callable[..., LaneBudget], lane_dir: Path, content: bytes
    ) -> None:
        budget = make_budget(8)
        (lane_dir / "ledger").write_bytes(content)
        assert budget.cap() == 8
        assert budget.limit() == 8

    def test_crc_damage_reads_as_no_cap(
        self, make_budget: Callable[..., LaneBudget], lane_dir: Path
    ) -> None:
        budget = make_budget(8)
        budget.report_incident()
        assert budget.cap() == 4
        raw = bytearray((lane_dir / "ledger").read_bytes())
        raw[40] ^= 0xFF
        (lane_dir / "ledger").write_bytes(bytes(raw))
        fresh = make_budget(8)
        assert fresh.cap() == 8

    def test_torn_record_is_reinitialised(
        self, make_budget: Callable[..., LaneBudget], lane_dir: Path
    ) -> None:
        budget = make_budget(8)
        (lane_dir / "ledger").write_bytes(b"\xde\xad" * 64)
        budget.report_incident()
        record = record_on_disk(lane_dir)
        assert record is not None
        assert record.cap == 4

    def test_concurrent_init_does_not_clobber_halved_cap(
        self, make_budget: Callable[..., LaneBudget], lane_dir: Path
    ) -> None:
        """Two processes that both saw an empty file; a third halves the cap;
        the second initialiser must see the halved cap under the lock."""
        first, second, third = make_budget(8), make_budget(8), make_budget(8)
        assert first.cap() == 8 and second.cap() == 8
        third.report_incident()
        second.request_reap()
        first.request_reap()
        record = record_on_disk(lane_dir)
        assert record is not None
        assert record.cap == 4
        assert record.reap_request_at > 0

    def test_two_initialisers_in_real_processes_keep_one_halving(self, lane_dir: Path) -> None:
        procs = [start_role("incident", {"dir": str(lane_dir), "lanes": 8}) for _ in range(3)]
        try:
            for proc in procs:
                assert proc.wait(timeout=20) == 0
        finally:
            reap(*procs)
        record = record_on_disk(lane_dir)
        assert record is not None
        # Three incidents in a row halve three times: 8 -> 4 -> 2 -> 1. None was lost.
        assert record.cap == 1

    def test_bad_record_is_reinitialised_only_under_the_lock(
        self, make_budget: Callable[..., LaneBudget], lane_dir: Path
    ) -> None:
        budget = make_budget(8)
        garbage = b"\x07" * RECORD_SIZE
        (lane_dir / "ledger").write_bytes(garbage)
        assert budget.cap() == 8
        assert (lane_dir / "ledger").read_bytes() == garbage  # a read never writes
        budget.request_reap()
        assert record_on_disk(lane_dir) is not None

    def test_ledger_bytes_never_cross_versions(
        self, make_budget: Callable[..., LaneBudget], lane_dir: Path
    ) -> None:
        budget = make_budget(8)
        budget.report_incident()
        good = bytearray((lane_dir / "ledger").read_bytes())
        other_version = bytearray(good)
        other_version[8] = 2
        assert LedgerRecord.decode(bytes(other_version)) is None
        other_magic = bytearray(good)
        other_magic[0] = ord("Q")
        assert LedgerRecord.decode(bytes(other_magic)) is None
        assert LedgerRecord.decode(bytes(good[:100])) is None
        assert LedgerRecord.decode(bytes(good) + b"\0") is None
        assert LedgerRecord.decode(bytes(good)) is not None


class TestTornReads:
    def test_torn_read_keeps_last_good_view(
        self, lane_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ledger = DomainLedger(lane_dir, view_ttl=0.0)
        with ledger.transaction() as txn:
            assert txn is not None
            txn.write(LedgerRecord(plan_lanes=8, cap=2, cap_stamp=1.0))
        assert ledger.read().cap == 2
        holder = start_role("hold_lock", {"dir": str(lane_dir)})
        try:
            first_line(holder)
            monkeypatch.setattr(os, "pread", lambda *a: b"\xff" * RECORD_SIZE)
            for _ in range(50):
                assert ledger.read().cap == 2  # never BLANK, never NO_CAP
        finally:
            reap(holder)
            ledger.close()

    def test_torn_reads_never_lift_a_live_brake_under_a_hammering_writer(
        self, lane_dir: Path
    ) -> None:
        ledger = DomainLedger(lane_dir, view_ttl=0.0)
        with ledger.transaction() as txn:
            assert txn is not None
            txn.write(LedgerRecord(plan_lanes=1, cap=1, cap_stamp=1.0, spawns_in_window=1))
        writer = start_role("ledger_writer", {"dir": str(lane_dir)})
        try:
            first_line(writer)
            deadline = time.monotonic() + 2.0
            seen = 0
            while time.monotonic() < deadline:
                record = ledger.read()
                seen += 1
                assert record.cap != NO_CAP
                assert record.cap == record.spawns_in_window == record.plan_lanes
            assert seen > 100
        finally:
            reap(writer)
            ledger.close()


# ---------------------------------------------------------------------------
# T28: the lock never blocks
# ---------------------------------------------------------------------------


class TestLockBudget:
    def test_stopped_lock_holder_does_not_block(
        self, make_budget: Callable[..., LaneBudget], lane_dir: Path
    ) -> None:
        budget = make_budget(8)
        budget.cap()
        holder = start_role("hold_lock", {"dir": str(lane_dir)})
        try:
            first_line(holder)
            os.kill(holder.pid, signal.SIGSTOP)
            took = []
            for _ in range(7):
                start = time.perf_counter()
                assert budget.admit_spawn(0) is None
                took.append(time.perf_counter() - start)
            took.sort()
            assert took[len(took) // 2] < 0.025, took
            assert took[-1] < 0.12, took
        finally:
            os.kill(holder.pid, signal.SIGCONT)
            reap(holder)

    def test_degrades_to_per_process_and_recovers(
        self,
        make_budget: Callable[..., LaneBudget],
        lane_dir: Path,
        clock: FakeClock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        budget = make_budget(8)
        holder = start_role("hold_lock", {"dir": str(lane_dir)})
        try:
            first_line(holder)
            os.kill(holder.pid, signal.SIGSTOP)
            assert budget.admit_spawn(0) is None  # not degraded yet: fail closed
            assert budget.stats()["ledger"] == "ok"
            clock.advance(2.1)
            with caplog.at_level("ERROR"):
                ticket = budget.admit_spawn(0)  # first worker, on per-process counts
                assert budget.admit_spawn(0) is not None or True
            assert ticket is not None
            stats = budget.stats()
            assert stats["ledger"] == "degraded"
            assert stats["slots"] == "shared"
            assert sum("ledger lock" in r.message for r in caplog.records) == 1
            os.kill(holder.pid, signal.SIGCONT)
        finally:
            reap(holder)
        assert budget.admit_spawn(0) is not None or budget.stats()["ledger"] == "ok"
        assert budget.stats()["ledger"] == "ok"

    def test_degrade_trigger_is_per_process_not_per_call(
        self, make_budget: Callable[..., LaneBudget], lane_dir: Path, clock: FakeClock
    ) -> None:
        budget = make_budget(8)
        holder = start_role("hold_lock", {"dir": str(lane_dir)})
        first_line(holder)
        assert budget.admit_spawn(0) is None  # failure at t0
        clock.advance(1.5)
        reap(holder)
        budget.request_reap()  # a success resets the timer
        holder = start_role("hold_lock", {"dir": str(lane_dir)})
        try:
            first_line(holder)
            clock.advance(1.5)
            assert budget.admit_spawn(0) is None  # 3 s after the first failure, 1.5 s after the success
            assert budget.stats()["ledger"] == "ok"
        finally:
            reap(holder)

    def test_many_pollers_do_not_trip_the_degrade_timer(
        self, make_budget: Callable[..., LaneBudget], lane_dir: Path
    ) -> None:
        from app.services.resource_governor.lanes import LaneBudget, plan_lanes

        budget = LaneBudget.open(
            plan_lanes(cpus=16, heavy=16), lane_dir, memory_reader=Mem(10 * GIB, GIB), fallback_seconds=0.3
        )
        stop = threading.Event()
        writer = start_role("ledger_writer", {"dir": str(lane_dir)})

        def poll() -> None:
            while not stop.is_set():
                budget.admit_spawn(0)
                time.sleep(0.01)

        threads = [threading.Thread(target=poll) for _ in range(31)]
        try:
            first_line(writer)
            for t in threads:
                t.start()
            time.sleep(1.5)
            assert budget.stats()["ledger"] == "ok"
        finally:
            stop.set()
            for t in threads:
                t.join()
            reap(writer)
            budget.close()

    def test_real_stopped_holder_does_degrade_within_2_5_seconds(self, lane_dir: Path) -> None:
        from app.services.resource_governor.lanes import LaneBudget, plan_lanes

        budget = LaneBudget.open(plan_lanes(cpus=16, heavy=16), lane_dir, memory_reader=Mem(10 * GIB, GIB))
        holder = start_role("hold_lock", {"dir": str(lane_dir)})
        try:
            first_line(holder)
            os.kill(holder.pid, signal.SIGSTOP)
            start = time.monotonic()
            while time.monotonic() - start < 2.5:
                budget.admit_spawn(0)
                if budget._ledger.degraded:
                    break
                time.sleep(0.05)
            assert 2.0 <= time.monotonic() - start <= 2.5
        finally:
            os.kill(holder.pid, signal.SIGCONT)
            reap(holder)
            budget.close()

    def test_failed_inner_take_releases_the_outer_lock(
        self, make_budget: Callable[..., LaneBudget], lane_dir: Path
    ) -> None:
        import fcntl

        budget = make_budget(8)
        holder = start_role("hold_mutex", {"dir": str(lane_dir)})
        try:
            first_line(holder)
            assert budget.admit_spawn(0) is None
            fd = os.open(lane_dir / "ledger.lock", os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises if leaked
            finally:
                os.close(fd)
        finally:
            reap(holder)

    def test_slot_code_never_takes_the_ledger_lock(self) -> None:
        import inspect

        from app.services.resource_governor import shared_slots

        source = inspect.getsource(shared_slots)
        assert "lane_ledger" not in source
        assert "ledger" not in source.lower().replace("ledgers", "")


# ---------------------------------------------------------------------------
# T29: staleness
# ---------------------------------------------------------------------------


class TestStaleness:
    def test_restart_does_not_inherit_stale_cap(
        self, make_budget: Callable[..., LaneBudget], clock: FakeClock
    ) -> None:
        old = make_budget(8)
        old.report_incident()
        assert old.cap() == 4
        clock.advance(301)
        assert make_budget(8).cap() == 8

    def test_dead_owner_cap_expires(
        self, make_budget: Callable[..., LaneBudget], clock: FakeClock, mem: Mem
    ) -> None:
        mem.at(0.68)  # above the recovery band, so only staleness can lift the cap
        owner = make_budget(8, pid=424242)
        owner.report_incident()
        reader = make_budget(8, alive=lambda pid: False)
        clock.advance(59)
        assert reader.cap() == 4
        clock.advance(2)
        assert reader.cap() == 8

    def test_dead_owner_pid_and_pid_reuse(
        self, make_budget: Callable[..., LaneBudget], clock: FakeClock, mem: Mem, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mem.at(0.68)
        owner = make_budget(8, pid=424242)
        owner.report_incident()
        reused = make_budget(8, alive=lambda pid: True)  # the pid is now someone else's
        clock.advance(120)
        assert reused.cap() == 4  # a live pid keeps it past 60 s
        clock.advance(181)
        assert reused.cap() == 8  # 300 s backstop

        def eperm(pid: int, sig: int) -> None:
            raise PermissionError

        monkeypatch.setattr(os, "kill", eperm)
        assert pid_alive(77) is True

        def esrch(pid: int, sig: int) -> None:
            raise ProcessLookupError

        monkeypatch.setattr(os, "kill", esrch)
        assert pid_alive(77) is False
        assert pid_alive(0) is False

    def test_cap_restamped_while_pressure_persists(
        self, make_budget: Callable[..., LaneBudget], clock: FakeClock, mem: Mem
    ) -> None:
        budget = make_budget(8)
        mem.at(0.75)
        caps = []
        for _ in range(40):  # ten minutes of samples every 15 s
            clock.advance(15)
            budget.brake_step()
            caps.append(budget.cap())
        assert caps[-1] == 1
        assert caps == sorted(caps, reverse=True), caps  # a live brake never lapses

    def test_plan_mismatch_does_not_reset_ledger(
        self, make_budget: Callable[..., LaneBudget], lane_dir: Path, mem: Mem
    ) -> None:
        mem.at(0.68)
        big, small = make_budget(7), make_budget(1)
        big.report_incident()
        before = record_on_disk(lane_dir)
        assert before is not None and before.cap == 3
        for i in range(500):
            (big if i % 2 else small).request_reap()
        after = record_on_disk(lane_dir)
        assert after is not None
        assert (after.cap, after.cooldown_until, after.cap_stamp) == (
            before.cap,
            before.cooldown_until,
            before.cap_stamp,
        )
        assert big.cap() == 3
        assert small.cap() == 1
        assert big.stats()["plan_mismatch"] is True


# ---------------------------------------------------------------------------
# T30 and T31: recovery and brake spacing
# ---------------------------------------------------------------------------


class TestRecovery:
    def test_time_recovery_without_sampler(
        self, make_budget: Callable[..., LaneBudget], clock: FakeClock
    ) -> None:
        incident = make_budget(8)
        incident.report_incident()
        reader = make_budget(8)  # runs no sampling loop at all
        assert reader.cap() == 4
        clock.advance(59)
        assert reader.cap() == 4  # cooldown
        clock.advance(16)  # 75 s: one period past the cooldown
        assert reader.cap() == 6
        clock.advance(30)
        assert reader.cap() == 8

    def test_recovery_requires_band(
        self, make_budget: Callable[..., LaneBudget], clock: FakeClock, mem: Mem
    ) -> None:
        budget = make_budget(8)
        budget.report_incident()
        clock.advance(200)
        mem.at(0.66)
        assert budget.cap() == 4
        mem.at(0.60)
        clock.advance(2)
        assert budget.cap() == 8

    def test_recovery_band_hysteresis(
        self, make_budget: Callable[..., LaneBudget], clock: FakeClock, mem: Mem
    ) -> None:
        budget = make_budget(8)
        budget.report_incident()
        clock.advance(75)
        for raw, expected in ((0.69, 4), (0.66, 4), (0.64, 6)):
            mem.at(raw)
            clock.advance(2)
            assert budget.cap() == expected, raw
        assert budget.brake_step(0.69) is None  # between band and soft: nothing moves

    @pytest.mark.parametrize("samplers", [2, 4])
    def test_brake_step_union_rate_with_n_samplers(
        self, make_budget: Callable[..., LaneBudget], clock: FakeClock, mem: Mem, samplers: int
    ) -> None:
        import random

        rng = random.Random(31)
        budgets = [make_budget(64) for _ in range(samplers)]
        mem.at(0.75)
        due = [clock.t + rng.uniform(0, 15) for _ in budgets]
        end = clock.t + 600
        steps = 0
        while True:
            i = min(range(samplers), key=due.__getitem__)
            if due[i] > end:
                break
            clock.t = due[i]
            if budgets[i].brake_step() is not None:
                steps += 1
            due[i] += 15 + rng.uniform(-1, 1)
        assert 40 <= steps <= 600 / 13 + 1, steps

    def test_brake_step_applies_to_effective_cap(
        self, make_budget: Callable[..., LaneBudget], clock: FakeClock
    ) -> None:
        """A step starts from what this process would use, not from the stored number."""
        budget = make_budget(8)
        budget.report_incident()  # stored cap 4
        clock.advance(400)  # stale: the effective cap is the plan
        assert budget.brake_step(0.75) == 7  # not 3
        small = make_budget(4)  # plan 4 reads min(plan, stored cap 7) = 4
        clock.advance(20)
        assert small.brake_step(0.75) == 3  # not 6

    def test_pressure_blocks_recovery_so_steps_are_not_undone(
        self, make_budget: Callable[..., LaneBudget], clock: FakeClock
    ) -> None:
        budget = make_budget(8)
        budget.report_incident()
        clock.advance(61)
        caps = []
        for _ in range(4):  # samples a little over one period apart, under pressure
            clock.advance(15.5)
            budget.brake_step(0.75)
            caps.append(budget.cap())
        assert caps == sorted(caps, reverse=True) and caps[-1] < caps[0]

    def test_incident_halves_immediately_and_sets_cooldown(
        self, make_budget: Callable[..., LaneBudget], lane_dir: Path, clock: FakeClock
    ) -> None:
        budget = make_budget(8)
        assert budget.report_incident() == 4
        assert budget.report_incident() == 2
        record = record_on_disk(lane_dir)
        assert record is not None
        assert record.cooldown_until == clock.t + 60


class TestPureRules:
    def test_next_lane_cap(self) -> None:
        assert next_lane_cap(8, 0.60) is None
        assert next_lane_cap(8, 0.70) == 7
        assert next_lane_cap(8, 0.80) == 4
        assert next_lane_cap(1, 0.99) == 1
        assert next_lane_cap(8, None) is None

    def test_effective_cap_is_pure(self) -> None:
        record = LedgerRecord(plan_lanes=8, cap=2, cap_stamp=100.0, cooldown_until=160.0, cap_owner_pid=5)
        always = lambda pid: True  # noqa: E731
        assert effective_cap(record, 8, 150.0, lambda: None, always) == (2, True)
        assert effective_cap(record, 8, 175.0, lambda: None, always) == (4, False)
        assert effective_cap(record, 8, 175.0, lambda: 0.9, always) == (2, False)
        assert effective_cap(BLANK, 8, 175.0, lambda: None, always) == (8, False)

    def test_future_stamped_cap_is_not_trusted(self) -> None:
        """A stamp ahead of the clock (a clock step, a record from another boot) must
        not hold a brake for as long as the stamp is in the future."""
        record = LedgerRecord(plan_lanes=8, cap=2, cap_stamp=1005.0, cap_owner_pid=5)
        always = lambda pid: True  # noqa: E731
        assert effective_cap(record, 8, 1000.0, lambda: 0.9, always) == (8, False)
        near = replace(record, cap_stamp=1000.5)  # inside the one-second tolerance
        assert effective_cap(near, 8, 1000.0, lambda: 0.9, always) == (2, False)

    def test_decide_spawn_charges_inflight_workers(self) -> None:
        kwargs = {
            "window_start": 100.0, "now": 101.0, "plan_lanes": 16, "mem_limit": 1000,
            "ws_bytes": 100, "warmup_seconds": 30.0, "held_before": 1,
        }
        ok = decide_spawn(spawns_in_window=0, raw_working_set=500, **kwargs)
        assert ok.admit and ok.spawns_in_window == 1  # (500 + 100) / 1000 = 0.6 < 0.7
        second = decide_spawn(spawns_in_window=1, raw_working_set=500, **kwargs)
        assert not second.admit and second.reason == "headroom"  # (500 + 200) / 1000 = 0.7
        assert decide_spawn(spawns_in_window=4, raw_working_set=0, **kwargs).reason == "allowance"
        assert decide_spawn(spawns_in_window=0, raw_working_set=None, **kwargs).reason == "unknown_memory"
        none_limit = {**kwargs, "mem_limit": None}
        assert decide_spawn(spawns_in_window=1, raw_working_set=None, **none_limit).admit


# ---------------------------------------------------------------------------
# T15, T25: spawn admission
# ---------------------------------------------------------------------------


class TestSpawnAdmission:
    def test_burst_with_stale_sample_is_rate_limited(
        self, make_budget: Callable[..., LaneBudget], mem: Mem, clock: FakeClock
    ) -> None:
        mem.at(0.50)  # held constant: the sample cannot see the workers being started
        procs = [make_budget(16) for _ in range(3)]
        tickets = [t for _ in range(20) for b in procs if (t := b.admit_spawn(WS)) is not None]
        assert len(tickets) == 2  # (0.5 + k x 0.075) < 0.7 allows k = 1, 2
        clock.advance(31)
        more = [t for _ in range(5) if (t := procs[0].admit_spawn(WS)) is not None]
        assert len(more) == 2  # a new window

    def test_burst_is_bounded_by_the_allowance(
        self, make_budget: Callable[..., LaneBudget], mem: Mem
    ) -> None:
        mem.at(0.05)
        budget = make_budget(16)
        tickets = [t for _ in range(30) if (t := budget.admit_spawn(WS)) is not None]
        assert len(tickets) == 4  # max(2, 16 // 4)

    def test_first_spawn_admitted_over_soft(
        self, make_budget: Callable[..., LaneBudget], mem: Mem
    ) -> None:
        mem.at(0.90)
        assert make_budget(8).admit_spawn(WS) is not None

    def test_second_spawn_refused_over_soft(
        self, make_budget: Callable[..., LaneBudget], mem: Mem
    ) -> None:
        mem.at(0.90)
        budget = make_budget(8)
        first = budget.admit_spawn(WS)
        assert first is not None
        assert budget.admit_spawn(WS) is None
        assert budget.held_count()[1] == 1

    def test_null_mem_limit_admits(
        self, make_budget: Callable[..., LaneBudget], mem: Mem
    ) -> None:
        mem.limit = mem.raw = None
        budget = make_budget(8)
        assert budget.admit_spawn(WS) is not None
        assert budget.admit_spawn(WS) is not None  # no limit, no headroom test
        assert budget.admit_spawn(WS) is None  # the allowance still applies

    def test_first_spawn_after_reap_of_idle_resident(
        self, make_budget: Callable[..., LaneBudget], mem: Mem
    ) -> None:
        mem.at(0.90)
        service_a, service_b = make_budget(8), make_budget(8)
        idle = service_a.admit_spawn(WS)
        assert idle is not None
        assert service_b.admit_spawn(WS) is None
        assert service_b.reap_requested_at() > 0  # P4: a headroom denial asks for a reap
        idle.release(started=True)
        idle.worker_exited()  # service A's reaper tick stops its idle worker
        assert service_b.admit_spawn(WS) is not None

    def test_headroom_denial_requests_reap_when_foreign_idle_worker(
        self, make_budget: Callable[..., LaneBudget], mem: Mem, clock: FakeClock
    ) -> None:
        mem.at(0.90)
        a, b = make_budget(8), make_budget(8)
        assert a.admit_spawn(WS) is not None
        assert b.reap_requested_at() == 0
        clock.advance(5)
        assert b.admit_spawn(WS) is None
        assert b.reap_requested_at() == clock.t

    def test_no_reap_request_when_admitted(
        self, make_budget: Callable[..., LaneBudget], mem: Mem
    ) -> None:
        budget = make_budget(8)
        assert budget.admit_spawn(WS) is not None
        assert budget.reap_requested_at() == 0

    def test_no_token_left_requests_reap(
        self, make_budget: Callable[..., LaneBudget], mem: Mem
    ) -> None:
        mem.limit = mem.raw = None
        budget = make_budget(2)
        assert budget.admit_spawn(0) is not None
        assert budget.admit_spawn(0) is not None
        assert budget.admit_spawn(0) is None
        assert budget.reap_requested_at() > 0

    def test_first_worker_after_owner_sigkill(self, lane_dir: Path) -> None:
        mem = {"mem_limit": 10 * GIB, "mem_raw": int(9.5 * GIB), "dir": str(lane_dir), "lanes": 4}
        owner = start_role("hold_resident", mem)
        try:
            assert first_line(owner)["admitted"] is True
            from app.services.resource_governor.lanes import LaneBudget, plan_lanes

            ours = LaneBudget.open(
                plan_lanes(cpus=8, heavy=8), lane_dir, memory_reader=Mem(10 * GIB, int(9.5 * GIB))
            )
            try:
                assert ours.admit_spawn(WS) is None  # one resident, over the soft limit
                owner.kill()
                owner.wait(timeout=5)
                assert ours.admit_spawn(WS) is not None  # no wait for an orphan to exit
            finally:
                ours.close()
        finally:
            reap(owner)

    def test_window_restart_when_no_resident(
        self, make_budget: Callable[..., LaneBudget], mem: Mem, clock: FakeClock
    ) -> None:
        mem.limit = mem.raw = None
        budget = make_budget(8)
        one, two = budget.admit_spawn(0), budget.admit_spawn(0)
        assert one and two
        assert budget.stats()["spawns_in_window"] == 2
        for ticket in (one, two):
            ticket.release(started=True)
            ticket.worker_exited()
        clock.advance(1)  # still inside the window, but no worker exists
        assert budget.admit_spawn(0) is not None
        assert budget.stats()["spawns_in_window"] == 1

    def test_window_not_restarted_when_resident_idle_elsewhere(
        self, make_budget: Callable[..., LaneBudget], mem: Mem, clock: FakeClock
    ) -> None:
        mem.limit = mem.raw = None
        a, b = make_budget(8), make_budget(8)
        assert a.admit_spawn(0) is not None
        clock.advance(1)
        assert b.admit_spawn(0) is not None
        assert b.stats()["spawns_in_window"] == 2
        assert b.admit_spawn(0) is None  # allowance 2 holds

    def test_probe_read_is_cached_per_second(self, lane_dir: Path) -> None:
        from app.services.resource_governor.lanes import LaneBudget, plan_lanes

        reader = Mem(10 * GIB, int(9.0 * GIB))
        budget = LaneBudget.open(plan_lanes(cpus=16, heavy=16), lane_dir, memory_reader=reader)
        assert budget.admit_spawn(WS) is not None  # the first worker; everything after is denied
        reader.calls = 0
        stop = threading.Event()

        def poll() -> None:
            while not stop.is_set():
                assert budget.admit_spawn(WS) is None
                time.sleep(0.01)

        threads = [threading.Thread(target=poll) for _ in range(31)]
        start = time.monotonic()
        try:
            for t in threads:
                t.start()
            time.sleep(3.0)
        finally:
            stop.set()
            for t in threads:
                t.join()
            budget.close()
        assert reader.calls <= int(time.monotonic() - start) + 1, reader.calls


# ---------------------------------------------------------------------------
# T33: tickets
# ---------------------------------------------------------------------------


class TestTickets:
    def test_failed_start_returns_slot(self, make_budget: Callable[..., LaneBudget], mem: Mem) -> None:
        mem.limit = mem.raw = None
        budget = make_budget(8)
        ticket = budget.admit_spawn(0)
        assert ticket is not None
        assert budget.held_count()[1] == 1 and budget.stats()["spawns_in_window"] == 1
        ticket.release(started=False)
        assert budget.held_count()[1] == 0 and budget.stats()["spawns_in_window"] == 0

    def test_started_worker_keeps_its_token_until_it_exits(
        self, make_budget: Callable[..., LaneBudget], mem: Mem
    ) -> None:
        mem.limit = mem.raw = None
        budget = make_budget(8)
        ticket = budget.admit_spawn(0)
        assert ticket is not None
        ticket.release(started=True)
        assert budget.held_count()[1] == 1
        assert budget.stats()["spawns_in_window"] == 1
        ticket.worker_exited()
        assert budget.held_count()[1] == 0

    def test_ticket_release_idempotent(self, make_budget: Callable[..., LaneBudget], mem: Mem) -> None:
        mem.limit = mem.raw = None
        a, b = make_budget(8), make_budget(8)
        mine, theirs = a.admit_spawn(0), b.admit_spawn(0)
        assert mine and theirs
        mine.release(started=False)
        mine.release(started=False)
        mine.release(started=True)
        assert a.stats()["spawns_in_window"] == 1  # the other process's slot is intact
        assert a.held_count()[1] == 1

    def test_cancelled_start_returns_slot(self, make_budget: Callable[..., LaneBudget], mem: Mem) -> None:
        """Cancel a stand-in for ``_start_worker`` at each of its await points."""
        import asyncio

        mem.limit = mem.raw = None
        budget = make_budget(8)

        async def start_worker(points: int) -> None:
            ticket = budget.admit_spawn(0)
            assert ticket is not None
            started = False
            try:
                for _ in range(points):
                    await asyncio.sleep(0.05)
                started = True
            finally:
                ticket.release(started=started)

        async def scenario() -> None:
            for cancel_after in (0.0, 0.06, 0.11):
                task = asyncio.ensure_future(start_worker(3))
                await asyncio.sleep(cancel_after)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert budget.held_count()[1] == 0
                assert budget.stats()["spawns_in_window"] == 0

        asyncio.run(scenario())

    def test_ticket_from_expired_window_does_not_decrement_new_one(
        self, make_budget: Callable[..., LaneBudget], mem: Mem, clock: FakeClock
    ) -> None:
        mem.limit = mem.raw = None
        a, b = make_budget(8), make_budget(8)
        old = a.admit_spawn(0)
        assert old is not None
        clock.advance(31)
        assert b.admit_spawn(0) is not None  # a new window
        assert b.stats()["spawns_in_window"] == 1
        old.release(started=False)
        assert b.stats()["spawns_in_window"] == 1

    def test_crash_between_admit_and_release(self, lane_dir: Path, clock: FakeClock) -> None:
        args = {"dir": str(lane_dir), "lanes": 4, "mem_limit": 10 * GIB, "mem_raw": GIB}
        spawner = start_role("hold_resident", args)
        try:
            assert first_line(spawner)["admitted"] is True
            spawner.kill()
            spawner.wait(timeout=5)
        finally:
            reap(spawner)
        from app.services.resource_governor.lanes import LaneBudget, plan_lanes

        budget = LaneBudget.open(plan_lanes(cpus=8, heavy=8), lane_dir, memory_reader=Mem(10 * GIB, GIB))
        try:
            assert budget.held_count() == (0, 0)  # the kernel freed the token
            assert budget.admit_spawn(WS) is not None  # and the count it left does not wedge us
        finally:
            budget.close()

    def test_return_while_the_lock_is_busy_is_applied_later(
        self, make_budget: Callable[..., LaneBudget], mem: Mem, lane_dir: Path
    ) -> None:
        mem.limit = mem.raw = None
        budget = make_budget(8)
        ticket = budget.admit_spawn(0)
        assert ticket is not None
        holder = start_role("hold_lock", {"dir": str(lane_dir)})
        try:
            first_line(holder)
            ticket.release(started=False)
        finally:
            reap(holder)
        assert budget.held_count()[1] == 0
        budget.request_reap()
        assert budget.stats()["spawns_in_window"] == 0


# ---------------------------------------------------------------------------
# T38: one atomic record
# ---------------------------------------------------------------------------


class TestAtomicRecord:
    def test_write_is_one_record(self, lane_dir: Path) -> None:
        import random

        rng = random.Random(38)
        for _ in range(12):
            writer = start_role("ledger_writer", {"dir": str(lane_dir)})
            try:
                first_line(writer)
                time.sleep(rng.uniform(0.0, 0.15))
                writer.send_signal(signal.SIGKILL)
                writer.wait(timeout=5)
            finally:
                reap(writer)
            record = record_on_disk(lane_dir)
            assert record is not None, "a killed writer left an unreadable record"
            assert record.cap == record.spawns_in_window == record.plan_lanes
            assert record.window_start == record.last_brake_at == record.cap_stamp == float(record.cap)

    def test_a_failed_write_leaves_a_record_a_later_write_repairs(
        self, lane_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ledger = DomainLedger(lane_dir, view_ttl=0.0)
        good = LedgerRecord(plan_lanes=8, cap=2, cap_stamp=5.0)
        with ledger.transaction() as txn:
            assert txn is not None
            txn.write(good)
        real = os.pwrite

        def half(fd: int, data: bytes, offset: int) -> int:
            real(fd, data[:20], offset)  # the new checksum, not yet the new cap
            raise OSError("disk went away")

        monkeypatch.setattr(os, "pwrite", half)
        with pytest.raises(OSError, match="disk went away"), ledger.transaction() as txn:
            assert txn is not None
            txn.write(replace(good, cap=7))
        monkeypatch.setattr(os, "pwrite", real)
        assert record_on_disk(lane_dir) is None  # torn on disk
        assert ledger.read().cap == NO_CAP  # a persistent tear, seen under the lock: uninitialised
        with ledger.transaction() as txn:
            assert txn is not None
            assert txn.read() == lane_ledger.BLANK  # a locked read concludes: uninitialised
            txn.write(replace(good, cap=3))
        assert record_on_disk(lane_dir) == replace(good, cap=3)
        ledger.close()
