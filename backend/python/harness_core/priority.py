"""Priority classes (G-45): which runs go first for a claim slot, a model gateway slot and a vendor rate budget.

Node gives each run its class at admission (from the run kind; a child takes its parent's) and raises a lower-class
run a person answered to ``approval_resume``; a claim names it beside the run. The table is
``contracts/harness/vectors/priority-classes.json``, which Node's ``domain/priority-class.ts`` is held to as well.

When the class takes effect (WP-61, decided): a class raised while an attempt holds the run is not pushed into that
attempt. The worker reads the class once per claim, so the attempt's own model calls (a loop run's), its place in
the provider limit and whether it may be preempted stay at the claim's class; the run's next claim is served at the
raised class, ahead of the older runs below it (R11D-PRI-1, R11D-PRI-2). An engine attempt's calls through the Model
Gateway take the class from the run's model grant, which the gateway keeps for up to 60 s per attempt, so they may
rise at that grant's next read within the attempt (R11D-PRI-3). A preemptible attempt a person has just answered can
still be asked to yield; it then comes back first at its next claim.
"""

from __future__ import annotations

from typing import Final

from harness_core.contracts.generated.enums import PRIORITY_CLASSES, PriorityClass

# A person waits on these run kinds. A child is absent: it takes its parent's class, and a claim that names no class
# (an older Node) reads as interactive, as every run was served before classes.
_RUN_KIND_CLASSES: Final[dict[str, PriorityClass]] = {
    "interactive": "interactive",
    "authoring": "interactive",
    "command": "interactive",
    "scheduled": "background",
    "workflow_code": "background",
}
DEFAULT_CLASS: Final[PriorityClass] = "interactive"
# Work nobody is waiting on: it gives its slot back at a step boundary when a higher class needs one.
PREEMPTIBLE: Final[frozenset[PriorityClass]] = frozenset({"background", "batch"})


def priority_rank(priority_class: PriorityClass) -> int:
    """0 is served first."""
    return PRIORITY_CLASSES.index(priority_class)


def class_for_run_kind(run_kind: str | None) -> PriorityClass:
    return _RUN_KIND_CLASSES.get(run_kind or "", DEFAULT_CLASS)


def after_approval(priority_class: PriorityClass) -> PriorityClass:
    """A lower-class run a person just answered: they wait on it now."""
    return "approval_resume" if priority_rank(priority_class) > priority_rank("approval_resume") else priority_class


def parse_class(raw: object) -> PriorityClass | None:
    """A class as a claim or grant names it; anything else (absent, a later release's name) is ``None``."""
    return next((c for c in PRIORITY_CLASSES if c == raw), None)


def preemptible(priority_class: PriorityClass) -> bool:
    return priority_class in PREEMPTIBLE


class PreemptRequests:
    """Which running attempts were asked to give their slot back (G-45): the claimer asks, the run's kernel yields
    ``preempted`` at its next step boundary, never inside a model call or a tool effect."""

    def __init__(self) -> None:
        self._asked: set[str] = set()

    def request(self, run_id: str) -> None:
        self._asked.add(run_id)

    def requested(self, run_id: str) -> bool:
        return run_id in self._asked

    def clear(self, run_id: str) -> None:
        self._asked.discard(run_id)


__all__ = [
    "DEFAULT_CLASS",
    "PREEMPTIBLE",
    "PRIORITY_CLASSES",
    "PreemptRequests",
    "PriorityClass",
    "after_approval",
    "class_for_run_kind",
    "parse_class",
    "preemptible",
    "priority_rank",
]
