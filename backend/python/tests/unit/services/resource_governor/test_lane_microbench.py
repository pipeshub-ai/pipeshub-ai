"""Cost of an uncontended lease and of a ledger call against the existing heavy-slot cost (ratio gate).

Counted, not timed: timing two calls on a shared runner raced them (a 1 ms median bound failed at 1.03 ms with
nothing slow in the lease, and earlier absolute p99 bounds at 3.2-3.6 ms against 0.08 ms on a quiet host). The calls
each makes, and the file-lock and other system calls among them, are the same on every host, coverage or not.
"""
from __future__ import annotations

import sys
from collections import Counter
from typing import TYPE_CHECKING

from app.services.resource_governor.lanes import LaneBudget, plan_lanes
from app.services.resource_governor.shared_slots import SharedSlots
from tests.unit.lane_gauge import Mem

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# Calls that wait or force a disk write: an uncontended lease or ledger call makes none of them.
_BLOCKING = ("time.sleep", "posix.fsync", "posix.fdatasync", "os.fsync", "select.select", "select.poll")


class _Cost:
    """The Python calls, builtin calls and system calls (by name) of one call of *fn*, after a warm-up call."""

    def __init__(self, fn: Callable[[], object]) -> None:
        fn()
        self.python = self.builtin = 0
        self.system: Counter[str] = Counter()

        def count(_frame: object, event: str, arg: object) -> None:
            if event == "call":
                self.python += 1
            elif event == "c_call":
                self.builtin += 1
                name = f"{getattr(arg, '__module__', None) or ''}.{getattr(arg, '__name__', '?')}"
                if name.startswith(("posix.", "os.", "fcntl.", "time.", "select.")):
                    self.system[name] += 1

        previous = sys.getprofile()
        sys.setprofile(count)
        try:
            fn()
        finally:
            sys.setprofile(previous)

    @property
    def locks(self) -> int:
        return self.system["fcntl.flock"] + self.system["fcntl.lockf"] + self.system["fcntl.fcntl"]

    @property
    def blocking(self) -> dict[str, int]:
        return {name: self.system[name] for name in _BLOCKING if self.system[name]}

    def __repr__(self) -> str:
        return f"python={self.python} builtin={self.builtin} system={dict(self.system)}"


def _assert_within(cost: _Cost, baseline: _Cost, factor: float, what: str) -> None:
    assert not cost.blocking, (what, cost.blocking)
    assert cost.python <= factor * baseline.python, (what, cost, baseline)
    assert cost.builtin <= factor * baseline.builtin, (what, cost, baseline)
    assert cost.locks <= factor * max(baseline.locks, 1), (what, cost, baseline)


def test_lane_lease_cost_against_heavy_slot_cost(tmp_path: Path) -> None:
    """An uncontended lease costs at most three heavy slots, and never waits or syncs."""
    budget = LaneBudget.open(plan_lanes(cpus=16, heavy=16), tmp_path / "lanes", memory_reader=Mem())
    # The governor sizes its heavy slots by the heavy ceiling, so their cost grows
    # with the host. A 64-core host (heavy 62) is the like-for-like set.
    heavy = SharedSlots(tmp_path / "heavy", 62)

    def lane() -> None:
        lease = budget.try_acquire()
        assert lease is not None
        lease.release()

    def slot() -> None:
        hold = heavy.try_acquire(31)
        assert hold is not None
        hold.release()

    try:
        lane_cost, slot_cost = _Cost(lane), _Cost(slot)
        print(f"lane {lane_cost}\nheavy slot {slot_cost}")
        _assert_within(lane_cost, slot_cost, 3, "lease")
    finally:
        budget.close()
        heavy.close()


def test_ledger_read_and_write_under_no_contention(tmp_path: Path) -> None:
    """An uncontended ledger read or write costs at most three heavy slots, which take the same kind of file lock,
    and never waits or syncs: a ledger call that sleeps, retries or syncs on every use shows in its calls."""
    budget = LaneBudget.open(plan_lanes(cpus=16, heavy=16), tmp_path / "lanes", memory_reader=Mem(), view_ttl=0.0)
    slots = SharedSlots(tmp_path / "heavy", 62)

    def slot() -> None:
        hold = slots.try_acquire(31)
        assert hold is not None
        hold.release()

    try:
        baseline = _Cost(slot)
        for name, call in (("cap", budget.cap), ("request_reap", budget.request_reap)):
            cost = _Cost(call)
            print(f"ledger {name} {cost}")
            _assert_within(cost, baseline, 3, name)
    finally:
        budget.close()
        slots.close()
