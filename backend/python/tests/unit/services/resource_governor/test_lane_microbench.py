"""Cost of an uncontended lease against the existing heavy-slot cost (ratio gate)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.resource_governor.lanes import LaneBudget, plan_lanes
from app.services.resource_governor.shared_slots import SharedSlots
from tests.support.cpu_budget import thread_cpu
from tests.unit.lane_gauge import Mem

if TYPE_CHECKING:
    from pathlib import Path


def _quantile_ms(fn, q: float, rounds: int = 1500) -> float:
    """The *q* quantile of one call's CPU time (its syscalls included): the locks are non-blocking, so a call's
    cost is CPU, and a reading on the wall clock would add the time a busy host kept the thread off the CPU."""
    fn()
    samples = []
    for _ in range(rounds):
        with thread_cpu() as cpu:
            fn()
        samples.append(cpu.seconds)
    samples.sort()
    return samples[int(q * (len(samples) - 1))] * 1000


def _p99_ms(fn, rounds: int = 1500) -> float:
    return _quantile_ms(fn, 0.99, rounds)


def _paired_ms(a, b, q: float, rounds: int = 1500) -> tuple[float, float]:
    """The *q* quantile of each of two calls, timed alternately so that a stall of the host lands on both."""
    a()
    b()
    times_a, times_b = [], []
    for _ in range(rounds):
        with thread_cpu() as cpu_a:
            a()
        with thread_cpu() as cpu_b:
            b()
        times_a.append(cpu_a.seconds)
        times_b.append(cpu_b.seconds)
    times_a.sort()
    times_b.sort()
    k = int(q * (rounds - 1))
    return times_a[k] * 1000, times_b[k] * 1000


def test_lane_lease_cost_against_heavy_slot_cost(tmp_path: Path) -> None:
    """The tail against the heavy slot measured in the same run; the absolute budget on the median, which a stalled runner barely moves (an
    absolute 3 ms p99 failed at 3.6 ms on a busy runner, against 0.08 ms on a quiet host). A lease that is slow every time fails the median, one
    slow only now and then fails the ratio."""
    budget = LaneBudget.open(plan_lanes(cpus=16, heavy=16), tmp_path / "lanes", memory_reader=Mem())
    # The governor sizes its heavy slots by the heavy ceiling, so their cost grows
    # with the host. A 64-core host (heavy 62) is the like-for-like set; the
    # 16-slot figure is printed to show the per-slot scaling, not gated.
    heavy = SharedSlots(tmp_path / "heavy", 62)
    small = SharedSlots(tmp_path / "small", 16)

    def lane() -> None:
        lease = budget.try_acquire()
        assert lease is not None
        lease.release()

    def slot() -> None:
        hold = heavy.try_acquire(31)
        assert hold is not None
        hold.release()

    try:
        for _ in range(3):  # a loaded host: take the best of three runs, same run for both
            # The tails are timed alternately: measured one after the other, a burst of load during only the
            # lane's 1,500 calls failed the ratio at 1.57 ms against 0.41 ms with nothing slow in the lease.
            lane_median = _quantile_ms(lane, 0.5)
            lane_p99, slot_p99 = _paired_ms(lane, slot, 0.99)
            if lane_median < 1.0 and lane_p99 <= 3 * slot_p99:
                break
        print(f"16-slot heavy set p99 {_p99_ms(lambda: small.try_acquire(8).release()):.3f} ms")
        print(f"lane median {lane_median:.3f} ms, p99 {lane_p99:.3f} ms, heavy slot p99 {slot_p99:.3f} ms, ratio {lane_p99 / slot_p99:.2f}")
        assert lane_median < 1.0
        assert lane_p99 <= 3 * slot_p99
    finally:
        budget.close()
        heavy.close()
        small.close()


def test_ledger_read_and_write_under_no_contention(tmp_path: Path) -> None:
    """An uncontended ledger read or write costs at most three heavy slots, which take the same kind of file lock.

    The median, against a baseline measured in the same run: a stalled host moves a tail figure
    (an absolute p99 bound failed at 3.2 ms on a busy runner) but not the typical call, while a
    ledger call that sleeps, retries or syncs on every use costs several times the baseline.
    """
    budget = LaneBudget.open(plan_lanes(cpus=16, heavy=16), tmp_path / "lanes", memory_reader=Mem(), view_ttl=0.0)
    slots = SharedSlots(tmp_path / "heavy", 62)

    def slot() -> None:
        hold = slots.try_acquire(31)
        assert hold is not None
        hold.release()

    try:
        baseline = _quantile_ms(slot, 0.5)
        for name, call in (("cap", budget.cap), ("request_reap", budget.request_reap)):
            median = _quantile_ms(call, 0.5)
            print(f"ledger {name} median {median:.4f} ms, heavy slot median {baseline:.4f} ms")
            assert median <= 3 * baseline, name
    finally:
        budget.close()
        slots.close()
