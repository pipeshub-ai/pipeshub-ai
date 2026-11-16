"""The elected container controller: one process per container runs the adaptive part, on container-wide demand.

Any governed process can hold the role (an indexing cell, or the Docling, parsing, embedding or query
service, or a parse process of a roles-mode pod with no indexing process at all). The ledger elects
it (:meth:`MemberBudget.elect_controller`): a heartbeat the holder writes every tick, an epoch that
fences a holder that hung, and the state block it persists every tick, which a successor resumes
instead of warm-starting. This module is the loop's step; the maths it runs is the caller's
``compute`` (the governor's ``next_limits``), so nothing here imports the governor.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.capacity.budget_policy import Family

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from app.capacity.member_budget import MemberBudget

_LOG = logging.getLogger(__name__)
SATURATED_TICKS = 2
CPU_FAMILIES = tuple(f for f in Family if f is not Family.LLM)


@dataclass(frozen=True)
class Tick:
    role: str  # "controller" or "follower"
    epoch: int | None
    state: dict[str, Any] | None  # the persisted state: what a controller resumed from, or a follower reads
    demand: Mapping[str, tuple[float, int, int, int]] = field(default_factory=dict)
    stale: bool = False  # follower: the controller's last beat is older than two ticks


class ElectedController:
    """This process's side of the role. Call :meth:`tick` once per governor sample."""

    def __init__(self, budget: MemberBudget, tick_s: float = 15.0) -> None:
        self.budget = budget
        self.tick_s = max(0.001, tick_s)
        self.epoch: int | None = None
        self.saturation = SaturationTracker()

    @property
    def is_controller(self) -> bool:
        return self.epoch is not None

    def tick(self, compute: Callable[[dict[str, Any] | None, Mapping[str, tuple[float, int, int, int]]], dict[str, Any]],
             *, now_ms: int | None = None) -> Tick:
        """Elect or keep the role. The controller resumes the persisted state, computes the next one from
        container-wide demand and writes it with its epoch; a write the ledger fences ends the role."""
        epoch = self.budget.elect_controller(self.tick_s, now_ms)
        info = self.budget.controller()
        if epoch is None:
            self.epoch = None
            age_ms = (now_ms if now_ms is not None else _now_ms()) - info["beat_ms"]
            return Tick("follower", None, info["state"], stale=age_ms > 2 * self.tick_s * 1000)
        demand = self.budget.container_demand(max_age_ms=int(3 * self.tick_s * 1000))
        new_state = compute(info["state"], demand)
        if not self.budget.controller_beat(epoch, new_state, now_ms):
            _LOG.warning("capacity controller: epoch %s was fenced by a newer controller; standing down", epoch)
            self.epoch = None
            return Tick("follower", None, self.budget.controller()["state"])
        if epoch != self.epoch:
            _LOG.info("capacity controller: this process is the container controller (epoch %s, resumed=%s)",
                      epoch, info["state"] is not None)
        self.epoch = epoch
        return Tick("controller", epoch, new_state, demand)

    def resign(self) -> None:
        if self.epoch is not None:
            self.budget.resign_controller(self.epoch)
            self.epoch = None

    def load_signal(self, *, floors_over_capacity: bool = False) -> dict[str, Any] | None:
        """The ``budget`` block of the load signal. Only the controller publishes it, so the container is counted once."""
        if self.epoch is None:
            return None
        snap = self.budget.snapshot()
        held = snap["controller"]
        if held["epoch"] != self.epoch or held["pid"] != os.getpid():
            # Another process took the role (this one missed two ticks): it is not this holder's block to publish.
            return None
        return budget_signal(snap, self.saturation, floors_over_capacity=floors_over_capacity)


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


class SaturationTracker:
    """``saturated`` after ``SATURATED_TICKS`` ticks of a full critical reserve with critical waiters, or a full C with any
    waiters; it clears after as many clean ticks (hysteresis)."""

    def __init__(self, ticks: int = SATURATED_TICKS) -> None:
        self.ticks = ticks
        self._hot = 0
        self._cold = 0
        self.saturated = False

    def update(self, snap: Mapping[str, Any]) -> bool:
        waiting = {f: v["waiting"] for f, v in snap["families"].items()}
        critical = {Family.INSPECT.value, Family.QUERY_EMBED.value, Family.DOCLING.value, Family.PARSE.value,
                    Family.EMBED.value}
        reserve_full = snap["held_floor"] > 0 and snap["critical_used"] >= snap["held_floor"]
        critical_waiting = sum(waiting[f] for f in critical) > 0
        c_full = snap["used"] >= snap["cpu_tokens"] and sum(waiting.values()) > 0
        if (reserve_full and critical_waiting) or c_full:
            self._hot += 1
            self._cold = 0
        else:
            self._cold += 1
            self._hot = 0
        if self._hot >= self.ticks:
            self.saturated = True
        elif self._cold >= self.ticks:
            self.saturated = False
        return self.saturated


def budget_signal(snap: Mapping[str, Any], tracker: SaturationTracker, *, floors_over_capacity: bool = False) -> dict[str, Any]:
    """``snapshot()`` as the load signal's ``budget{...}``. The family keys are exactly the ``Family`` enum."""
    families = {f.value: {"used": snap["families"][f.value]["used"], "waiting": snap["families"][f.value]["waiting"]}
                for f in Family}
    return {
        "used": snap["used"],
        "cpuTokens": snap["cpu_tokens"],
        "heldFloor": snap["held_floor"],
        "saturated": tracker.update(snap),
        "floorsOverCapacity": floors_over_capacity,
        "families": families,
    }
