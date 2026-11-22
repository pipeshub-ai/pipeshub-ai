"""The governor's side of the container controller (``PIPESHUB_CAPACITY_PROFILE=auto`` only).

Each governed process publishes the demand of its last interval into the shared member ledger. The
one elected process (``app.capacity.controller``) sums it, runs ``next_limits`` once for the whole
container on the fleet ceilings, and persists the limits and the ramp state in the ledger, so a
successor resumes instead of warm-starting. Every process then takes its share of the container
limits. A process that finds no usable state (no controller yet, or its beat is stale) keeps the
limits its own ``next_limits`` step gave it, which is what the governor does without the flag.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from typing import TYPE_CHECKING, Any

from app.services.resource_governor.models import (
    Ceilings,
    ControllerState,
    LimitRule,
    Limits,
    Pool,
    PoolDemand,
    PoolState,
)
from app.services.resource_governor.policy import next_limits, warm_start_limits

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.capacity.controller import ElectedController
    from app.capacity.member_budget import MemberBudget
    from app.services.resource_governor.models import FeedbackWindow, ResourceSnapshot

_POOL_STATE_FIELDS = (
    "healthy_streak", "cooldown_until", "in_slow_start", "slow_start_step", "prev_grow_mem_pressure",
    "prev_grow_cpu_utilisation", "hold_ewma", "hold_baseline", "downstream_holdoff",
)


def _round(value: float | None) -> float | None:
    return round(value, 4) if isinstance(value, float) else value


def encode_state(limits: Limits, state: ControllerState) -> dict[str, Any]:
    """Compact JSON for the ledger's state block (about 500 bytes)."""
    return {
        "v": 1,
        "l": {p.value: limits.get(p) for p in Pool},
        "p": {p.value: [_round(getattr(state.get(p), f)) for f in _POOL_STATE_FIELDS] for p in Pool},
    }


def decode_state(raw: Mapping[str, Any] | None) -> tuple[Limits, ControllerState] | None:
    if not raw or raw.get("v") != 1:
        return None
    try:
        limits = Limits(values={p: int(raw["l"][p.value]) for p in Pool})
        pools = {p: PoolState(**dict(zip(_POOL_STATE_FIELDS, raw["p"][p.value], strict=True))) for p in Pool}
    except (KeyError, TypeError, ValueError):
        return None
    return limits, ControllerState(pools=pools)


_INDEX_POOLS = (Pool.INDEX_HEAVY, Pool.INDEX_LIGHT)
_HOLD_RULES = (LimitRule.LATENCY_HOLD, LimitRule.DOWNSTREAM_HOLDOFF)


