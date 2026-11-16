"""The member budget's rules (pure): who may hold how many CPU tokens, and in what order.

One member (container or pod) has ``C`` CPU tokens and ``M`` bytes. A unit of work reserves its
threads (``Demand.cpu``) and its memory estimate once, by the component that runs it. No I/O, no
clock, no globals: the ledger in :mod:`.member_budget` calls these under its lock.

Critical (latency-class) work: ``inspect`` and ``query_embed``, and ``docling``, ``parse`` and
``embed`` units of cost class ``small``. The sum of their floors is one reserve that large and huge
work never use. Idle reserve tokens are lent to any critical family, and when a family below its own
floor has waiters, a freed token goes to it first.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


class Family(str, enum.Enum):
    DOCLING = "docling"
    PARSE = "parse"
    INSPECT = "inspect"
    RASTER = "raster"
    EMBED = "embed"
    QUERY_EMBED = "query_embed"
    QUERY_PARSE = "query_parse"
    LLM = "llm"


SMALL, LARGE, HUGE = "small", "large", "huge"
_ALWAYS_CRITICAL = frozenset({Family.INSPECT, Family.QUERY_EMBED})
_SMALL_CRITICAL = frozenset({Family.DOCLING, Family.PARSE, Family.EMBED})
# Floors are kept in this order when they do not all fit.
PRECEDENCE = (Family.DOCLING, Family.EMBED, Family.QUERY_EMBED, Family.INSPECT, Family.PARSE)

DOCLING_WEIGHT = 2
EMBED_WEIGHT = 2
QUERY_EMBED_WEIGHT = 2
DESIGN_UTILISATION = 0.55  # rho_d the Docling floor is sized for
DESIGN_ARRIVALS_PER_S_AT_16_CPU = 0.2
DOCLING_SECONDS_PER_PAGE_DEFAULT = 1.0
SMALL_DOCLING_PAGES = 10.5  # a small PDF's mean pages: its service time is pages x s_d


def is_critical(family: Family, cls: str | None) -> bool:
    if family in _ALWAYS_CRITICAL:
        return True
    return family in _SMALL_CRITICAL and cls == SMALL


@dataclass(frozen=True)
class Demand:
    family: Family
    cpu: int  # tokens: the threads the unit runs; 0 for ``llm``
    mem_bytes: int = 0
    cls: str | None = None  # small | large | huge
    ceiling_tokens: int | None = None  # the caller's class or family ceiling, checked with the take
    est_seconds: float | None = None


@dataclass(frozen=True)
class AdmissionPolicy:
    large_min: float = 0.20
    large_cap: float = 0.50
    huge_cap: float = 0.50
    borrow_cap: float = 0.85
    small_recent_s: float = 600.0
    unit_budget_s: float = 300.0
    aging_s: float = 900.0
    aged_min: float = 0.34


@dataclass(frozen=True)
class WaiterHead:
    ticket: int
    cls: str | None
    family: Family
    cpu: int
    mem_bytes: int
    est_seconds: float | None
    enqueue_ms: int
    proc_slot: int = 0

    @property
    def critical(self) -> bool:
        return is_critical(self.family, self.cls)


@dataclass
class LedgerState:
    """What the ledger holds: tokens by ``(family, cls)``, the waiter heads, LLM calls in flight."""

    used: dict[tuple[Family, str | None], int] = field(default_factory=dict)
    heads: list[WaiterHead] = field(default_factory=list)
    llm_used: int = 0
    llm_cap: int = 24
    last_critical_ms: int | None = None

    def total(self) -> int:
        return sum(v for (f, _), v in self.used.items() if f is not Family.LLM)

    def family_used(self, family: Family) -> int:
        return sum(v for (f, _), v in self.used.items() if f is family)

    def class_used(self, cls: str) -> int:
        return sum(v for (f, c), v in self.used.items() if c == cls and not is_critical(f, c) and f is not Family.LLM)

    def critical_family_used(self, family: Family) -> int:
        """Tokens ``family`` holds as critical work: the usage a held floor is measured against."""
        return sum(v for (f, c), v in self.used.items() if f is family and is_critical(f, c))

    def critical_used(self) -> int:
        return sum(v for (f, c), v in self.used.items() if is_critical(f, c))

    def big_used(self) -> int:
        return self.total() - self.critical_used()


@dataclass(frozen=True)
class Floors:
    by_family: Mapping[Family, int]
    desired: Mapping[Family, int]
    docling_warm_workers: int
    docling_max_workers: int
    warnings: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return sum(self.by_family.values())

    def of(self, family: Family) -> int:
        return self.by_family.get(family, 0)


@dataclass(frozen=True)
class Decision:
    ok: bool
    reason: str  # "ok" or what is short: tokens, memory, ceiling, reserve, class_cap, borrow_budget, llm


def slots(fraction: float, cpu_tokens: int) -> int:
    return max(1, math.ceil(fraction * cpu_tokens - 1e-9))


@dataclass(frozen=True)
class DemandEwma:
    """Container-wide demand, kept by the controller: arrivals per second and service seconds."""

    arrivals_per_s: Mapping[Family, float] = field(default_factory=dict)
    service_s: Mapping[Family, float] = field(default_factory=dict)


def docling_design_workers(cpus: float, s_d: float = DOCLING_SECONDS_PER_PAGE_DEFAULT) -> int:
    """``F_d``: workers that keep Docling's utilisation at or below 0.55 at the design arrival rate."""
    arrivals = DESIGN_ARRIVALS_PER_S_AT_16_CPU * cpus / 16.0
    return max(1, math.ceil(arrivals * SMALL_DOCLING_PAGES * s_d / DESIGN_UTILISATION - 1e-9))


