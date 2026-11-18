"""Per-process ordering of parse waiters, in front of the member budget.

``ClassGate`` decides which local waiter goes next (parse-now first, then class by the admission policy, then
round-robin between orgs and FIFO inside an org) and hands that waiter's demand to ``MemberBudget.reserve``. The
budget alone says whether tokens exist; the gate never counts capacity of its own beyond what it is told.

A step is reserved exactly once: the permit the gate returns is the one the lane adopts. A gate with no budget
is an ordering gate: it releases one waiter at a time up to a capacity it is given, and reserves nothing (the
Docling-routed small PDF, whose conversion the Docling service reserves).
"""
from __future__ import annotations

import asyncio
import bisect
import contextlib
import dataclasses
import itertools
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.modules.parse_engine.admission import (
    AdmissionNumbers,
    ClassDemand,
    Grant,
    next_grant,
    slots,
)
from app.modules.parse_engine.budget_port import (
    AdmissionTimeout,
    Cancelled,
    CancelToken,
    Demand,
    Family,
    MemberBudget,
    Permit,
    capacity_of,
    held_floors_of,
)
from app.modules.parse_engine.cost import CostClass, CostEstimate
from app.services.resource_governor.org_share import OrgShare, max_org_share

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["ClassGate", "GatePermit", "GateRequest", "GrantRecord", "PoolCapacity", "request_from_estimate"]

_BIG = (CostClass.LARGE, CostClass.HUGE)
_POLL_S = 0.5


@dataclass(frozen=True)
class PoolCapacity:
    capacity: int  # P: the tokens of this pool
    held_floors: int = 0  # H: what the budget holds for the small-file path


@dataclass(frozen=True)
class GateRequest:
    cls: CostClass
    family: Family = Family.PARSE
    cpu: int = 1  # the unit this gate counts in (permits of the pool it orders)
    mem_bytes: int = 0
    tokens: int | None = None  # the member budget's weight for the step, when it differs from ``cpu`` (default: ``cpu``)
    est_seconds: float | None = None
    bounded: bool = True
    org: str = ""
    priority: bool = False  # parse-now: head of its class
    # When the work first arrived, in this gate's clock. A hand-back keeps it, so aging counts from the start.
    first_seen_s: float | None = None


def request_from_estimate(est: CostEstimate, *, org: str = "", priority: bool = False, cpu: int = 1) -> GateRequest:
    return GateRequest(
        cls=est.cls,
        family=Family.DOCLING if est.needs_docling_permit else Family.PARSE,
        cpu=cpu,
        mem_bytes=est.mem_bytes,
        est_seconds=est.est_seconds,
        bounded=est.bounded,
        org=org,
        priority=priority,
    )


@dataclass(frozen=True)
class GrantRecord:
    cls: CostClass
    rule: str
    waiting: dict[CostClass, int]


@dataclass(eq=False)
class _Waiter:
    req: GateRequest
    seq: int
    first_seen: float
    future: asyncio.Future[str] = field(repr=False)
    ceiling: int | None = None
    claimed: bool = False

    @property
    def key(self) -> tuple[float, int]:
        return (self.first_seen, self.seq)


class GatePermit:
    """What ``acquire`` returns: the ledger's permit (or none, for an ordering gate) plus the gate's own claim."""

    def __init__(self, gate: ClassGate, waiter: _Waiter, inner: Permit | None) -> None:
        self._gate = gate
        self._w = waiter
        self.inner = inner
        self.cls = waiter.req.cls
        self.family = waiter.req.family
        self.cpu = waiter.req.cpu
        self.released = False

    def adopt(self) -> GatePermit:
        """The lane takes over the release. Nothing is reserved again."""
        if self.inner is not None:
            self.inner.adopt()
        return self

    def upgrade(self, cls: CostClass) -> None:
        """The work turned out to be of another class (its size or page count is now known): the running
        count and the org share move to it, without a requeue. The ledger's own permit keeps the class it
        was reserved under; a gate that reserves tokens decides the class before it reserves."""
        if self.released or cls is self.cls:
            return
        self._gate._reclass(self._w, cls)
        self.cls = cls

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        if self.inner is not None:
            self.inner.release()
        self._gate._finish(self._w)

    def __enter__(self) -> GatePermit:
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


