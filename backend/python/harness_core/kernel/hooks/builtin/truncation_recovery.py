# pyright: basic
from __future__ import annotations

import logging
from collections.abc import Callable

from harness_core.kernel.core.finish_moves import TASK_COMPLETE, can_call
from harness_core.kernel.core.types import ToolResult as CoreToolResult
from harness_core.kernel.core.types import UserMessage
from harness_core.kernel.hooks.middleware.context import ModelResponseContext

"""POST_MODEL middleware: builds the recovery content a truncated
(max-output-tokens-capped) LLM response needs, so `Agent.run()` never
executes tool calls parsed from incomplete arguments — see the `truncated`
branch in `agent/__init__.py`'s turn loop.

Pure content construction: whether to actually short-circuit the rest of the
turn (skip tool execution, `continue` to the next turn) stays owned by the
loop, since that is genuine turn-loop control flow, not a policy a
middleware can express by itself.
"""

__all__ = ["default_truncation_recovery", "truncated_call_note", "truncation_note"]

logger = logging.getLogger(__name__)

_TOOL_CALL_TRUNCATION_NOTE = (
    "[Tool call not executed: your response was cut off at the "
    "maximum output-token limit, so this call's arguments were "
    "incomplete. Retry with a shorter response — for long final "
    "outputs, emit the content across multiple turns or trim it{finish}.]"
)

_TOOL_CALL_TRUNCATION_ESCALATED = (
    "[Tool call not executed: your response was cut off at the "
    "maximum output-token limit AGAIN. You have already been "
    "truncated multiple times in a row — you MUST change your "
    "approach:\n"
    "{split}"
    "- Reduce the amount of content in a single tool call\n"
    "- Write a shorter, simpler version\n"
    "Do NOT retry the same long output — it will be truncated again.]"
)

_RUN_CODE = "run_code"
_SPLIT_CODE_LINE = (
    f"- Split your code into SMALLER pieces across multiple {_RUN_CODE} "
    "calls (e.g. build part 1, then part 2)\n"
)

_TEXT_ONLY_CONTINUATION_NOTE = (
    "[System: your previous response was cut off at the maximum "
    "output-token limit and is incomplete. Continue from where "
    "you stopped, keeping the remainder concise.]"
)

# P4.17: a reasoning model can spend the whole limit thinking and show nothing. There is nothing to continue, and a
# fresh attempt rarely runs away again, so the model is asked for the answer itself.
_REASONING_EXHAUSTED_NOTE = (
    "[System: your previous response used the whole output limit on reasoning and produced no answer. "
    "Do not reason at length again: answer now, directly and concisely, from what you already have, "
    "or make the single tool call you need next.]"
)

# P4.17: the harness stopped a response that kept repeating itself; its text ends just before the loop.
_REPETITION_NOTE = (
    "[System: your previous response started repeating the same text over and over, so it was stopped "
    "there. Continue from where it stopped without repeating anything already written, and finish concisely.]"
)

_TOOL_CALL_REPETITION_NOTE = (
    "[Tool call not executed: its arguments started repeating the same content over and over, so the "
    "response was stopped. Call the tool again with arguments that do not repeat; for long content, "
    "write it in smaller parts.]"
)

_CONSECUTIVE_TRUNCATION_THRESHOLD = 2


def truncated_call_note(
    consecutive: int,
    cut: str | None = None,
    *,
    can_call_tool: Callable[[str], bool] = lambda _name: False,
) -> str:
    """The error result a truncated turn's tool call gets instead of running (both loops).
    It names `task_complete` and `run_code` only when the run can call them."""
    if cut == "repetition":
        return _TOOL_CALL_REPETITION_NOTE
    if consecutive > _CONSECUTIVE_TRUNCATION_THRESHOLD:
        return _TOOL_CALL_TRUNCATION_ESCALATED.format(
            split=_SPLIT_CODE_LINE if can_call_tool(_RUN_CODE) else "",
        )
    finish = f" before calling {TASK_COMPLETE}" if can_call_tool(TASK_COMPLETE) else ""
    return _TOOL_CALL_TRUNCATION_NOTE.format(finish=finish)


def truncation_note(text: str, cut: str | None = None) -> str:
    """What a truncated turn with no tool call tells the model next: continue it, or, when nothing visible came
    out (a reasoning model spent the limit thinking), answer now."""
    if cut == "repetition":
        return _REPETITION_NOTE
    if not text.strip():
        return _REASONING_EXHAUSTED_NOTE
    return _TEXT_ONLY_CONTINUATION_NOTE


def default_truncation_recovery():
    consecutive_truncations = 0

    async def _middleware(ctx: ModelResponseContext, next_fn) -> None:
        nonlocal consecutive_truncations
        if getattr(ctx.response, "truncated", False):
            consecutive_truncations += 1
            cut = getattr(ctx.response, "cut", None)
            if ctx.tool_calls:
                note = truncated_call_note(
                    consecutive_truncations, cut, can_call_tool=lambda name: can_call(ctx.scope, name),
                )
                ctx.recovery_tool_results = [
                    CoreToolResult(tool_call_id=c.id, name=c.name, content=note, is_error=True)
                    for c in ctx.tool_calls
                ]
                outcome = f"{len(ctx.tool_calls)} tool call(s) not run"
            else:
                text = getattr(ctx.response, "text", "") or ""
                ctx.recovery_message = UserMessage(content=truncation_note(text, cut), injected=True)
                outcome = "asked to continue"
            logger.info(
                "truncation_recovery: reply cut off at the output-token limit, %s (consecutive=%d, turn=%d)",
                outcome, consecutive_truncations, ctx.turn_index,
            )
        else:
            consecutive_truncations = 0
        await next_fn()

    return _middleware
