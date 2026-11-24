"""This process's handle on the container's member budget, for the services that use it.

``None`` unless ``PIPESHUB_CAPACITY_PROFILE=auto``: with the flag off nothing here runs and no ledger
file is created. The budget is built once per process from the plan the container started with.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING

from app.capacity.budget_policy import PRECEDENCE, Family, Floors
from app.capacity.knobs import capacity_profile

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.capacity.member_budget import MemberBudget
    from app.capacity.plan import CapacityPlan

_LOG = logging.getLogger(__name__)
_LOCK = threading.Lock()
_STATE: dict[str, object] = {}


def floors_from_plan(plan: CapacityPlan) -> Floors:
    member, widths = plan.member_budget, plan.widths
    assert member is not None and widths is not None
    by = {f: int(member.floors.get(f.value, 0)) for f in PRECEDENCE}
    desired = {f: int(member.desired_floors.get(f.value, by[f])) for f in PRECEDENCE}
    return Floors(by, desired, max(1, widths.docling_workers_warm), max(1, widths.docling_workers_max),
                  tuple(w for w in plan.warnings if "floor" in w))


def plan_for_process(env: Mapping[str, str] | None = None) -> CapacityPlan | None:
    """The ``auto`` plan, or ``None`` (flag off, or no plan could be built)."""
    from app.capacity.policy import plan_capacity
    from app.capacity.sources import resolve_facts

    source = os.environ if env is None else env
    if capacity_profile(source) != "auto":
        return None
    try:
        plan = plan_capacity(resolve_facts(source, probe_gpu=False), source)
    except Exception:
        _LOG.exception("capacity: the auto plan could not be built; this process runs without a member budget")
        return None
    return plan if plan.member_budget is not None and plan.widths is not None else None


def member_budget(service: str | None = None) -> MemberBudget | None:
    """The container's ledger as this process sees it, built once; ``None`` when ``auto`` is off or the ledger cannot be opened."""
    if capacity_profile() != "auto":
        return None
    with _LOCK:
        if "budget" in _STATE:
            return _STATE["budget"]  # type: ignore[return-value]
        budget: MemberBudget | None = None
        plan = plan_for_process()
        if plan is not None:
            from app.capacity.member_budget import MemberBudget

            try:
                budget = MemberBudget(
                    cpu_tokens=plan.member_budget.cpu_tokens,  # type: ignore[union-attr]
                    mem_bytes=plan.member_budget.mem_bytes,  # type: ignore[union-attr]
                    floors=floors_from_plan(plan),
                    llm_cap=plan.widths.llm_permits,  # type: ignore[union-attr]
                    service=service or os.environ.get("PIPESHUB_SERVICE", ""),
                )
            except Exception:
                _LOG.exception("capacity: the member budget ledger could not be opened; running without it")
        _STATE["budget"] = budget
        _STATE["plan"] = plan
        return budget


def current_plan() -> CapacityPlan | None:
    member_budget()
    return _STATE.get("plan")  # type: ignore[return-value]


def reset_for_tests() -> None:
    with _LOCK:
        budget = _STATE.pop("budget", None)
        _STATE.pop("plan", None)
    if budget is not None:
        budget.close()  # type: ignore[attr-defined]


__all__ = ["Family", "current_plan", "floors_from_plan", "member_budget", "plan_for_process", "reset_for_tests"]
