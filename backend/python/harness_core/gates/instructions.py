"""What a gated run is told about its gates. Words only: each gate is enforced where calls run, never by these."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from harness_core.gates.plan import PLAN_FIRST
from harness_core.gates.spec import dry_run, plan_approval

if TYPE_CHECKING:
    from harness_core.contracts.generated.run_spec import RunSpec

DRY_RUN_NOTE: Final = (
    "This run is a dry run. Reads work as usual, but nothing that would change, send, create or delete anything "
    "outside this run is done: such a call is recorded as what you would have done, and its result says so. Work "
    "through the task as you would for real, then say in your answer what you would have done and that none of it "
    "was actually done."
)


def gate_notes(spec: RunSpec) -> tuple[str, ...]:
    return (*((DRY_RUN_NOTE,) if dry_run(spec) else ()), *((PLAN_FIRST,) if plan_approval(spec) else ()))


def gated_instructions(instructions: str, spec: RunSpec) -> str:
    notes = gate_notes(spec)
    return "\n\n".join((instructions, *notes)) if notes else instructions


__all__ = ["DRY_RUN_NOTE", "gate_notes", "gated_instructions"]
