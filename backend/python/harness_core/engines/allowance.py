"""What an engine is told about its allowance and its writing window, in words (WP-70 DEEPFINISH, ADR-RUN-01 D4).

In one research run, Codex gathered 166 pages and 29 searches in 9 minutes, then the profile's 50-step cap ended the
turn at USD 0.64 of its USD 5 budget, before it wrote a word of the report it was asked for. Three rules follow:

* **The money decides.** A metered run with a USD budget gets a step backstop scaled to that budget, never above
  what its profile allows. The stall detector (BU-06) still stops a loop that makes no progress.
* **The engine knows its allowance.** Its instructions say, in plain terms, how many steps, minutes and dollars the
  task has, when it starts writing at the latest, and that it will be told to stop gathering.
* **Writing is reserved.** When a turn comes within its writing reserve of any limit (steps, wall time, money), it
  enters its synthesis window: the engine is told (steered, where it can be) to stop gathering and write the
  deliverable from its notes, and from then on its gateway calls are refused with the same words.

The numbers (the allowance, the writing reserve, the phase plan) are the run budget's
(``harness_core.budget.run_budget``); this module only words them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from harness_core.budget.run_budget import SYNTHESIS_SHARE
from harness_core.kernel.durable.harness_channel import HarnessInstruction

if TYPE_CHECKING:
    from harness_core.budget.run_budget import Allowance, PhasePlan, SynthesisReason

# The harness instruction a turn's writing window is sent as (ADR-RUN-01 D5): its key names it in logs and tests.
WRITE_BY_KEY = "write_by"

_LIMIT_WORDS: dict[SynthesisReason, str] = {
    "steps": "its step allowance",
    "wall_time": "its working time",
    "budget": "its spending limit",
}


def allowance_note(allowance: Allowance, plan: PhasePlan | None = None) -> str:
    """The engine's instructions on its allowance, in plain terms, with the turn's time plan when it has one (D4:
    "start writing by 17:20 UTC"); "" when nothing bounds the turn."""
    parts: list[str] = []
    if allowance.steps is not None:
        parts.append(f"{allowance.steps} model steps")
    if allowance.wall_s is not None:
        parts.append(f"about {max(1, round(allowance.wall_s / 60))} minutes")
    if allowance.usd is not None:
        parts.append(f"USD {allowance.usd:.2f} of model use")
    if not parts:
        return ""
    share = round(SYNTHESIS_SHARE * 100)
    return (
        f"Your allowance for this task is {_listed(parts)}, whichever runs out first. Plan for it: gather what the "
        "task needs, keep notes as you go, write each finding into the file the task asks for as you make it (with "
        "its source, never just a placeholder), and leave room to write. When about "
        f"{share}% of any of these is left you will be told to stop gathering; from then on tool calls are refused, "
        "so write the deliverable from your notes and saved results, cite every source, and finish with your answer."
        + (f" {plan_words(plan)}" if plan is not None else "")
    )


def plan_words(plan: PhasePlan) -> str:
    """The turn's phase plan in plain terms: when writing starts at the latest and when the work must be done."""
    return (
        f"Time plan: gather until {_clock(plan.write_by_s)} at the latest, then write; your work must be finished "
        f"by {_clock(plan.stop_by_s)}."
    )


def write_by_instruction(reason: SynthesisReason, left: str, deliverable: str | None) -> HarnessInstruction:
    """The steer a turn's writing window sends (ADR-RUN-01 D5: a typed harness instruction, worded here once)."""
    return HarnessInstruction(WRITE_BY_KEY, synthesis_text(reason, left, deliverable))


def _clock(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s, tz=UTC).strftime("%H:%M UTC")


def synthesis_text(reason: SynthesisReason, left: str, deliverable: str | None) -> str:
    """What the engine is told when its turn enters the synthesis window (steered, and as each refused call's
    result): stop gathering, write the deliverable now."""
    what = (
        f"Write the deliverable, out/{deliverable}, in full now"
        if deliverable
        else "Write your answer now"
    )
    return (
        f"Stop gathering: this task is close to {_LIMIT_WORDS[reason]} ({left} left), and tool calls are refused from "
        f"here. {what}, from your notes and the results you already saved. Cite each finding with its source "
        "(its URL, or the [source](refN) a tool gave), mark what you could not verify, then end your turn with your "
        "answer" + (", naming the file." if deliverable else ".")
    )


def left_words(reason: SynthesisReason, allowance: Allowance, *, steps: int, elapsed_s: float, usd_left: float | None) -> str:
    """What is left of the limit that opened the window, in words."""
    if reason == "steps" and allowance.steps is not None:
        return f"{max(allowance.steps - steps, 0)} steps"
    if reason == "wall_time" and allowance.wall_s is not None:
        return f"about {max(1, round((allowance.wall_s - elapsed_s) / 60))} minutes"
    if usd_left is not None:
        return f"USD {max(usd_left, 0):.2f}"
    return "little"


def _listed(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


__all__ = [
    "WRITE_BY_KEY",
    "allowance_note",
    "left_words",
    "plan_words",
    "synthesis_text",
    "write_by_instruction",
]
