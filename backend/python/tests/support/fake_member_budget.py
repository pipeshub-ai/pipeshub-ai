"""An in-memory ``MemberBudget`` with the ledger's observable behaviour.

Faithful where the class gate depends on it: sum of admitted weight never above C (an empty ledger admits one
unit), held floors that big work never takes, big work above its guarantee only in bounded units, the atomic
``ceiling_tokens`` check, head-of-line reservation for a weighted head, class order small > large > huge with
FIFO inside a class, ``deadline_s=None`` only with a cancel token, an idempotent ``release``, and ``adopt``.
It is not the ledger: no processes, no files, no EWMAs.
"""
from __future__ import annotations

import asyncio
import itertools
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.modules.parse_engine.budget_port import (
    AdmissionTimeout,
    Cancelled,
    CancelToken,
    CostClass,
    Demand,
    Family,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_BIG = (CostClass.LARGE, CostClass.HUGE)
_CLASS_ORDER = {CostClass.SMALL: 0, CostClass.LARGE: 1, CostClass.HUGE: 2, None: 0}


class FakePermit:
    def __init__(self, budget: FakeMemberBudget, d: Demand, ticket: int) -> None:
        self._budget = budget
        self.family = d.family
        self.cpu = d.cpu
        self.mem_bytes = d.mem_bytes
        self.cls = d.cls
        self.ticket = ticket
        self.released = False
        self.adopted = False

    def release(self) -> None:
        if not self.released:
            self.released = True
            self._budget._release(self)

    def adopt(self) -> FakePermit:
        self.adopted = True
        return self

    def __enter__(self) -> FakePermit:
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


@dataclass
class _Waiter:
    ticket: int
    demand: Demand
    enqueued: float
    future: asyncio.Future[FakePermit] = field(repr=False)


class FakeMemberBudget:
    def __init__(
        self,
        capacity: int,
        *,
        floors: Mapping[Family, int] | None = None,
        large_min: float = 0.20,
        unit_budget_s: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.capacity = capacity
        self.floors: dict[Family, int] = dict(floors or {})
        self.large_min_slots = max(1, int(large_min * capacity))
        self.unit_budget_s = unit_budget_s
        self._clock = clock
        self._live: list[FakePermit] = []
        self._waiters: list[_Waiter] = []
        self._tickets = itertools.count(1)
        self.max_used = 0
        self.reserve_calls = 0
        self.grant_log: list[Demand] = []

    # -- accounting -----------------------------------------------------

    def _used(self, pred: Callable[[FakePermit], bool] = lambda _p: True) -> int:
        return sum(p.cpu for p in self._live if pred(p))

    def _small_used(self) -> int:
        return self._used(lambda p: p.cls not in _BIG)

    def _big_used(self) -> int:
        return self._used(lambda p: p.cls in _BIG)

    def set_capacity(self, capacity: int) -> None:
        """A brake: the ledger never revokes, it only stops admitting above the new C."""
        self.capacity = capacity
        self._pump()

    def _fits(self, d: Demand) -> bool:
        used = self._used()
        if used == 0:
            return True  # an empty ledger admits one unit
        if used + d.cpu > self.capacity:
            return False
        if d.cls in _BIG:
            if d.ceiling_tokens is not None and self._used(lambda p: p.cls == d.cls) + d.cpu > d.ceiling_tokens:
                return False  # the ceiling is per class (a huge unit never takes large's share)
            if self._big_used() + d.cpu <= self.large_min_slots:
                return True  # big's guarantee
            if d.est_seconds is not None and d.est_seconds > self.unit_budget_s:
                return False  # borrowed tokens only in bounded units
            reserve_idle = max(0, sum(self.floors.values()) - self._small_used())
            return used + d.cpu + reserve_idle <= self.capacity
        if d.ceiling_tokens is not None and self._used(lambda p: p.family is d.family) + d.cpu > d.ceiling_tokens:
            return False
        return True

    # -- the Protocol ---------------------------------------------------

    def try_reserve(self, d: Demand) -> FakePermit | None:
        self.reserve_calls += 1
        ahead = [w for w in self._waiters if _CLASS_ORDER[w.demand.cls] <= _CLASS_ORDER[d.cls]]
        if ahead or not self._fits(d):
            return None
        return self._grant(d)

    async def reserve(
        self, d: Demand, *, deadline_s: float | None = None, cancel: CancelToken | None = None
    ) -> FakePermit:
        if deadline_s is None and cancel is None:
            raise ValueError("an unbounded reserve needs a cancel token")
        self.reserve_calls += 1
        loop = asyncio.get_running_loop()
        w = _Waiter(next(self._tickets), d, self._clock(), loop.create_future())
        self._waiters.append(w)
        self._pump()
        end = None if deadline_s is None else self._clock() + deadline_s
        returned = False
        try:
            while not w.future.done():
                if cancel is not None and cancel.cancelled():
                    raise Cancelled
                left = None if end is None else end - self._clock()
                if left is not None and left <= 0:
                    raise AdmissionTimeout
                await asyncio.wait({w.future}, timeout=0.05 if left is None else min(0.05, left))
            returned = True
            return w.future.result()
        finally:
            if w in self._waiters:
                self._waiters.remove(w)
            if not returned:
                if w.future.done() and not w.future.cancelled():
                    w.future.result().release()  # granted just as the caller gave up
                else:
                    w.future.cancel()
            self._pump()

    def snapshot(self) -> dict[str, Any]:
        """The ledger's mapping, the keys the gate and the tests read."""
        fams = {f.value: {"used": 0, "waiting": 0} for f in Family}
        for p in self._live:
            fams[p.family.value]["used"] += p.cpu
        for w in self._waiters:
            fams[w.demand.family.value]["waiting"] += 1
        by_class: dict[str, int] = defaultdict(int)
        for p in self._live:
            by_class[f"{p.family.value}/{p.cls or 'none'}"] += p.cpu
        return {
            "cpu_tokens": self.capacity,
            "used": self._used(),
            "big_used": self._big_used(),
            "families": fams,
            "by_class": dict(by_class),
            "held_floor": sum(self.floors.values()),
            "floors": {f.value: n for f, n in self.floors.items()},
        }

    # -- internals ------------------------------------------------------

    def _grant(self, d: Demand) -> FakePermit:
        p = FakePermit(self, d, next(self._tickets))
        self._live.append(p)
        self.max_used = max(self.max_used, self._used())
        self.grant_log.append(d)
        return p

    def _release(self, p: FakePermit) -> None:
        self._live.remove(p)
        self._pump()

    def _pump(self) -> None:
        """Grant heads in class order, FIFO inside a class. A head that does not fit holds the ones behind it."""
        order = sorted(self._waiters, key=lambda w: (_CLASS_ORDER[w.demand.cls], w.ticket))
        for w in order:
            if w.future.done():
                continue
            if not self._fits(w.demand):
                if _CLASS_ORDER[w.demand.cls] == 0:
                    break  # head-of-line reservation for a small head
                continue
            self._waiters.remove(w)
            w.future.set_result(self._grant(w.demand))
