"""The 2c edits to the lane budget: standby resident limit, presence taken by pool owners, defaults."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.services.resource_governor import lanes as lanes_module
from app.services.resource_governor.lane_ledger import decide_spawn

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.services.resource_governor.lanes import LaneBudget
    from tests.unit.lane_gauge import FakeClock, Mem

GIB = 2**30


def test_defaults_match_the_measured_working_set() -> None:
    assert lanes_module.DEFAULT_WORKING_SET_GB == 1.25
    assert lanes_module.DEFAULT_WARMUP_SECONDS == 20.0
    assert lanes_module.REAP_MIN_IDLE_SECONDS == 5.0
    assert lanes_module.REAP_TICK_SECONDS == 1.0
    assert lanes_module.REAP_REQUEST_FRESH_SECONDS == 2.0


def test_resident_limit_allows_one_standby_per_extra_process(
    make_budget: Callable[..., LaneBudget], mem: Mem
) -> None:
    mem.limit, mem.raw = 1000 * GIB, 0
    budgets = [make_budget(1) for _ in range(3)]
    assert budgets[0].resident_limit() == 1
    for b in budgets:
        b.register_presence()
    for b in budgets:
        b._live_cache = None
    assert budgets[0].live_processes() == 3
    assert budgets[0].resident_limit() == 3
    tickets = [b.admit_spawn(0) for b in budgets for _ in range(2)]
    # The window allowance may refuse some, the resident limit refuses everything above 3.
    assert 1 <= sum(t is not None for t in tickets) <= 3
    stats = budgets[0].stats()
    assert stats["resident_limit"] == 3 and stats["standby"] == 2


def test_presence_counted_only_for_pool_owning_processes(
    make_budget: Callable[..., LaneBudget],
) -> None:
    owner, private_only = make_budget(2), make_budget(2)
    assert private_only.live_processes() == 1  # opening a budget registers nothing
    owner.register_presence()
    owner._live_cache = private_only._live_cache = None
    assert owner.live_processes() == 1 and private_only.live_processes() == 1
    assert private_only.resident_limit() == 2  # the private-pool process adds no standby
    other = make_budget(2)
    other.register_presence()
    owner._live_cache = None
    assert owner.live_processes() == 2 and owner.resident_limit() == 3
    owner.register_presence()  # idempotent
    owner._live_cache = None
    assert owner.live_processes() == 2


def test_maybe_request_reap_full_uses_resident_limit(
    make_budget: Callable[..., LaneBudget], mem: Mem, clock: FakeClock
) -> None:
    mem.limit, mem.raw = 1000 * GIB, 0
    a, b = make_budget(1), make_budget(1)
    a.register_presence()
    b.register_presence()
    for x in (a, b):
        x._live_cache = None
    first = a.admit_spawn(0)
    clock.advance(30)
    second = b.admit_spawn(0)
    assert first is not None and second is not None  # lanes 1 + 1 standby
    assert a.reap_requested_at() == 0.0  # two of two residents is not "full" for a request
    clock.advance(30)
    assert a.admit_spawn(0) is None and b.admit_spawn(0) is None  # token exhausted at the limit
    assert a.reap_requested_at() == clock.t


def test_slow_start_beyond_warmup_is_still_charged_by_raw_reading() -> None:
    kw = {"plan_lanes": 8, "mem_limit": 100 * GIB, "ws_bytes": int(1.25 * GIB), "warmup_seconds": 20.0, "held_before": 1}
    # 25 s after the first spawn the window has restarted, so the worker that took long
    # to load is no longer charged by the window; its footprint is in the raw reading.
    late = decide_spawn(window_start=1000.0, spawns_in_window=1, now=1025.0, raw_working_set=int(84 * GIB), **kw)
    assert not late.admit and late.reason == "headroom"
    ok = decide_spawn(window_start=1000.0, spawns_in_window=1, now=1025.0, raw_working_set=int(30 * GIB), **kw)
    assert ok.admit and ok.spawns_in_window == 1


def test_reap_request_needs_the_resident_limit_to_be_reached(
    make_budget: Callable[..., LaneBudget], mem: Mem, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused token with fewer residents than the limit (they left meanwhile) asks nobody to stop."""
    mem.limit, mem.raw = 1000 * GIB, 0
    a, b = make_budget(1), make_budget(1)
    for x in (a, b):
        x.register_presence()
        x._live_cache = None
    assert a.resident_limit() == 2
    monkeypatch.setattr(a._resident, "try_acquire", lambda limit, count=1: None)
    monkeypatch.setattr(a._resident, "held_count", lambda budget=1.0: 1)
    assert a.admit_spawn(0) is None
    assert a.reap_requested_at() == 0.0


def test_lease_polling_is_jittered(make_budget: Callable[..., LaneBudget], monkeypatch: pytest.MonkeyPatch) -> None:
    """Waiters must not poll in lock step, or one process can lose every race for the lease."""
    slept: list[float] = []
    monkeypatch.setattr("app.services.resource_governor.lanes.time.sleep", slept.append)
    budget = make_budget(1)
    draws = iter([0.0, 0.5, 0.99])
    budget._rng = lambda: next(draws)
    for _ in range(3):
        budget._backoff(0.1)
    assert slept == pytest.approx([0.05, 0.1, 0.149])