def static_floors(cpus: float, s_d: float) -> dict[Family, int]:
    """Static minimum of each critical family's floor, in tokens."""
    return {
        Family.DOCLING: docling_design_workers(cpus, s_d) * DOCLING_WEIGHT,
        Family.EMBED: EMBED_WEIGHT * max(1, round(cpus / 16.0)),
        Family.QUERY_EMBED: QUERY_EMBED_WEIGHT,
        Family.INSPECT: 1,
        Family.PARSE: 1,
    }


def plan_floors(
    cpus: float,
    cpu_tokens: int,
    ewma: DemandEwma | None,
    s_d: float,
    policy: AdmissionPolicy,
    *,
    docling_workers_cap: int | None = None,
) -> Floors:
    """Held floors: ``max(static_min, ceil(arrivals x service / 0.55) x w)`` per critical family.

    Their sum is bounded by ``C - slots(largeMin)``. Over that, floors are kept in the order
    docling, embed, query_embed, inspect, parse and ``critical_floor_over_capacity`` is reported:
    the workload is never shrunk silently. ``docling_workers_cap`` (memory) bounds the Docling
    workers; a floor above it is reported as ``docling_floor_capped``.
    """
    warnings: list[str] = []
    desired = static_floors(cpus, s_d)
    weights = {Family.DOCLING: DOCLING_WEIGHT, Family.EMBED: EMBED_WEIGHT, Family.QUERY_EMBED: QUERY_EMBED_WEIGHT,
               Family.INSPECT: 1, Family.PARSE: 1}
    if ewma is not None:
        for fam, arrivals in ewma.arrivals_per_s.items():
            if fam in desired and (service := ewma.service_s.get(fam)):
                measured = math.ceil(arrivals * service / DESIGN_UTILISATION - 1e-9) * weights[fam]
                desired[fam] = max(desired[fam], measured)
    bound = max(0, cpu_tokens - slots(policy.large_min, cpu_tokens))
    docling_max = max(1, bound // DOCLING_WEIGHT)
    if docling_workers_cap is not None and docling_workers_cap < docling_max:
        docling_max = max(1, docling_workers_cap)
    held: dict[Family, int] = {}
    left = bound
    for fam in PRECEDENCE:
        want = desired[fam]
        if fam is Family.DOCLING:
            want_held = min(want, docling_max * DOCLING_WEIGHT)
            if want_held < want and "docling_floor_capped" not in warnings:
                warnings.append("docling_floor_capped")
        else:
            want_held = want
        w = weights[fam]
        take = min(want_held, (left // w) * w)
        held[fam] = take
        left -= take
    if sum(held.values()) < sum(desired.values()):
        warnings.append("critical_floor_over_capacity")
    warm = min(held[Family.DOCLING] // DOCLING_WEIGHT, docling_max) if held[Family.DOCLING] else 0
    return Floors(held, desired, max(1, warm), docling_max, tuple(warnings))


def _critical_floor_shortfall(state: LedgerState, floors: Floors) -> int:
    return sum(max(0, floors.of(f) - state.critical_family_used(f)) for f in PRECEDENCE)


def _effective_large_min(state: LedgerState, policy: AdmissionPolicy, now_ms: int) -> float:
    for h in state.heads:
        if not h.critical and now_ms - h.enqueue_ms >= policy.aging_s * 1000:
            return policy.aged_min
    return policy.large_min


def small_recent(state: LedgerState, policy: AdmissionPolicy, now_ms: int) -> bool:
    if state.family_used(Family.INSPECT) or state.critical_used():
        return True
    if any(h.critical for h in state.heads):
        return True
    last = state.last_critical_ms
    return last is not None and now_ms - last < policy.small_recent_s * 1000


def admit(
    state: LedgerState,
    d: Demand,
    floors: Floors,
    policy: AdmissionPolicy,
    cpu_tokens: int,
    mem_avail: int,
    now_s: float,
) -> Decision:
    """May ``d`` be admitted now? Pure. A wait is ``ok=False``; there is no refusal for contention.

    An empty ledger admits one unit whatever its size (I1': a unit larger than C must still run).
    """
    now_ms = int(now_s * 1000)
    if d.family is Family.LLM:
        return Decision(state.llm_used + 1 <= state.llm_cap, "ok" if state.llm_used + 1 <= state.llm_cap else "llm")
    total = state.total()
    if total == 0:
        return Decision(True, "ok")
    if d.mem_bytes > mem_avail and total > 0:
        return Decision(False, "memory")
    if total + d.cpu > cpu_tokens:
        return Decision(False, "tokens")
    cls = d.cls
    if d.ceiling_tokens is not None:
        if is_critical(d.family, cls):
            # A held-floor family's ceiling is its own width: count what it holds as critical work
            # (``class_used`` leaves critical work out, so the check would never see it).
            used = state.critical_family_used(d.family)
        else:
            used = state.class_used(cls) if cls else state.family_used(d.family)
        if used + d.cpu > d.ceiling_tokens:
            return Decision(False, "ceiling")
    large_min = slots(_effective_large_min(state, policy, now_ms), cpu_tokens)
    if is_critical(d.family, cls):
        big_waiting = any(not h.critical for h in state.heads)
        guard = max(state.big_used(), large_min if big_waiting else 0)
        if state.critical_used() + d.cpu + guard > cpu_tokens:
            return Decision(False, "reserve")
        return Decision(True, "ok")
    big_after = state.big_used() + d.cpu
    if big_after <= large_min:
        return Decision(True, "ok")
    if d.est_seconds is not None and d.est_seconds > policy.unit_budget_s:
        return Decision(False, "borrow_budget")
    if big_after > cpu_tokens - floors.total:
        return Decision(False, "reserve")
    recent = small_recent(state, policy, now_ms)
    cap = slots(policy.large_cap if recent else policy.borrow_cap, cpu_tokens)
    if big_after > cap:
        return Decision(False, "class_cap")
    if cls == HUGE and state.class_used(HUGE) + d.cpu > slots(policy.huge_cap, cpu_tokens):
        return Decision(False, "class_cap")
    return Decision(True, "ok")


@dataclass(frozen=True)
class GrantPlan:
    order: tuple[WaiterHead, ...]  # who gets the next token, in order
    blocked_critical: WaiterHead | None = None  # the oldest critical head that does not fit yet


_CLASS_RANK = {None: 2, SMALL: 1, LARGE: 2, HUGE: 3}


def next_grant(state: LedgerState, floors: Floors, policy: AdmissionPolicy, cpu_tokens: int, now_s: float) -> GrantPlan:
    """The order heads are served in.

    1. the oldest waiter of a critical family that is below its own floor (owner first);
    2. then the oldest latency-class waiter anywhere, ``query_embed`` ahead of the rest;
    3. then big work, large before huge, first come first served. An aged big head only raises
       the big guarantee (see ``admit``); it never moves ahead of small work.
    """
    heads = sorted(state.heads, key=lambda h: (h.enqueue_ms, h.ticket))
    critical = [h for h in heads if h.critical]
    owners = [h for h in critical if state.critical_family_used(h.family) < floors.of(h.family)]
    first = owners[:1]
    rest_critical = sorted(
        (h for h in critical if h not in first),
        key=lambda h: (0 if h.family is Family.QUERY_EMBED else 1, h.enqueue_ms, h.ticket),
    )
    big = sorted((h for h in heads if not h.critical), key=lambda h: (_CLASS_RANK.get(h.cls, 2), h.enqueue_ms, h.ticket))
    order = tuple(first) + tuple(rest_critical) + tuple(big)
    free = cpu_tokens - state.total()
    blocked = next((h for h in order if h.critical and h.cpu > free), None)
    return GrantPlan(order, blocked)


def may_take(state: LedgerState, head: WaiterHead, plan: GrantPlan, cpu_tokens: int) -> bool:
    """Head-of-line: a later head may take tokens only beyond what the blocked critical head needs."""
    if head in plan.order[:1] or plan.blocked_critical is None:
        return True
    if head == plan.blocked_critical:
        return True
    idx = {h: i for i, h in enumerate(plan.order)}
    if idx.get(head, 0) < idx[plan.blocked_critical]:
        return True
    free = cpu_tokens - state.total()
    return free - head.cpu >= plan.blocked_critical.cpu


@dataclass(frozen=True)
class UnitMem:
    est: int
    actual: int = 0  # the worker's RSS; 0 until read


def mem_available(limit: int, headroom: int, baseline_ws: int, units: Sequence[UnitMem]) -> int:
    """``M - baseline_ws - sum(max(actual_i, est_i))``: each byte counted once, never negative."""
    charged = sum(max(u.actual, u.est) for u in units)
    return max(0, limit - headroom - baseline_ws - charged)
