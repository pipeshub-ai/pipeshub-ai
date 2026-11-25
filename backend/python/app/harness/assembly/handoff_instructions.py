"""ADR-RUN-01 D5: the wording of every instruction DEEPROUTE gives the chat model, in one place.

Each is a ``HarnessInstruction`` built from typed values (why the run is handed off, what a failed engine kept), sent
in the harness's system channel for the one turn it is for (``harness_core.kernel.durable.harness_channel``), never
as prose in the person's turn: a long research run's model took the hand-off steer, then an injected user message, for the
person's words and copied it into the engine's task. The channel's frame says whose they are, so the texts carry no
"[PipesHub]" tag of their own.
"""

from __future__ import annotations

from typing import Final

from harness_core.engines.outcome.briefing import BRIEFING, ChildReport
from harness_core.engines.outcome.locales.en import MESSAGES as OUTCOME_WORDS
from harness_core.kernel.durable.harness_channel import HarnessInstruction

UPFRONT_KEY: Final = "deeproute.upfront"
ESCALATION_KEY: Final = "deeproute.escalation"
ENGINE_FAILED_KEY: Final = "deeproute.engine_failed"

_UPFRONT_WHY: dict[str, str] = {
    "research": "research across many sources",
    "analysis": "analysis across many items",
    "planning": "planning",
    "long_report": "a long write-up",
    "many_steps": "many steps",
}
_ESCALATION_WHY: dict[str, str] = {
    "many_steps": "it has taken many steps",
    "context": "its results no longer fit comfortably",
    "budget": "it has used much of the reply's budget",
}

# DEEPROUTE2: the engine's answer ends a routed turn, so a fact the person asked to have remembered was kept only if
# the engine called remember; in the live A/B it wrote the fact into a wiki page instead (0/6 recalled), uncited.
# Asked to call remember beside code_task, the model did in half the hand-offs; code_task's own ``remember`` list,
# written with the brief, keeps it (``code_task.REMEMBER_FIELD``).
REMEMBER_IN_HANDOFF = (
    "The engine's answer ends this turn: if the person asked you to remember anything, list each thing in "
    "code_task's remember (it is kept for them), and say in the goal that it is already remembered and needs no file. "
    "Do not call any other tool in this turn."
)
# D5: the engine is given the person's request as they wrote it (``task_spec``); the goal is what it needs beyond it.
UPFRONT_INSTRUCTION = (
    "This request needs {why}: more steps and more room than one chat reply. Hand it to the coding engine now: call "
    "code_task once. PipesHub gives the engine the person's request as they wrote it; in the goal, add every name, "
    "link, figure and constraint from this conversation the work needs, and every rule they set for how it is "
    "written (such as each figure linking the file it came from). The engine has this conversation's tools, "
    "including web search and fetch, knowledge and connectors, and works in the conversation's workspace. "
    + REMEMBER_IN_HANDOFF
)
ESCALATION_INSTRUCTION = (
    "This reply has grown past a chat turn ({why}). Hand the rest of the work to the coding engine now: call "
    "code_task once. PipesHub gives the engine the person's request as they wrote it; in the goal, add (1) every rule "
    "they set for how it is written, (2) what you found so far, each fact with its source link or [source](refN) "
    "exactly as you were given it, and (3) what is still open. The engine has this conversation's tools, including "
    "web search and fetch, and is given the tool calls you already made with their results, so it does not make them "
    "again. " + REMEMBER_IN_HANDOFF
)
# ADR-RUN-01 D1: what a parent is told when its engine child fails is the outcome briefing's (CHILDREF, WP-62, WP-56);
# its texts are read here only so a copy of one in a hand-off's notes is recognised.
ENGINE_FAILED_INSTRUCTION = OUTCOME_WORDS["briefing.failed.nothing"]
ENGINE_FAILED_WITH_WORK_INSTRUCTION = OUTCOME_WORDS["briefing.failed.unkept"]
ENGINE_FAILED_WITH_KEPT_WORK_INSTRUCTION = OUTCOME_WORDS["briefing.failed.kept"]
# The instructions' texts, for the task builder to recognise one copied into a hand-off's notes (D5).
ALL_TEXTS: Final = (
    UPFRONT_INSTRUCTION,
    ESCALATION_INSTRUCTION,
    ENGINE_FAILED_INSTRUCTION,
    ENGINE_FAILED_WITH_WORK_INSTRUCTION,
    ENGINE_FAILED_WITH_KEPT_WORK_INSTRUCTION,
)


def upfront(reason: str) -> HarnessInstruction:
    """The up-front hand-off for a request routed to the engine for ``reason``."""
    why = _UPFRONT_WHY.get(reason, _UPFRONT_WHY["many_steps"])
    return HarnessInstruction(UPFRONT_KEY, UPFRONT_INSTRUCTION.format(why=why))


def escalation(reason: str) -> HarnessInstruction:
    """The mid-run hand-off of a reply that crossed a threshold for ``reason``."""
    return HarnessInstruction(ESCALATION_KEY, ESCALATION_INSTRUCTION.format(why=_ESCALATION_WHY[reason]))


def engine_failed_briefed(report: ChildReport) -> HarnessInstruction:
    """A failed hand-off's instruction from the child's report, read once (``OutcomeBriefing``)."""
    return HarnessInstruction(ENGINE_FAILED_KEY, BRIEFING.failed_instruction(report))


def engine_failed(*, folder: str | None = None, revisions: int | None = None) -> HarnessInstruction:
    """A failed hand-off's briefing (``OutcomeBriefing``): with the folder its unfinished work was kept in, else the
    revisions its fork saved apart, else that it returned nothing."""
    return engine_failed_briefed(ChildReport(failed=True, kept_folder=folder or None, revisions=revisions or 0))


__all__ = [
    "ALL_TEXTS",
    "ENGINE_FAILED_INSTRUCTION",
    "ENGINE_FAILED_KEY",
    "ENGINE_FAILED_WITH_KEPT_WORK_INSTRUCTION",
    "ENGINE_FAILED_WITH_WORK_INSTRUCTION",
    "ESCALATION_INSTRUCTION",
    "ESCALATION_KEY",
    "REMEMBER_IN_HANDOFF",
    "UPFRONT_INSTRUCTION",
    "UPFRONT_KEY",
    "engine_failed",
    "engine_failed_briefed",
    "escalation",
    "upfront",
]