def _share(total: int, cells: int) -> int:
    return total if cells <= 1 else max(1, total // cells)


class ContainerLink:
    def __init__(self, budget: MemberBudget, controller: ElectedController, fleet: Ceilings, cells: int,
                 logger: logging.Logger | None = None) -> None:
        self.budget = budget
        self.controller = controller
        self.fleet = fleet
        self.cells = max(1, cells)
        self._logger = logger or logging.getLogger(__name__)

    def step(
        self,
        *,
        own: tuple[Limits, ControllerState],
        snap: ResourceSnapshot,
        ceilings: Ceilings,
        demand: Mapping[Pool, PoolDemand],
        now: float,
        interval: float,
        feedback: FeedbackWindow | None,
        heavy_working_set_gb: float | None,
        now_ms: int | None = None,
    ) -> tuple[Limits, ControllerState]:
        """This process's limits for the next interval: its share of the container's, else ``own``."""
        try:
            self.budget.publish_demand({
                p.value: (d.permit_seconds, d.blocked_acquires, d.completions, d.max_in_use) for p, d in demand.items()
            })

            def compute(raw: dict[str, Any] | None, summed: Mapping[str, tuple[float, int, int, int]]) -> dict[str, Any]:
                resumed = decode_state(raw)
                limits, state = resumed if resumed is not None else (warm_start_limits(self.fleet), ControllerState.initial())
                container_demand = {
                    p: PoolDemand(permit_seconds=summed[p.value][0], blocked_acquires=summed[p.value][1],
                                  completions=summed[p.value][2], max_in_use=summed[p.value][3])
                    for p in Pool
                }
                new_limits, new_state = next_limits(
                    current=limits, snap=snap, ceilings=self.fleet, state=state, demand=container_demand,
                    now=now, interval=interval, feedback=feedback, worker_count=1,
                    heavy_working_set_gb=heavy_working_set_gb,
                )
                return encode_state(new_limits, new_state)

            tick = self.controller.tick(compute, now_ms=now_ms)
        except Exception:
            self._logger.exception("capacity controller step failed; keeping this process's own limits")
            return own
        if tick.stale:
            return own
        shared = decode_state(tick.state)
        if shared is None:
            return own
        limits = Limits(values={
            p: (
                max(1, min(_share(shared[0].get(p), self.cells), _cap(p, ceilings)))
                if shared[0].get(p) > 0 and _cap(p, ceilings) > 0  # a pool this process runs without stays without
                else 0
            )
            for p in Pool
        })
        # A local memory incident (the heavy pool halved, with a cooldown) outlasts the shared ramp, which does not see
        # it: while a pool's cooldown runs, this process keeps the lower of its own and the shared limit.
        # The same for an index pool this process holds back to avoid growing into downstream latency: the shared ramp
        # has its own, smoothed view of that and may lift the pool before the local hold ends.
        def holding(p: Pool) -> bool:
            state = own[1].get(p)
            return state.cooldown_until > now or (p in _INDEX_POOLS and state.rule in _HOLD_RULES)

        held = {p: min(limits.get(p), own[0].get(p)) for p in Pool if holding(p)}
        if held:
            limits = Limits(values={p: held.get(p, limits.get(p)) for p in Pool})
        return limits, own[1]

    def stats(self) -> dict[str, Any]:
        snap = self.budget.snapshot()
        return {
            "profile": "auto",
            "role": "controller" if self.controller.is_controller else "follower",
            "controller": snap["controller"],
            "budget": {k: snap[k] for k in ("cpu_tokens", "used", "held_floor", "mem_available", "llm", "families")},
        }


def _cap(pool: Pool, ceilings: Ceilings) -> int:
    return {Pool.HEAVY_PARSE: ceilings.heavy, Pool.LIGHT_PARSE: ceilings.light,
            Pool.INDEX_HEAVY: ceilings.index_heavy, Pool.INDEX_LIGHT: ceilings.index_light}[pool]


@dataclasses.dataclass
class AutoInputs:
    """What ``PIPESHUB_CAPACITY_PROFILE=auto`` changes for one governor."""

    resolve_kwargs: dict[str, Any]
    heavy_cap: int | None
    budget: MemberBudget | None
    tick_s: float
    plan_fleet: Ceilings | None = None  # the whole container's ceilings from the plan

    def partition(self, fleet: Ceilings) -> Ceilings:
        """The Docling and query governors hold heavy parses to their service's share of the member budget."""
        if self.heavy_cap is None or fleet.heavy <= self.heavy_cap:
            return fleet
        return dataclasses.replace(fleet, heavy=max(1, self.heavy_cap))

    def link(self, fleet: Ceilings, ceilings: Ceilings, logger: logging.Logger) -> ContainerLink | None:
        if self.budget is None:
            return None
        from app.capacity.controller import ElectedController

        # The shared ramp belongs to the container, whichever service holds the role: the plan's fleet. It is
        # divided by the processes that actually run (the plan's cell count is only a proposal when fewer start).
        # This service's own heavy cap stays on its own gates (``partition``, ``ceilings``).
        whole = self.plan_fleet or fleet
        cells = max(1, int(self.resolve_kwargs.get("cells", 1)))
        return ContainerLink(self.budget, ElectedController(self.budget, self.tick_s), whole, cells, logger)


def auto_inputs(worker_count: int, tick_s: float = 5.0) -> AutoInputs | None:
    """``None`` unless ``PIPESHUB_CAPACITY_PROFILE=auto``: then the governor is built from the capacity plan."""
    from app.capacity.knobs import capacity_profile

    if capacity_profile() != "auto":
        return None
    from app.capacity import runtime

    plan = runtime.current_plan()
    if plan is None:
        return None
    service = os.environ.get("PIPESHUB_SERVICE", "")
    governor = plan.services[service].governor if service in plan.services else None
    return AutoInputs(
        resolve_kwargs={"cells": max(1, worker_count), "profile": "auto"},
        heavy_cap=governor.heavy_cap if governor is not None else None,
        budget=runtime.member_budget(service),
        tick_s=tick_s,
        plan_fleet=Ceilings(*plan.fleet_ceilings) if plan.fleet_ceilings else None,
    )