class ClassGate:
    def __init__(
        self,
        name: str,
        *,
        numbers: Callable[[], AdmissionNumbers],
        capacity: Callable[[], PoolCapacity] | None = None,
        budget: MemberBudget | None = None,
        small_waiting_elsewhere: Callable[[], bool] = lambda: False,
        org_share: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        log_size: int = 256,
    ) -> None:
        if capacity is None and budget is None:
            raise ValueError("a gate needs a budget or a capacity")
        self.name = name
        self._numbers = numbers
        self._budget = budget
        self._capacity = capacity or self._from_snapshot
        self._capacity_given = capacity is not None  # a snapshot read takes the ledger's lock: not polled
        self._elsewhere = small_waiting_elsewhere
        self._clock = clock
        self._seq = itertools.count()
        # class -> org -> waiters sorted by (first_seen, seq); class -> parse-now waiters
        self._q: dict[CostClass, dict[str, list[_Waiter]]] = {c: {} for c in CostClass}
        self._prio: dict[CostClass, list[_Waiter]] = {c: [] for c in CostClass}
        self._last_org: dict[CostClass, str | None] = dict.fromkeys(CostClass)
        self._running: dict[CostClass, int] = dict.fromkeys(CostClass, 0)
        self._shares = OrgShare(org_share if org_share is not None else max_org_share())
        self._last_small = float("-inf")
        self.grants: deque[GrantRecord] = deque(maxlen=log_size)
        self.upgrades = 0
        self._seen_capacity: PoolCapacity | None = None
        self._unbounded: list[_Waiter] = []  # waiting big records that cannot be split, oldest first (see A5)
        self._repump_at = float("inf")  # the next time the policy changes by the clock alone (see ``_pump``)

    # -- public ---------------------------------------------------------

    async def acquire(
        self, req: GateRequest, *, deadline_s: float | None = None, cancel: CancelToken | None = None
    ) -> GatePermit:
        """Wait for this request's turn and reserve its tokens. Raises ``AdmissionTimeout`` or ``Cancelled``;
        with no deadline a cancel token is required, so no wait is both unbounded and uncancellable."""
        if deadline_s is None and cancel is None:
            raise ValueError("an unbounded acquire needs a cancel token")
        now = self._clock()
        end = None if deadline_s is None else now + deadline_s
        w = _Waiter(req, next(self._seq), req.first_seen_s if req.first_seen_s is not None else now,
                    asyncio.get_running_loop().create_future())
        self._enqueue(w, now)
        self._pump()
        try:
            while not w.future.done():
                if cancel is not None and cancel.cancelled():
                    raise Cancelled
                left = None if end is None else end - self._clock()
                if left is not None and left <= 0:
                    raise AdmissionTimeout
                if self._capacity_given and (cap := self._capacity()) != self._seen_capacity:
                    self._seen_capacity = cap  # a limit moved under the waiters (a brake lifted, a floor changed)
                    self._pump()
                    if w.future.done():
                        break
                await asyncio.wait({w.future}, timeout=_POLL_S if left is None else min(_POLL_S, left))
                if not w.future.done() and self._clock() >= self._repump_at:
                    self._pump()  # a timer of the policy (small demand no longer recent, aging) came due
            inner: Permit | None = None
            if self._budget is not None:
                left = None if end is None else max(0.0, end - self._clock())
                inner = await self._budget.reserve(
                    Demand(
                        req.family, req.cpu if req.tokens is None else req.tokens, req.mem_bytes, req.cls, w.ceiling,
                        req.est_seconds,
                    ),
                    deadline_s=left,
                    cancel=cancel,
                )
            return GatePermit(self, w, inner)
        except BaseException:
            self._finish(w)
            raise

    def poke(self) -> None:
        """Re-check admission after capacity changed (a brake, a floor move)."""
        self._pump()

    def waiting(self, cls: CostClass) -> int:
        return sum(len(v) for v in self._q[cls].values()) + len(self._prio[cls])

    def running(self, cls: CostClass) -> int:
        return self._running[cls]

    def oldest_wait_s(self, cls: CostClass) -> float:
        """How long the longest-waiting record of *cls* has waited (from its first sight, a hand-back keeps it)."""
        firsts = [q[0].first_seen for q in self._q[cls].values() if q] + [q.first_seen for q in self._prio[cls][:1]]
        return max(0.0, self._clock() - min(firsts)) if firsts else 0.0

    def stats(self) -> dict[str, dict[str, int | float]]:
        return {
            c.value: {
                "running": self._running[c], "waiting": self.waiting(c), "oldest_wait_s": round(self.oldest_wait_s(c), 3)
            }
            for c in CostClass
        }

    # -- internals ------------------------------------------------------

    def _capacity_now(self) -> PoolCapacity:
        """The pool's capacity with the ledger's held floors: a gate given its own capacity still grants
        big work only what the ledger will not refuse."""
        cap = self._capacity()
        if self._budget is not None and self._capacity_given and not cap.held_floors:
            return PoolCapacity(cap.capacity, held_floors_of(self._budget.snapshot()))
        return cap

    def _from_snapshot(self) -> PoolCapacity:
        assert self._budget is not None
        snap = self._budget.snapshot()
        return PoolCapacity(capacity_of(snap), held_floors_of(snap))

    def _enqueue(self, w: _Waiter, now: float) -> None:
        cls = w.req.cls
        if cls is CostClass.SMALL:
            self._last_small = now
        if w.req.priority:
            bisect.insort(self._prio[cls], w, key=lambda x: x.key)
        else:
            bisect.insort(self._q[cls].setdefault(w.req.org, []), w, key=lambda x: x.key)
        if cls in _BIG:
            self._shares.arrive("big", w.req.org)
            if not w.req.bounded:
                bisect.insort(self._unbounded, w, key=lambda x: x.key)

    def _dequeue(self, w: _Waiter) -> bool:
        cls = w.req.cls
        lst = self._prio[cls] if w.req.priority else self._q[cls].get(w.req.org, [])
        try:
            lst.remove(w)
        except ValueError:
            return False
        if not w.req.priority and not lst:
            self._q[cls].pop(w.req.org, None)
        if cls in _BIG and not w.req.bounded:
            with contextlib.suppress(ValueError):
                self._unbounded.remove(w)
        return True

    def _finish(self, w: _Waiter) -> None:
        """The waiter is done: gone from the queue, or its claim is returned."""
        if w.claimed:
            w.claimed = False
            self._running[w.req.cls] -= w.req.cpu
            if w.req.cls is CostClass.SMALL:
                self._last_small = self._clock()
            if w.req.cls in _BIG:
                self._shares.leave("big", w.req.org, w.req.cpu)
        elif self._dequeue(w) and w.req.cls in _BIG:
            self._shares.leave("big", w.req.org)
        if not w.future.done():
            w.future.cancel()
        self._pump()

    def _reclass(self, w: _Waiter, cls: CostClass) -> None:
        old = w.req.cls
        if not w.claimed or old is cls:
            return
        self._running[old] -= w.req.cpu
        self._running[cls] += w.req.cpu
        if old in _BIG:
            self._shares.leave("big", w.req.org, w.req.cpu)
        if cls in _BIG:
            self._shares.arrive("big", w.req.org)
            self._shares.hold("big", w.req.org, w.req.cpu)
        if cls is CostClass.SMALL or old is CostClass.SMALL:
            self._last_small = self._clock()
        w.req = dataclasses.replace(w.req, cls=cls)
        self.upgrades += 1
        self._pump()

    def _oldest_wait(self, cls: CostClass, now: float) -> float:
        firsts = [lst[0].first_seen for lst in self._q[cls].values()] + [p.first_seen for p in self._prio[cls][:1]]
        return max(0.0, now - min(firsts)) if firsts else 0.0

    def _pick(
        self, cls: CostClass, *, bounded_only: bool, big_limit: int, exclude: frozenset[_Waiter] | set[_Waiter] = frozenset()
    ) -> _Waiter | None:
        for w in self._prio[cls]:
            if w in exclude:
                continue
            if w.req.bounded or not bounded_only:
                return w
        orgs = sorted(self._q[cls])
        if not orgs:
            return None
        last = self._last_org[cls]
        start = bisect.bisect_right(orgs, last) if last is not None else 0
        for org in orgs[start:] + orgs[:start]:
            for w in self._q[cls][org]:
                if w in exclude or (bounded_only and not w.req.bounded):
                    continue
                if cls in _BIG and self._shares.over_share("big", org, w.req.cpu, big_limit):
                    break
                return w
        return None

    def _demand(self, now: float, big_limit: int) -> dict[CostClass, ClassDemand]:
        out: dict[CostClass, ClassDemand] = {}
        for c in CostClass:
            waiting = self.waiting(c)
            head_bounded = True
            if waiting and c in _BIG:
                # What the borrowing rules need: is there an eligible bounded waiter. The unbounded
                # head matters only to the guarantee rule, which does not read this flag.
                head_bounded = self._pick(c, bounded_only=True, big_limit=big_limit) is not None
            if waiting and c in _BIG and self._pick(c, bounded_only=False, big_limit=big_limit) is None:
                waiting = 0  # every waiter is over its org share: nothing to grant
            out[c] = ClassDemand(self._running[c], waiting, self._oldest_wait(c, now), head_bounded)
        return out

    def _pump(self) -> None:
        self._pump_grants()
        self._repump_at = self._next_policy_change()

    def _next_policy_change(self) -> float:
        """When a rule of the admission policy next changes by the clock alone: small demand stops being recent (the
        big share rises) or the oldest waiting big record reaches the aging threshold. Waiters recheck then, even if
        no capacity moved."""
        numbers = self._numbers()
        now = self._clock()
        due = float("inf")
        if self._last_small > float("-inf") and not self._running[CostClass.SMALL]:
            expiry = self._last_small + numbers.small_recent_s
            if expiry > now:
                due = min(due, expiry)
        for cls in _BIG:
            oldest = self._oldest_wait(cls, now)
            if 0.0 < oldest < numbers.aging_s:
                due = min(due, now + (numbers.aging_s - oldest))
        return due

    def _aged_unbounded_head(self, now: float, aging_s: float, skipped: set[_Waiter]) -> _Waiter | None:
        """The oldest waiting big record that cannot be split into bounded units, if it has waited ``aging_s``.

        ``_unbounded`` is ordered by arrival, so the aged ones are a prefix and nothing is scanned when there are none.
        A priority waiter among the aged goes first, then the oldest."""
        best: _Waiter | None = None
        for w in self._unbounded:
            if w in skipped:
                continue
            if now - w.first_seen < aging_s:
                break
            if w.req.priority:
                return w
            if best is None:
                best = w
        return best

    def _pump_grants(self) -> None:
        numbers = self._numbers()
        skipped: set[_Waiter] = set()  # waiters this gate will not admit now (over their class ceiling): not retried this pump
        masked: set[CostClass] = set()  # classes whose every waiter is skipped: the next rule is tried
        while True:
            cap = self._capacity_now()
            P, H = cap.capacity, cap.held_floors
            now = self._clock()
            demand = self._demand(now, max(1, P))
            for c in masked:
                demand[c] = dataclasses.replace(demand[c], waiting=0)
            small = demand[CostClass.SMALL]
            since = 0.0 if (small.waiting or small.running) else now - self._last_small
            g = None
            aged = self._aged_unbounded_head(now, numbers.aging_s, skipped) if self._unbounded else None
            if (
                aged is not None
                and P < 2
                and demand[CostClass.SMALL].waiting
            ):
                # A5, for the one case the ordinary rules cannot grant: at a capacity under 2, A1 (which needs room for
                # two while small work waits) never admits a big record that cannot be split, and a steady small stream
                # would starve it. Once it has aged it is served next, whatever its lane and whatever else is waiting (a priority
                # one among the aged heads first): it is overdue, and a priority-lane consumer's own small stream is
                # priority too. When it does not fit yet the other rules go on.
                used_now = sum(self._running.values())
                if not used_now or used_now + aged.req.cpu <= P:
                    g = Grant(aged.req.cls, "A5")
            if g is None:
                g = next_grant(
                    numbers, P, demand, since_small_s=since, held_floors=H, small_waiting_elsewhere=self._elsewhere()
                )
            if g is None:
                return
            bounded_only = g.rule in ("A2", "A4")
            w = aged if g.rule == "A5" else self._pick(g.cls, bounded_only=bounded_only, big_limit=max(1, P), exclude=skipped)
            if w is None:
                if masked or skipped:
                    masked.add(g.cls)
                    continue
                return
            used = sum(self._running.values())
            if used and used + w.req.cpu > P:
                return  # a weighted head waits for room; nothing behind it overtakes
            if g.cls in _BIG:
                above = max(1, P - H)
                if g.rule == "A1" or not w.req.bounded:
                    w.ceiling = slots(numbers.large_min, P)
                elif g.rule == "A2":
                    w.ceiling = min(slots(numbers.aged_min, P), above)
                else:
                    recent = self._running[CostClass.SMALL] > 0 or since < numbers.small_recent_s
                    w.ceiling = min(slots(numbers.large_cap if recent else numbers.borrow_cap, P), above)
                before_huge_cap = w.ceiling
                if g.cls is CostClass.HUGE:
                    # One ceiling per class: huge work can never take large's share.
                    w.ceiling = min(w.ceiling, slots(numbers.huge_cap, P))
                # The huge cap protects large work: it holds only while large work waits (as ``next_grant`` applies it).
                limit = w.ceiling if g.cls is not CostClass.HUGE or self.waiting(CostClass.LARGE) else before_huge_cap
                if self._budget is None and self._running[g.cls] and self._running[g.cls] + w.req.cpu > limit:
                    # An ordering gate has no ledger to enforce the class ceiling: apply it to the whole weight of the
                    # request. A class with nothing running still takes a unit heavier than its ceiling, alone. The
                    # waiter is skipped for this pump: the next one of its class that fits, or the next rule, goes on.
                    skipped.add(w)
                    continue
            skipped.clear()
            masked.clear()
            self.grants.append(GrantRecord(g.cls, g.rule, {c: self.waiting(c) for c in CostClass}))
            self._dequeue(w)
            w.claimed = True
            self._running[g.cls] += w.req.cpu
            if g.cls in _BIG:
                self._shares.hold("big", w.req.org, w.req.cpu)
            self._last_org[g.cls] = w.req.org if not w.req.priority else self._last_org[g.cls]
            w.future.set_result(g.rule)


def route_gate(est: CostEstimate, cpu_gate: ClassGate, docling_gate: ClassGate) -> ClassGate:
    """A Docling-routed small PDF queues for a Docling permit and takes no CPU parse slot."""
    return docling_gate if est.needs_docling_permit else cpu_gate


__all__ += ["route_gate"]
