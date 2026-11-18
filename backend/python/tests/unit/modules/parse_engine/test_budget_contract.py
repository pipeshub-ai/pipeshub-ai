"""One contract suite for the member budget, run against the real ledger and the in-memory fake.

The class gate is written against ``budget_port.MemberBudget``. Its unit tests use the fake, so the fake
must behave like the ledger wherever the gate depends on it; this suite is where that is held.
"""
from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

import pytest

from app.capacity.member_budget import MemberBudget as RealLedger
from app.modules.parse_engine.budget_port import (
    AdmissionTimeout,
    Cancelled,
    CancelToken,
    CostClass,
    Demand,
    Family,
    MemberBudget,
    Permit,
    capacity_of,
    held_floors_of,
)
from tests.support.fake_member_budget import FakeMemberBudget

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

GIB = 1 << 30
S, L, H = CostClass.SMALL, CostClass.LARGE, CostClass.HUGE


class _Token:
    def __init__(self) -> None:
        self._set = threading.Event()

    def cancel(self) -> None:
        self._set.set()

    def cancelled(self) -> bool:
        return self._set.is_set()


@pytest.fixture(params=["fake", "real"])
def make(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Callable[..., MemberBudget]]:
    opened: list[RealLedger] = []

    def build(capacity: int = 20) -> MemberBudget:
        if request.param == "fake":
            return FakeMemberBudget(capacity)
        d = tmp_path / f"ledger{len(opened)}"
        d.mkdir(mode=0o700)
        ledger = RealLedger(d, cpu_tokens=capacity, mem_bytes=64 * GIB, service="contract")
        opened.append(ledger)
        return ledger

    yield build
    for ledger in opened:
        ledger.close()


def test_both_satisfy_the_port(make) -> None:
    budget = make()
    permit = budget.try_reserve(Demand(Family.PARSE, 1, 0, S))
    assert isinstance(permit, Permit)
    assert isinstance(_Token(), CancelToken)
    assert (permit.family, permit.cpu, permit.cls) == (Family.PARSE, 1, S)
    permit.release()


def test_release_is_idempotent_and_adopt_keeps_the_permit(make) -> None:
    budget = make(4)
    permit = budget.try_reserve(Demand(Family.PARSE, 2, 0, L))
    assert permit is not None
    assert permit.adopt() is permit
    assert budget.snapshot()["used"] == 2
    permit.release()
    permit.release()
    assert budget.snapshot()["used"] == 0


def test_try_reserve_is_none_at_capacity_and_never_raises(make) -> None:
    budget = make(2)
    a = budget.try_reserve(Demand(Family.PARSE, 1, 0, S))
    b = budget.try_reserve(Demand(Family.PARSE, 1, 0, S))
    assert a is not None and b is not None
    assert budget.try_reserve(Demand(Family.PARSE, 1, 0, S)) is None
    a.release()
    assert budget.try_reserve(Demand(Family.PARSE, 1, 0, S)) is not None


def test_an_empty_ledger_admits_one_unit_larger_than_the_budget(make) -> None:
    budget = make(2)
    assert budget.try_reserve(Demand(Family.PARSE, 5, 0, S)) is not None


def test_a_wait_needs_a_deadline_or_a_cancel_token(make) -> None:
    budget = make(2)
    with pytest.raises(ValueError):
        asyncio.run(budget.reserve(Demand(Family.PARSE, 1, 0, S)))


def test_a_wait_times_out_with_admission_timeout(make) -> None:
    budget = make(1)
    held = budget.try_reserve(Demand(Family.PARSE, 1, 0, S))
    assert held is not None
    with pytest.raises(AdmissionTimeout):
        asyncio.run(budget.reserve(Demand(Family.PARSE, 1, 0, S), deadline_s=0.15))
    assert budget.snapshot()["families"]["parse"]["waiting"] == 0  # the waiter is gone


def test_a_wait_stops_on_a_cancel_token_method(make) -> None:
    budget = make(1)
    held = budget.try_reserve(Demand(Family.PARSE, 1, 0, S))
    assert held is not None
    token = _Token()
    threading.Timer(0.1, token.cancel).start()
    with pytest.raises(Cancelled):
        asyncio.run(budget.reserve(Demand(Family.PARSE, 1, 0, S), cancel=token))


def test_a_waiter_is_granted_when_capacity_frees(make) -> None:
    budget = make(1)
    held = budget.try_reserve(Demand(Family.PARSE, 1, 0, S))
    assert held is not None

    async def run() -> int:
        asyncio.get_running_loop().call_later(0.1, held.release)
        permit = await budget.reserve(Demand(Family.PARSE, 1, 0, S), deadline_s=5)
        return permit.cpu

    assert asyncio.run(run()) == 1


def test_the_ceiling_is_per_class(make) -> None:
    """a large unit is held to the large ceiling, and huge units do not count against it."""
    budget = make(20)
    large = [budget.try_reserve(Demand(Family.PARSE, 1, 0, L, ceiling_tokens=2, est_seconds=100)) for _ in range(2)]
    assert all(p is not None for p in large)
    assert budget.try_reserve(Demand(Family.PARSE, 1, 0, L, ceiling_tokens=2, est_seconds=100)) is None
    assert budget.try_reserve(Demand(Family.PARSE, 1, 0, H, ceiling_tokens=2, est_seconds=100)) is not None


def test_the_snapshot_carries_what_the_gate_reads(make) -> None:
    budget = make(12)
    snap = budget.snapshot()
    assert capacity_of(snap) == 12
    assert held_floors_of(snap) >= 0
    assert {"used", "families"} <= snap.keys()


def test_the_ledgers_own_cancel_token_stops_a_wait(tmp_path: Path) -> None:
    """The ledger's own ``CancelToken`` is the one ``reserve`` polls with ``cancelled()`` (a method, as the contract says)."""
    from app.capacity.member_budget import CancelToken as LedgerToken

    d = tmp_path / "ledger"
    d.mkdir(mode=0o700)
    ledger = RealLedger(d, cpu_tokens=1, mem_bytes=64 * GIB, service="contract")
    try:
        token = LedgerToken()
        assert callable(token.cancelled) and token.cancelled() is False
        assert ledger.try_reserve(Demand(Family.PARSE, 1, 0, S)) is not None
        threading.Timer(0.1, token.cancel).start()
        with pytest.raises(Cancelled):
            asyncio.run(ledger.reserve(Demand(Family.PARSE, 1, 0, S), cancel=token))
    finally:
        ledger.close()
