"""Turn-budget rules shared by every loop: the model's last-turn note, the degraded answer, the step footer. The
words a person reads about a cut-short run are the outcome presenter's (``harness_core.engines.outcome``)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from harness_core.engines.outcome.presenter import PRESENTER
from harness_core.kernel.core.tool_content import tool_message_content

if TYPE_CHECKING:
    from collections.abc import Iterable

# Below this many characters, the last assistant turn's text is treated as
# a stray fragment (a lone acknowledgement, a mid-thought sentence) rather
# than a real answer worth returning.
MIN_DEGRADED_OUTPUT_CHARS = 40

# Which limit cut a run's work short (``CutShortReason``, run.completed.truncatedBy).
CutShort = Literal["budget", "turns", "time", "stall", "provider", "engine_error", "secret_blocked"]

_LIMIT_REACHED: dict[CutShort, str] = {
    "turns": "the maximum number of steps for this request",
    "budget": "this reply's spending limit",
    "time": "this reply's working-time limit",
    "stall": "a point where your recent steps are not making progress (the same calls keep failing, return nothing, or repeat)",
    "provider": "a point where the AI model kept refusing requests",
    "engine_error": "a point where the coding engine stopped with an error",
    "secret_blocked": "a point where PipesHub blocked a request that looked like it contained a secret",
}
_STOPPED_BECAUSE: dict[CutShort, str] = {
    "turns": "it reached its step limit",
    "budget": "it reached its spending limit",
    "time": "it ran out of working time",
    "stall": "its recent steps kept repeating without making progress",
    "provider": "the AI model kept refusing requests for several minutes (it was busy or getting too many requests)",
    "engine_error": "the coding engine ran into an error",
    "secret_blocked": "PipesHub blocked a request that looked like it contained a secret (an API key or access token)",
}
_HOW_TO_CONTINUE: dict[CutShort, str] = {
    "turns": "they can send 'continue' to carry on",
    "budget": (
        "they can send 'continue' to carry on in a new reply, or ask an admin to raise the limit if it is "
        "the organization's"
    ),
    "time": "they can send 'continue' to carry on",
    "stall": "they can send 'continue', ideally with more detail or another approach, to carry on",
    "provider": (
        "they can try again in a few minutes, or ask their admin to check the model's limits, then send 'continue' "
        "to carry on"
    ),
    "engine_error": "they can send 'continue' to carry on",
    "secret_blocked": (
        "they can send 'continue' to carry on, and that if it happens again they should remove the secret from the "
        "files or messages the task reads"
    ),
}


def cut_short_note(reason: CutShort) -> str:
    """The answer-only final turn's request when ``reason``'s limit ran out (BUDGETEND). Sent once, on the latest
    tool result's loop footer (``add_loop_note``), that call's copy only, on both loops: a user-posed instruction
    after tool output trips provider content filters."""
    return (
        f"[loop: You have reached {_LIMIT_REACHED[reason]}. Do not call any more tools. Reply now with your final "
        "answer to the user's question, using only the information you have already gathered, and cite it as "
        "usual. In the user's language, say plainly that this reply stopped early because "
        f"{_STOPPED_BECAUSE[reason]}, name briefly what you did not get to, and say {_HOW_TO_CONTINUE[reason]}. "
        "If something could not be determined, say so briefly.]"
    )


# Sent once after the last allowed turn when that turn called tools (the model has not seen those results).
FINAL_ANSWER_NOTE = cut_short_note("turns")

def cut_short_fallback(reason: CutShort, tool_names: Iterable[str]) -> str:
    """The answer of a run whose limit ran out when no model call could write one (BUDGETEND), as the outcome
    presenter words it (ADR-RUN-01 D1): that it was cut off, what it had gathered by tool, and how to carry on."""
    return PRESENTER.no_answer(reason, tool_names)


def is_cut_short_fallback(text: str) -> bool:
    """Whether ``text`` is a no-model answer of a run that stopped before its answer was written, or one that owes
    its deliverable (WP-70): the presenter's own words, recognised by the presenter."""
    return PRESENTER.is_no_answer(text)


def degraded_answer(last_text: str) -> str | None:
    """The best-effort answer a run past its turn budget returns, or ``None``
    when its last text is too short to be one (the run then fails)."""
    text = last_text.strip()
    return text if len(text) >= MIN_DEGRADED_OUTPUT_CHARS else None


def next_stale_rounds(
    previous: int, results: Iterable[tuple[bool, Any]]
) -> int:
    """Turns in a row whose tool results carried nothing useful: reset by any
    result that is not an error and has content (``(is_error, content)``)."""
    useful = any(not is_error and bool(tool_message_content(content)) for is_error, content in results)
    return 0 if useful else previous + 1


def step_footer(turn_index: int, max_turns: int, stale_rounds: int) -> str:
    """Observable loop-control state appended to every tool result, so the
    model can make a reactive stop decision (``prompt_builder``'s operating rules)."""
    return f"\n\n[loop: step {turn_index + 1}/{max_turns}, stale_rounds={stale_rounds}]"


__all__ = [
    "FINAL_ANSWER_NOTE",
    "CutShort",
    "cut_short_fallback",
    "cut_short_note",
    "is_cut_short_fallback",
    "MIN_DEGRADED_OUTPUT_CHARS",
    "degraded_answer",
    "next_stale_rounds",
    "step_footer",
]
